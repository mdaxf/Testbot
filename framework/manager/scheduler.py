"""The background scheduler: watches the schedule files and starts the tests when they are due.

It runs as an ordinary background program (not tied to the UI): start it by hand, or let Windows start it for you at
logon / at startup with `scheduler install`. The UI only edits the schedule files and shows this program's heartbeat."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from framework.manager import schedules
from framework.manager.workspace import Workspace, app_dir

TASK_NAME = "testbot-scheduler"
POLL_SECONDS = 15


def _heartbeat_file(ws: Workspace) -> Path:
    return schedules._state_dir(ws) / "scheduler.json"


def heartbeat(ws: Workspace) -> dict[str, Any]:
    """Is a scheduler running against this workspace? (read by the UI)"""
    p = _heartbeat_file(ws)
    if not p.exists():
        return {"running": False}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {"running": False}
    fresh = (time.time() - data.get("beat", 0)) < POLL_SECONDS * 3
    return {"running": bool(fresh and schedules.pid_alive(data.get("pid"))), "pid": data.get("pid"),
            "started": data.get("started"), "last_beat": datetime.fromtimestamp(data.get("beat", 0)).isoformat(timespec="seconds")}


def _write_heartbeat(ws: Workspace, started: str) -> None:
    _heartbeat_file(ws).write_text(json.dumps({"pid": os.getpid(), "started": started, "beat": time.time()}), encoding="utf-8")


def run_due_once(ws: Workspace, log=print) -> int:
    """Start every schedule that is due (each in its own thread) and wait for them. Returns how many were started."""
    due = schedules.due_schedules(ws)
    threads = []
    for sched in due:
        log(f"[{datetime.now():%H:%M:%S}] starting schedule '{sched['id']}'")
        t = threading.Thread(target=lambda s=sched: schedules.run_schedule(ws, s, trigger="scheduled"), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return len(due)


def run_loop(ws: Workspace, stop: Optional[threading.Event] = None, log=print) -> None:
    stop = stop or threading.Event()
    started = datetime.now().isoformat(timespec="seconds")
    # a previous scheduler that died mid-run leaves 'running' flags behind: clear the ones whose process is gone
    for s in schedules.list_schedules(ws):
        st = schedules.read_state(ws, s["id"])
        if st.get("running") and not schedules.pid_alive(st.get("running_pid")):
            st["running"] = False
            schedules.write_state(ws, s["id"], st)
    log(f"testbot scheduler started for {ws.file} (checking every {POLL_SECONDS}s). Press Ctrl+C to stop.")
    schedules.slog.info("scheduler started for %s (checking every %ds)", ws.file, POLL_SECONDS)
    active: list[threading.Thread] = []
    while not stop.is_set():
        _write_heartbeat(ws, started)
        try:
            for sched in schedules.due_schedules(ws):
                log(f"[{datetime.now():%H:%M:%S}] starting schedule '{sched['id']}'")
                t = threading.Thread(target=lambda s=sched: schedules.run_schedule(ws, s, trigger="scheduled"), daemon=True)
                t.start()
                active.append(t)
            active = [t for t in active if t.is_alive()]
        except Exception as exc:  # noqa: BLE001 - one bad schedule file must not stop the service
            log(f"scheduler error: {exc}")
            schedules.slog.error("scheduler error: %s", exc)
        stop.wait(POLL_SECONDS)
    _heartbeat_file(ws).unlink(missing_ok=True)


# ------------------------------------------------------------------ start automatically with Windows

def _self_command(ws: Workspace) -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" scheduler run --workspace "{ws.file}"'
    return f'"{sys.executable}" "{app_dir() / "scripts" / "manager.py"}" scheduler run --workspace "{ws.file}"'


def install(ws: Workspace, at: str = "logon") -> str:
    """Register a Windows scheduled task that starts this scheduler at logon (no admin needed) or at startup (admin)."""
    if os.name != "nt":
        raise RuntimeError("automatic start is only implemented for Windows; run 'scheduler run' from your own service manager")
    trigger = "ONLOGON" if at == "logon" else "ONSTART"
    cmd = ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", _self_command(ws), "/SC", trigger, "/F"]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip() or "schtasks failed")
    return f"Task '{TASK_NAME}' registered to start at {'logon' if at == 'logon' else 'system startup'}."


def uninstall() -> str:
    if os.name != "nt":
        raise RuntimeError("only implemented for Windows")
    done = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip() or "schtasks failed")
    return f"Task '{TASK_NAME}' removed."


def installed() -> bool:
    if os.name != "nt":
        return False
    return subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True).returncode == 0
