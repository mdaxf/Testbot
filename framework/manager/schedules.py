"""Test schedules: which tests run, in which order, and when. Plus session plans (ordered lists of suite files that
can share one logged-in browser).

A schedule is a plain JSON file in the schedules folder. Running one (`run_schedule`) is a function that starts
`testbot.exe` for each item in order -- the UI's "Run now" and the background scheduler both call it, so neither
needs the other to be running."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from framework import emailer, logs
from framework import tlsconfig
from framework.manager.workspace import Workspace, WorkspaceError, check_id

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TRIGGER_TYPES = ["once", "interval", "daily", "weekly", "monthly", "manual"]
HISTORY_LIMIT = 60
MISSED_GRACE = timedelta(minutes=10)   # a run missed by more than this (scheduler was off) is skipped, not replayed
slog = logs.get("scheduler")
CANCEL_GRACE_S = 10                    # after "cancel", how long a test run may take to stop before it is killed


# ------------------------------------------------------------------ storage

def _state_dir(ws: Workspace) -> Path:
    d = ws.dir("schedules") / ".state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _schedule_file(ws: Workspace, sid: str) -> Path:
    return ws.dir("schedules") / f"{check_id(sid, 'schedule id')}.json"


def _state_file(ws: Workspace, sid: str) -> Path:
    return _state_dir(ws) / f"{check_id(sid, 'schedule id')}.json"


def _write_json_atomic(path: Path, data: Any) -> None:
    """Write to a temporary file next to `path`, then os.replace it: a reader never sees half a file (same pattern as
    revisions._write). The temporary name is unique per process/thread, so two writers never share it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:                     # Windows: the target is open in another process for a moment
            if attempt == 9:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def list_schedules(ws: Workspace) -> list[dict[str, Any]]:
    base = ws.dir("schedules")
    out = []
    if base.exists():
        for path in sorted(base.glob("*.json")):
            try:
                sched = json.loads(path.read_text(encoding="utf-8-sig"))
                sched.setdefault("id", path.stem)
                out.append(sched)
            except ValueError:
                out.append({"id": path.stem, "name": path.stem, "enabled": False, "broken": True, "items": [], "trigger": {"type": "manual"}})
    return out


def load_schedule(ws: Workspace, sid: str) -> dict[str, Any]:
    path = _schedule_file(ws, sid)
    if not path.is_file():
        raise WorkspaceError(f"schedule '{sid}' does not exist")
    sched = json.loads(path.read_text(encoding="utf-8-sig"))
    sched.setdefault("id", sid)
    return sched


def save_schedule(ws: Workspace, sched: dict[str, Any]) -> dict[str, Any]:
    sid = check_id(sched.get("id", ""), "schedule id")
    problems = check_schedule(ws, sched)
    if any(p["level"] == "error" for p in problems):
        raise ValueError("; ".join(p["message"] for p in problems if p["level"] == "error"))
    _write_json_atomic(_schedule_file(ws, sid), sched)
    return sched


def delete_schedule(ws: Workspace, sid: str) -> None:
    path = _schedule_file(ws, sid)
    if path.is_file():
        path.unlink()
    _state_file(ws, sid).unlink(missing_ok=True)


def check_schedule(ws: Workspace, sched: dict[str, Any]) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    trig = sched.get("trigger") or {}
    ttype = trig.get("type")
    if ttype not in TRIGGER_TYPES:
        problems.append({"level": "error", "message": f"trigger type must be one of {TRIGGER_TYPES}"})
    if ttype in ("daily", "weekly", "monthly") and not re.match(r"^\d{1,2}:\d{2}$", str(trig.get("time", ""))):
        problems.append({"level": "error", "message": "trigger time must be HH:MM"})
    if ttype == "weekly" and not [d for d in trig.get("days", []) if d in DAYS]:
        problems.append({"level": "error", "message": "weekly trigger needs at least one day"})
    if ttype == "interval" and not (float(trig.get("every", 0) or 0) > 0 and trig.get("unit", "minutes") in ("minutes", "hours")):
        problems.append({"level": "error", "message": "interval trigger needs 'every' > 0 and unit minutes/hours"})
    if ttype == "once":
        try:
            datetime.fromisoformat(str(trig.get("at")))
        except ValueError:
            problems.append({"level": "error", "message": "one-time trigger needs a valid date/time"})
    if not sched.get("items"):
        problems.append({"level": "warning", "message": "the schedule has no tests to run"})
    for i, item in enumerate(sched.get("items", []), start=1):
        if item.get("type") not in ("suite", "session", "group"):
            problems.append({"level": "error", "message": f"item {i}: type must be suite, session or group"})
            continue
        base = "test_cases"
        if item["type"] == "group":
            if not item.get("path"):
                problems.append({"level": "error", "message": f"item {i}: choose a test group"})
            elif not (ws.dir("groups") / f"{item['path']}.json").exists():
                problems.append({"level": "warning", "message": f"item {i}: the test group '{item['path']}' does not exist (yet)"})
            if (item.get("mode") or "all") not in ("all", "remaining"):
                problems.append({"level": "error", "message": f"item {i}: mode must be all or remaining"})
            if item.get("cases") or item.get("tags"):
                problems.append({"level": "error", "message": f"item {i}: a test group item has no cases/tags of its own"})
            continue
        if not item.get("path"):
            problems.append({"level": "error", "message": f"item {i}: choose a file"})
        elif not ws.safe_path(base, item["path"]).exists():
            problems.append({"level": "warning", "message": f"item {i}: '{item['path']}' does not exist (yet)"})
        for key in ("cases", "tags"):
            vals = item.get(key)
            if vals and (item["type"] != "suite" or not isinstance(vals, list) or not all(isinstance(c, str) and c.strip() for c in vals)):
                problems.append({"level": "error", "message": f"item {i}: '{key}' is a list of {'test case ids' if key == 'cases' else 'tags'} and only applies to a test file (a session lists its cases in the session itself)"})
    return problems


# ------------------------------------------------------------------ when does it run next

def _at(day: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


def next_fire(trigger: dict[str, Any], after: datetime, *, anchor: Optional[datetime] = None) -> Optional[datetime]:
    """The first moment strictly after `after` when `trigger` fires. `anchor` = when an interval schedule last ran
    (or was first seen). None = never again (manual / one-time in the past)."""
    ttype = trigger.get("type")
    if ttype == "once":
        at = datetime.fromisoformat(trigger["at"])
        return at if at > after else None
    if ttype == "interval":
        step = timedelta(**{trigger.get("unit", "minutes"): float(trigger["every"])})
        return (anchor or after) + step
    if ttype == "daily":
        cand = _at(after, trigger["time"])
        return cand if cand > after else cand + timedelta(days=1)
    if ttype == "weekly":
        wanted = {DAYS.index(d) for d in trigger.get("days", []) if d in DAYS}
        for add in range(0, 8):
            cand = _at(after + timedelta(days=add), trigger["time"])
            if cand > after and cand.weekday() in wanted:
                return cand
        return None
    if ttype == "monthly":
        day = int(trigger.get("day", 1))
        for add in range(0, 63):
            base = after + timedelta(days=add)
            last = ((base.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day
            if base.day == min(day, last):
                cand = _at(base, trigger["time"])
                if cand > after:
                    return cand
        return None
    return None


def read_state(ws: Workspace, sid: str) -> dict[str, Any]:
    p = _state_file(ws, sid)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError as exc:
            slog.warning("schedule %s: the state file %s is not valid JSON (%s); starting with an empty history", sid, p, exc)
    return {"history": []}


def write_state(ws: Workspace, sid: str, state: dict[str, Any]) -> None:
    state["history"] = state.get("history", [])[-HISTORY_LIMIT:]
    _write_json_atomic(_state_file(ws, sid), state)


def schedule_status(ws: Workspace, sched: dict[str, Any], now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now()
    state = read_state(ws, sched["id"])
    last = state["history"][-1] if state.get("history") else None
    anchor = datetime.fromisoformat(state["anchor"]) if state.get("anchor") else None
    nxt = None
    if sched.get("enabled", True) and sched.get("trigger", {}).get("type") != "manual":
        try:
            nxt = next_fire(sched["trigger"], now, anchor=anchor)
        except (KeyError, ValueError):
            nxt = None
    return {"last_run": last, "next_run": nxt.isoformat(timespec="minutes") if nxt else None, "running": state.get("running", False)}


# ------------------------------------------------------------------ running

def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _popen_kwargs() -> dict[str, Any]:
    """Start the runner in its own process group, so cancel can stop it together with the browser it started."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _kill_tree(proc: subprocess.Popen, grace_s: float = CANCEL_GRACE_S) -> None:
    """Stop `proc` and everything it started: politely first, then by force after `grace_s`."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
        try:
            proc.wait(timeout=grace_s)
            return
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=grace_s)
            return
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_child(cmd: list[str], *, cancel: threading.Event, timeout_s: Optional[float] = None, poll_s: float = 1.0,
              grace_s: float = CANCEL_GRACE_S, **popen_kwargs: Any) -> tuple[int, str]:
    """Run `cmd` until it exits, `cancel` is set, or `timeout_s` passes. Returns (exit code, "" | "cancelled" | "timeout").
    A cancelled or timed-out child is stopped together with its children (see _kill_tree)."""
    proc = subprocess.Popen(cmd, **_popen_kwargs(), **popen_kwargs)
    deadline = time.monotonic() + timeout_s if timeout_s else None
    try:
        while True:
            try:
                return proc.wait(timeout=poll_s), ""
            except subprocess.TimeoutExpired:
                pass
            if cancel.is_set():
                _kill_tree(proc, grace_s)
                return -15, "cancelled"
            if deadline is not None and time.monotonic() > deadline:
                _kill_tree(proc, grace_s)
                return -9, "timeout"
    finally:
        if proc.poll() is None:          # an unexpected error above: never leave the child running
            _kill_tree(proc, grace_s)


_running: dict[str, threading.Event] = {}
_running_lock = threading.Lock()


def item_command(ws: Workspace, item: dict[str, Any], report_dir: Path) -> list[str]:
    runner = ws.runner_argv()
    if runner is None:
        raise WorkspaceError("testbot.exe was not found. Set 'Runner command' in Settings.")
    opts = item.get("options") or {}
    if item["type"] == "group":          # a test group runs as a session built from its cases (all of them, or only those not passed yet)
        from framework.manager import groups

        plan = groups.run_plan(ws, item["path"], item.get("mode") or "all")
        path = str(ws.safe_path("test_cases", plan["path"]))
        cmd = runner + ["session", "--plan", path]
    else:
        path = str(ws.safe_path("test_cases", item["path"]))
        cmd = runner + (["session", "--plan", path] if item["type"] == "session" else ["suite", "--suite", path])
    if item["type"] == "suite":
        for case_id in item.get("cases") or []:      # only these test cases, in this order
            cmd += ["--case", str(case_id)]
        for tag in item.get("tags") or []:           # the cases that carry any of these tags
            cmd += ["--tag", str(tag)]
    cmd += ["--report-dir", str(report_dir)]
    if item.get("env") and item["type"] == "suite":
        cmd += ["--env", item["env"]]
    if opts.get("headed"):
        cmd.append("--headed")
    for flag, key in (("--workers", "workers"), ("--iterations", "iterations"), ("--duration", "duration"), ("--ramp-up", "ramp_up")):
        if opts.get(key) not in (None, "", 0):
            cmd += [flag, str(opts[key])]
    if opts.get("screenshots"):
        cmd += ["--screenshots", opts["screenshots"]]
    if opts.get("confirm_load"):
        cmd.append("--confirm-load")
    if opts.get("mode"):
        cmd += ["--mode", opts["mode"]]
    return cmd


def agent_env(agent: dict[str, Any]) -> dict[str, str]:
    """The workspace's agent defaults as TESTBOT_* environment variables for the runner (only what is set)."""
    mapping = {"mode": "TESTBOT_MODE", "provider": "TESTBOT_AGENT_PROVIDER", "model": "TESTBOT_AGENT_MODEL", "vision": "TESTBOT_AGENT_VISION",
               "max_steps": "TESTBOT_AGENT_MAX_STEPS", "profile": "TESTBOT_AGENT_PROFILE"}
    env = {var: str(agent[key]) for key, var in mapping.items() if agent.get(key) not in (None, "")}
    if agent.get("allow_destructive"):
        env["TESTBOT_AGENT_ALLOW_DESTRUCTIVE"] = "1"
    if agent.get("learn"):
        env["TESTBOT_AGENT_LEARN"] = "1"
    return env


def run_schedule(ws: Workspace, sched: dict[str, Any], trigger: str = "manual",
                 progress: Optional[Callable[[dict[str, Any]], None]] = None, cancel: Optional[threading.Event] = None) -> dict[str, Any]:
    """Run every item of the schedule in order; returns (and stores) a run record."""
    sid = sched["id"]
    prior = read_state(ws, sid)
    if prior.get("running") and pid_alive(prior.get("running_pid")) and prior.get("running_pid") != os.getpid():
        return {"status": "skipped", "message": "already running in another process", "schedule": sid}
    with _running_lock:
        if sid in _running:
            return {"status": "skipped", "message": "already running", "schedule": sid}
        _running[sid] = cancel or threading.Event()
    record: dict[str, Any] = {"schedule": sid, "trigger": trigger, "items": [], "status": "running"}
    overall = "pass"
    log = None
    state_written = False
    try:                                  # everything after the registration is inside try/finally, so it is always removed
        started = datetime.now()
        slog.info("schedule %s started (%s), %d item(s)", sid, trigger, len(sched.get("items", [])))
        run_id = started.strftime("%Y%m%d-%H%M%S")
        run_dir = ws.dir("results") / (sched.get("results_subdir") or sid) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        record.update(run_id=run_id, started=started.isoformat(timespec="seconds"), dir=ws.rel("results", run_dir))
        state = read_state(ws, sid)
        state["running"] = True
        state["running_pid"] = os.getpid()
        write_state(ws, sid, state)
        state_written = True
        if progress:
            progress(record)
        log = (run_dir / "run.log").open("w", encoding="utf-8")
        stop = False
        for n, item in enumerate(sched.get("items", []), start=1):
            entry: dict[str, Any] = {"n": n, "type": item.get("type"), "path": item.get("path")}
            if stop or (_running[sid].is_set()):
                entry.update(status="skipped", exit_code=None)
                record["items"].append(entry)
                continue
            item_dir = run_dir / f"{n:02d}-{Path(item['path']).stem}"
            t0 = time.monotonic()
            try:
                cmd = item_command(ws, item, item_dir)
                log.write(f"\n=== item {n}: {' '.join(cmd)}\n")
                log.flush()
                child_env = {**os.environ, **tlsconfig.env_for_child(tlsconfig.TlsSettings.from_dict(ws.data.get("ai", {}))), **agent_env(ws.data.get("agent", {})),
                             **logs.env_for_child((ws.data.get("logging") or {}).get("level")),
                             **emailer.env_for_child(ws.data.get("email"))}
                code, why = run_child(cmd, cancel=_running[sid], timeout_s=item.get("timeout_s") or None,
                                      cwd=str(ws.root), stdout=log, stderr=subprocess.STDOUT, env=child_env)
                if why == "timeout":
                    log.write("\n(timed out)\n")
                elif why == "cancelled":
                    log.write("\n(cancelled)\n")
            except Exception as exc:  # noqa: BLE001 - reported in the record, never crashes the scheduler
                code = -1
                log.write(f"\n(could not start: {exc})\n")
            entry.update(status="pass" if code == 0 else "fail", exit_code=code, seconds=round(time.monotonic() - t0, 1),
                         dir=ws.rel("results", item_dir) if item_dir.exists() else None)
            record["items"].append(entry)
            slog.log(20 if code == 0 else 30, "schedule %s item %d (%s): %s, exit code %s, %.1f s", sid, n, item.get("path"), entry["status"], code, entry["seconds"])
            if code != 0:
                overall = "fail"
                if item.get("stop_on_failure") or sched.get("stop_on_failure"):
                    stop = True
            if progress:
                progress(record)
    except Exception as exc:  # noqa: BLE001 - set-up failed (disk full, permissions, bad results_subdir): a failed record, never a stuck schedule
        overall = "error"
        record["message"] = f"{type(exc).__name__}: {exc}"
        slog.error("schedule %s could not run: %s", sid, record["message"])
    finally:
        try:
            if log is not None:
                log.close()
            finished = datetime.now()
            record.update(status=overall if not _running[sid].is_set() else "cancelled", finished=finished.isoformat(timespec="seconds"))
            if state_written or "run_id" in record:
                state = read_state(ws, sid)
                state.update(running=False, anchor=finished.isoformat(timespec="seconds"))
                state.setdefault("history", []).append(record)
                write_state(ws, sid, state)
        except Exception as exc:  # noqa: BLE001
            slog.error("schedule %s: could not record the run: %s", sid, exc)
        finally:
            with _running_lock:
                _running.pop(sid, None)
    return record


def cancel_schedule(sid: str) -> bool:
    with _running_lock:
        ev = _running.get(sid)
    if ev is None:
        return False
    ev.set()
    return True


def due_schedules(ws: Workspace, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Schedules whose fire time has arrived (within the grace period) and that are not already running."""
    now = now or datetime.now()
    due = []
    for sched in list_schedules(ws):
        if sched.get("broken") or not sched.get("enabled", True) or sched.get("trigger", {}).get("type") in (None, "manual"):
            continue
        state = read_state(ws, sched["id"])
        if state.get("running") and sched["id"] in _running:
            continue
        # the slot we are working towards, remembered so a poll every few seconds cannot fire it twice
        if not state.get("anchor"):
            state["anchor"] = now.isoformat(timespec="seconds")
            write_state(ws, sched["id"], state)
        anchor = datetime.fromisoformat(state["anchor"])
        last_slot = datetime.fromisoformat(state["last_slot"]) if state.get("last_slot") else anchor
        try:
            slot = next_fire(sched["trigger"], last_slot, anchor=max(anchor, last_slot))  # intervals count from the later of the two
        except (KeyError, ValueError):
            continue
        if slot is None or slot > now:
            continue
        state["last_slot"] = slot.isoformat(timespec="seconds")
        write_state(ws, sched["id"], state)
        if now - slot <= MISSED_GRACE:
            due.append(sched)
        # else: missed while the scheduler was off -- skip it and move on to the next slot
    return due


# ------------------------------------------------------------------ session plans (ordered suite files)

def _plans_dir(ws: Workspace) -> Path:
    return ws.dir("test_cases") / "sessions"


def list_sessions(ws: Workspace) -> list[dict[str, Any]]:
    d = _plans_dir(ws)
    out = []
    if d.exists():
        for p in sorted(list(d.glob("*.yaml")) + list(d.glob("*.yml"))):
            try:
                plan = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                out.append({"path": ws.rel("test_cases", p), "session_id": plan.get("session_id"), "session_name": plan.get("session_name"),
                            "files": len(plan.get("files", []))})
            except yaml.YAMLError:
                out.append({"path": ws.rel("test_cases", p), "session_id": p.stem, "session_name": "(unreadable)", "files": 0})
    return out


def read_session(ws: Workspace, rel: str) -> dict[str, Any]:
    p = ws.safe_path("test_cases", rel)
    plan = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    root = ws.root
    for f in plan.get("files", []):  # show paths relative to the test-cases folder in the UI
        fp = Path(f.get("path", ""))
        full = fp if fp.is_absolute() else (root / fp)
        try:
            f["path"] = full.resolve().relative_to(ws.dir("test_cases")).as_posix()
        except ValueError:
            pass
    return plan


def save_session(ws: Workspace, rel: str, plan: dict[str, Any]) -> str:
    if not rel.lower().endswith((".yaml", ".yml")):
        rel += ".yaml"
    if not plan.get("session_id"):
        raise ValueError("a session needs a session_id")
    plan = json.loads(json.dumps(plan))
    for f in plan.get("files", []):  # the runner resolves file paths from the workspace folder
        full = ws.safe_path("test_cases", f["path"])
        try:
            f["path"] = full.relative_to(ws.root).as_posix()
        except ValueError:
            f["path"] = str(full)
    p = ws.safe_path("test_cases", "sessions/" + Path(rel).name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return ws.rel("test_cases", p)


def delete_session(ws: Workspace, rel: str) -> None:
    ws.safe_path("test_cases", rel).unlink(missing_ok=True)
