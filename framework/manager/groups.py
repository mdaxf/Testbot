"""Test groups: a named collection of sessions, suites and individual test cases, tracked over a time window.

A group answers "how far are we?" for a test effort such as a UAT cycle: 100 cases, starting Monday.

  members      {"type": "session", "path": "sessions/daily.yaml"}            every case the session runs
               {"type": "suite",   "path": "orders.json"}                    every case of the suite
               {"type": "case",    "path": "orders.json", "cases": ["TC-1"]} chosen cases of a suite
  resolved     to a set of cases, identified by (suite_id, case_id); a case reached through two members counts once
  window       optional start / end date (local days) and an optional environment: only results inside the window, and only runs against that
               environment, count. The final result of a case = its latest result in the window (a re-test replaces a failure);
               no result = "not run". Testers can also record a MANUAL result (pass / fail, who, comment) for a case.
  live/frozen  a live group follows its sessions and suites as they change; a frozen group keeps the list of cases it had when it was frozen
"""
from __future__ import annotations

import bisect
import copy
import csv
import html as _html
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from framework.manager import results, schedules, testcases
from framework.manager.workspace import Workspace, WorkspaceError

STATUSES = ("pass", "fail", "error", "inconclusive", "not_run")
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,60}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MEMBER_TYPES = ("session", "suite", "case")


# ------------------------------------------------------------------ storage

def _file(ws: Workspace, gid: str) -> Path:
    if not _ID.match(gid or ""):
        raise WorkspaceError("a group id is letters, digits, '-', '_' or '.' (max 60 characters)")
    return ws.dir("groups") / f"{gid}.json"


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip("-")[:60] or "group"


def read_group(ws: Workspace, gid: str) -> dict[str, Any]:
    p = _file(ws, gid)
    if not p.is_file():
        raise WorkspaceError(f"group '{gid}' does not exist")
    return json.loads(p.read_text(encoding="utf-8-sig"))


def _all(ws: Workspace) -> list[dict[str, Any]]:
    d = ws.dir("groups")
    out = []
    if d.exists():
        for p in sorted(d.glob("*.json")):
            try:
                g = json.loads(p.read_text(encoding="utf-8-sig"))
                if isinstance(g, dict) and g.get("id"):
                    out.append(g)
            except ValueError:
                continue
    return out


def validate(g: dict[str, Any]) -> None:
    if not _ID.match(str(g.get("id") or "")):
        raise ValueError("the group needs an id: letters, digits, '-', '_' or '.'")
    if not str(g.get("name") or "").strip():
        raise ValueError("the group needs a name")
    for key in ("start", "end"):
        if g.get(key) and not _DATE.match(str(g[key])):
            raise ValueError(f"{key} must be a date like 2026-10-05")
    if g.get("start") and g.get("end") and g["end"] < g["start"]:
        raise ValueError("the end date is before the start date")
    n = g.get("notify")
    if n:
        from framework import emailer

        if not isinstance(n, dict) or (n.get("on") and n["on"] not in emailer.RULES) or any(not emailer._ADDR.match(a) for a in emailer._addresses(n.get("to"))):
            raise ValueError("the group's email setting needs `on` (never, always or failure) and valid recipient addresses")
    for i, m in enumerate(g.get("members") or [], start=1):
        if m.get("type") not in MEMBER_TYPES or not m.get("path"):
            raise ValueError(f"member {i}: a type (session, suite or case) and a file are needed")
        if m["type"] == "case" and not (isinstance(m.get("cases"), list) and m["cases"]):
            raise ValueError(f"member {i}: choose at least one test case")


def save_group(ws: Workspace, g: dict[str, Any], *, create: bool = False) -> dict[str, Any]:
    g = json.loads(json.dumps(g))
    validate(g)
    p = _file(ws, g["id"])
    if create and p.exists():
        raise FileExistsError(f"a group with the id '{g['id']}' already exists")
    old: dict[str, Any] = json.loads(p.read_text(encoding="utf-8-sig")) if p.is_file() else {}
    for keep in ("manual", "closed"):                 # these are changed by their own actions, never by an edit of the definition
        if old.get(keep):
            g[keep] = old[keep]
        else:
            g.pop(keep, None)
    g["members"] = [{k: v for k, v in m.items() if k in ("type", "path", "cases")} for m in g.get("members") or []]
    for key in ("start", "end", "environment", "description"):
        if not g.get(key):
            g.pop(key, None)
    if g.get("notify"):
        g["notify"] = {k: v for k, v in g["notify"].items() if k in ("on", "to", "attach_report") and (v or k == "attach_report" and v)}
    if not g.get("notify"):
        g.pop("notify", None)
    if g.get("frozen"):
        if not old.get("frozen") or not old.get("snapshot"):
            g["snapshot"] = [{"suite_id": c["suite_id"], "case_id": c["case_id"], "title": c["title"], "path": c["path"]} for c in resolve(ws, {**g, "frozen": False})["cases"]]
        else:
            g["snapshot"] = old["snapshot"]
    else:
        g.pop("frozen", None)
        g.pop("snapshot", None)
    ws.dir("groups").mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(g, indent=2, ensure_ascii=False), encoding="utf-8")
    return g


def delete_group(ws: Workspace, gid: str) -> None:
    _file(ws, gid).unlink(missing_ok=True)


def duplicate_group(ws: Workspace, gid: str, new_id: str) -> dict[str, Any]:
    g = read_group(ws, gid)
    for key in ("manual", "closed", "snapshot"):
        g.pop(key, None)
    g.pop("frozen", None)
    g["id"], g["name"] = new_id, f"{g['name']} (copy)"
    return save_group(ws, g, create=True)


# ------------------------------------------------------------------ members -> cases

def _suite_dict(ws: Workspace, path: str) -> dict[str, Any]:
    return testcases.read_test(ws, path)["suite"]


def _member_cases(ws: Workspace, m: dict[str, Any]) -> tuple[list[dict[str, Any]], Optional[str]]:
    """The cases one member stands for, and a problem text if part of it could not be resolved."""
    out: list[dict[str, Any]] = []
    problem: Optional[str] = None
    try:
        if m["type"] in ("suite", "case"):
            suite = _suite_dict(ws, m["path"])
            wanted = m.get("cases") if m["type"] == "case" else None
            ids = {c.get("id"): c for c in suite.get("cases", [])}
            if wanted:
                missing = [w for w in wanted if w not in ids]
                if missing:
                    problem = f"not in {m['path']}: {', '.join(missing)}"
                chosen = [ids[w] for w in wanted if w in ids]
            else:
                chosen = suite.get("cases", [])
            out = [{"suite_id": suite.get("suite_id"), "case_id": c.get("id"), "title": c.get("title", ""), "path": m["path"]} for c in chosen]
        else:
            plan = schedules.read_session(ws, m["path"])
            from framework.models import TestSuite
            from framework.runner.selection import REPEAT_SEPARATOR, select_cases

            for f in plan.get("files", []):
                try:
                    suite = _suite_dict(ws, f.get("path", ""))
                except Exception as exc:  # noqa: BLE001
                    problem = f"{f.get('path')}: {exc}"
                    continue
                try:
                    chosen_ids = [c.id.split(REPEAT_SEPARATOR)[0] for c in select_cases(TestSuite.model_validate(copy.deepcopy(suite)), f.get("cases"), f.get("tags"))]
                except Exception as exc:  # noqa: BLE001 - an invalid suite / selection: show what we can
                    problem = f"{f.get('path')}: {exc}"
                    chosen_ids = f.get("cases") or [c.get("id") for c in suite.get("cases", [])]
                titles = {c.get("id"): c.get("title", "") for c in suite.get("cases", [])}
                out += [{"suite_id": suite.get("suite_id"), "case_id": cid, "title": titles.get(cid, ""), "path": f.get("path", "")} for cid in chosen_ids]
    except Exception as exc:  # noqa: BLE001
        problem = str(exc)
    return out, problem


def member_label(m: dict[str, Any]) -> str:
    return {"session": "session", "suite": "suite", "case": "cases of"}[m["type"]] + " " + m["path"] + (f" ({', '.join(m['cases'])})" if m["type"] == "case" else "")


def resolve(ws: Workspace, g: dict[str, Any]) -> dict[str, Any]:
    """{cases: [unique cases, first-seen order, each with `sources`], members: [{label, count, problem}], overlap}."""
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    members, total = [], 0
    for m in g.get("members") or []:
        cs, problem = _member_cases(ws, m)
        total += len(cs)
        members.append({"type": m["type"], "path": m["path"], "cases": m.get("cases"), "label": member_label(m), "count": len(cs), "problem": problem,
                        "keys": [(c["suite_id"], c["case_id"]) for c in cs]})
        for c in cs:
            key = (c["suite_id"], c["case_id"])
            if key not in unique:
                unique[key] = {**c, "sources": []}
            if m_label := member_label(m):
                if m_label not in unique[key]["sources"]:
                    unique[key]["sources"].append(m_label)
    if g.get("frozen") and g.get("snapshot"):
        keep = {(c["suite_id"], c["case_id"]) for c in g["snapshot"]}
        snap = {(c["suite_id"], c["case_id"]): c for c in g["snapshot"]}
        cases = []
        for key, c in snap.items():
            cases.append({**c, "sources": unique.get(key, {}).get("sources") or ["(frozen list)"]})
        for mem in members:
            mem["keys"] = [k for k in mem["keys"] if k in keep]
            mem["count"] = len(mem["keys"])
        total = sum(mem["count"] for mem in members)
    else:
        cases = list(unique.values())
    return {"cases": cases, "members": [{k: v for k, v in mem.items()} for mem in members], "overlap": max(total - len(cases), 0)}


# ------------------------------------------------------------------ tracking

def _day_start(d: date) -> float:
    return datetime(d.year, d.month, d.day).astimezone().timestamp()


def _day_end(d: date) -> float:
    return _day_start(d + timedelta(days=1)) - 0.001


def _status(s: Any) -> str:
    return s if s in ("pass", "fail", "error", "inconclusive") else "error"


def _counts(statuses: list[str]) -> dict[str, int]:
    out = {s: 0 for s in STATUSES}
    for s in statuses:
        out[s if s in out else "error"] += 1
    return out


def track_cases(ws: Workspace, cases: list[dict[str, Any]], start_ts: float, end_ts: float, *, environment: Optional[str] = None,
                manual: Optional[list[dict[str, Any]]] = None, members: Optional[list[dict[str, Any]]] = None, start_day: Optional[date] = None,
                end_day: Optional[date] = None, count_not_run: bool = True) -> dict[str, Any]:
    keys = {(c["suite_id"], c["case_id"]) for c in cases}
    occ_by: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for o in results.all_occurrences(ws):
        k = (o["suite_id"], o["case_id"])
        if k in keys and start_ts <= o["ts"] <= end_ts and (not environment or o.get("environment") == environment):
            occ_by.setdefault(k, []).append({"ts": o["ts"], "status": _status(o["status"]), "when": o["when"], "kind": "auto", "run_path": o["run_path"], "run_kind": o["run_kind"],
                                            "session_id": o["session_id"], "entry": o["entry"], "problem": o["problem"], "duration_ms": o["duration_ms"], "order": o["order"]})
    for mr in manual or []:
        k = (mr.get("suite_id"), mr.get("case_id"))
        ts = results._ts(mr.get("at"), 0.0)
        if k in keys and start_ts <= ts <= end_ts:
            occ_by.setdefault(k, []).append({"ts": ts, "status": mr.get("status"), "when": mr.get("at"), "kind": "manual", "by": mr.get("by"), "comment": mr.get("comment"),
                                            "run_path": None, "run_kind": None, "session_id": None, "entry": None, "problem": mr.get("comment") or "", "duration_ms": 0, "order": 0})
    rows = []
    for c in cases:
        k = (c["suite_id"], c["case_id"])
        occs = sorted(occ_by.get(k, []), key=lambda o: (o["ts"], o["order"]))
        last = occs[-1] if occs else None
        row = {"suite_id": c["suite_id"], "case_id": c["case_id"], "title": c.get("title", ""), "path": c.get("path"), "sources": c.get("sources", []),
               "status": last["status"] if last else "not_run", "when": last["when"] if last else None, "attempts": len(occs), "first_status": occs[0]["status"] if occs else None,
               "kind": last["kind"] if last else None, "by": last.get("by") if last else None, "comment": last.get("comment") if last else None,
               "run_path": last["run_path"] if last else None, "run_kind": last["run_kind"] if last else None, "session_id": last["session_id"] if last else None,
               "entry": last["entry"] if last else None, "problem": last["problem"] if last and last["status"] != "pass" else "",
               "failed_before": any(o["status"] != "pass" for o in occs[:-1]), "_ts": [o["ts"] for o in occs], "_st": [o["status"] for o in occs]}
        rows.append(row)
    if not count_not_run:
        rows = [r for r in rows if r["attempts"]]
    counts = _counts([r["status"] for r in rows])
    total = len(rows)
    executed = total - counts["not_run"]
    # daily series: the final status of every case as of the end of each day, and what was executed that day
    first_day = start_day
    if first_day is None:
        stamps = [t for r in rows for t in r["_ts"]]
        first_day = datetime.fromtimestamp(min(stamps)).date() if stamps else date.today()
    last_day = min(end_day or date.today(), date.today())
    daily = []
    d = first_day
    while d <= last_day and (d - first_day).days <= 400:
        eod, sod = _day_end(d), _day_start(d)
        finals, ran = [], {"pass": 0, "fail": 0, "other": 0}
        for r in rows:
            i = bisect.bisect_right(r["_ts"], eod)
            finals.append(r["_st"][i - 1] if i else "not_run")
            for t, s in zip(r["_ts"], r["_st"]):
                if sod <= t <= eod:
                    ran["pass" if s == "pass" else ("fail" if s == "fail" else "other")] += 1
        daily.append({"date": d.isoformat(), **_counts(finals), "ran_pass": ran["pass"], "ran_fail": ran["fail"], "ran_other": ran["other"]})
        d += timedelta(days=1)
    by_member = []
    final_by_key = {(r["suite_id"], r["case_id"]): r["status"] for r in rows}
    for m in members or []:
        ks = [k for k in m.get("keys", []) if k in final_by_key]
        by_member.append({"label": m["label"], **_counts([final_by_key[k] for k in ks]), "total": len(ks)})
    for r in rows:
        r.pop("_ts"), r.pop("_st")
    pick = lambda pred: [r for r in rows if pred(r)]
    return {"total": total, "counts": counts, "executed": executed,
            "pct_executed": round(100 * executed / total, 1) if total else 0, "pct_pass": round(100 * counts["pass"] / total, 1) if total else 0,
            "first_time_pass": sum(1 for r in rows if r["first_status"] == "pass"), "daily": daily, "by_member": by_member, "cases": rows,
            "not_run": pick(lambda r: r["status"] == "not_run"), "failing": pick(lambda r: r["status"] in ("fail", "error", "inconclusive")),
            "retested": pick(lambda r: r["status"] == "pass" and r["failed_before"])}


def _window(g: dict[str, Any]) -> tuple[float, float, Optional[date], Optional[date]]:
    sd = date.fromisoformat(g["start"]) if g.get("start") else None
    ed = date.fromisoformat(g["end"]) if g.get("end") else None
    start_ts = _day_start(sd) if sd else 0.0
    end_ts = _day_end(ed) if ed else datetime.now(timezone.utc).timestamp() + 1
    return start_ts, end_ts, sd, ed


def track(ws: Workspace, g: dict[str, Any]) -> dict[str, Any]:
    res = resolve(ws, g)
    start_ts, end_ts, sd, ed = _window(g)
    t = track_cases(ws, res["cases"], start_ts, end_ts, environment=g.get("environment") or None, manual=g.get("manual"), members=res["members"], start_day=sd, end_day=ed)
    t["members"] = [{k: v for k, v in m.items() if k != "keys"} for m in res["members"]]
    t["overlap"] = res["overlap"]
    return t


def summary(ws: Workspace, g: dict[str, Any]) -> dict[str, Any]:
    t = track(ws, g)
    return {"id": g["id"], "name": g["name"], "description": g.get("description", ""), "start": g.get("start"), "end": g.get("end"), "environment": g.get("environment"),
            "frozen": bool(g.get("frozen")), "closed": g.get("closed"), "members": len(g.get("members") or []), "total": t["total"], "counts": t["counts"],
            "pct_executed": t["pct_executed"], "pct_pass": t["pct_pass"]}


def list_groups(ws: Workspace) -> list[dict[str, Any]]:
    out = []
    for g in _all(ws):
        try:
            out.append(summary(ws, g))
        except Exception as exc:  # noqa: BLE001 - one broken group must not hide the others
            out.append({"id": g["id"], "name": g.get("name", g["id"]), "broken": str(exc)[:200], "total": 0, "counts": {s: 0 for s in STATUSES}})
    return out


def groups_of(ws: Workspace, suite_id: Optional[str] = None, case_id: Optional[str] = None) -> list[dict[str, Any]]:
    """The groups that contain a test case (or any case of a suite) with the case's status in that group."""
    out = []
    for g in _all(ws):
        try:
            res = resolve(ws, g)
        except Exception:  # noqa: BLE001
            continue
        hit = [c for c in res["cases"] if c["suite_id"] == suite_id and (case_id is None or c["case_id"] == case_id)]
        if hit:
            t = track(ws, g)
            status = next((r["status"] for r in t["cases"] if r["suite_id"] == suite_id and (case_id is None or r["case_id"] == case_id)), None) if case_id else None
            out.append({"id": g["id"], "name": g["name"], "status": status, "pct_pass": t["pct_pass"], "cases": len(hit)})
    return out


# ------------------------------------------------------------------ manual results, closing, running what is left

def add_manual(ws: Workspace, gid: str, suite_id: str, case_id: str, status: str, by: str, comment: str = "") -> dict[str, Any]:
    g = read_group(ws, gid)
    if status not in ("pass", "fail"):
        raise ValueError("a manual result is pass or fail")
    if not str(by).strip():
        raise ValueError("say who tested it")
    if not any(c["suite_id"] == suite_id and c["case_id"] == case_id for c in resolve(ws, g)["cases"]):
        raise ValueError("that test case is not in the group")
    g.setdefault("manual", []).append({"suite_id": suite_id, "case_id": case_id, "status": status, "by": str(by).strip()[:80], "comment": str(comment or "")[:500],
                                       "at": datetime.now(timezone.utc).isoformat()})
    _file(ws, gid).write_text(json.dumps(g, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"recorded": True}


def set_closed(ws: Workspace, gid: str, closed: bool) -> dict[str, Any]:
    g = read_group(ws, gid)
    if closed:
        g["closed"] = {"at": datetime.now(timezone.utc).isoformat()}
        g.setdefault("end", date.today().isoformat())      # a closed cycle is frozen at the day it was signed off
    else:
        g.pop("closed", None)
    _file(ws, gid).write_text(json.dumps(g, indent=2, ensure_ascii=False), encoding="utf-8")
    return g


def remaining_plan(ws: Workspace, gid: str) -> dict[str, Any]:
    """Kept for the callers that want only the cases that have not passed yet."""
    return run_plan(ws, gid, "remaining")


def run_plan(ws: Workspace, gid: str, mode: str = "all") -> dict[str, Any]:
    """A session plan (saved under test_cases/_groups/) that runs the group's cases: every case (mode "all") or only those that have not passed yet
    ("remaining"). The plan carries the group's environment and its `notify` (email) setting. Returns {path, count, files}."""
    if mode not in ("all", "remaining"):
        raise ValueError("mode must be all or remaining")
    g = read_group(ws, gid)
    if g.get("closed"):
        raise ValueError(f"the group '{g['name']}' is closed (signed off): re-open it to run it")
    t = track(ws, g)
    runnable = [c for c in t["cases"] if c["path"]]
    todo = runnable if mode == "all" else [c for c in runnable if c["status"] != "pass"]
    if not t["cases"]:
        raise ValueError(f"the group '{g['name']}' has no test cases")
    if not todo:
        raise ValueError("every test case of the group has already passed" if mode == "remaining" else "none of the group's test cases can be run (their files were not found)")
    order: dict[str, list[str]] = {}
    for c in todo:
        order.setdefault(c["path"], [])
        if c["case_id"] not in order[c["path"]]:
            order[c["path"]].append(c["case_id"])
    files = []
    for path, ids in order.items():
        full = ws.safe_path("test_cases", path)
        try:
            shown = full.relative_to(ws.root).as_posix()
        except ValueError:
            shown = str(full)
        files.append({"path": shown, "cases": ids})
    label = "all test cases" if mode == "all" else "cases that have not passed yet"
    plan: dict[str, Any] = {"session_id": f"GROUP-{gid}-{mode.upper()}"[:60], "session_name": f"{g['name']}: {label}", "files": files}
    if g.get("environment"):
        plan["environment"] = g["environment"]
    if g.get("notify"):
        plan["notify"] = g["notify"]
    d = ws.dir("test_cases") / "_groups"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{gid}-{mode}.yaml"
    p.write_text(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"path": ws.rel("test_cases", p), "count": len(todo), "files": len(files)}


# ------------------------------------------------------------------ analysis of one suite / session over the last N days

def analysis(ws: Workspace, kind: str, ident: str, days: int) -> dict[str, Any]:
    occ = results.all_occurrences(ws)
    keys: dict[tuple[str, str], dict[str, Any]] = {}
    for o in occ:
        if (kind == "suite" and o["suite_id"] == ident) or (kind == "session" and o["session_id"] == ident):
            keys.setdefault((o["suite_id"], o["case_id"]), {"suite_id": o["suite_id"], "case_id": o["case_id"], "title": o["title"], "path": None, "sources": []})
    days = max(1, min(int(days or 30), 400))
    today = date.today()
    sd = today - timedelta(days=days - 1)
    t = track_cases(ws, list(keys.values()), _day_start(sd), _day_end(today), start_day=sd, end_day=today, count_not_run=False)
    t["days"] = days
    return t


# ------------------------------------------------------------------ export

def _local(iso: Optional[str]) -> str:
    try:
        d = datetime.fromisoformat(str(iso))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return ""


def export_csv(ws: Workspace, gid: str) -> str:
    g = read_group(ws, gid)
    t = track(ws, g)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Group", "Test suite", "Test case", "Title", "Final result", "Final test (local time)", "Attempts", "First result", "Recorded by", "Comment / problem", "Members"])
    for r in t["cases"]:
        w.writerow([g["name"], r["suite_id"], r["case_id"], r["title"], r["status"], _local(r["when"]), r["attempts"], r["first_status"] or "",
                    (r["by"] or "") if r["kind"] == "manual" else "", (r["comment"] if r["kind"] == "manual" else r["problem"]) or "", "; ".join(r["sources"])])
    return buf.getvalue()


_COLORS = {"pass": "#1a7f37", "fail": "#cf222e", "error": "#bc4c00", "inconclusive": "#9a6700", "not_run": "#8c959f"}
_LABELS = {"pass": "Pass", "fail": "Fail", "error": "Error", "inconclusive": "Inconclusive", "not_run": "Not run"}


def _donut_svg(counts: dict[str, int]) -> str:
    import math

    total = sum(counts.values()) or 1
    cx = cy = 90
    r, r2 = 80, 48
    parts, angle = [], -math.pi / 2
    for s in STATUSES:
        n = counts.get(s, 0)
        if not n:
            continue
        a2 = angle + 2 * math.pi * n / total
        if n == total:
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{(r + r2) / 2}" fill="none" stroke="{_COLORS[s]}" stroke-width="{r - r2}"/>')
        else:
            x1, y1, x2, y2 = cx + r * math.cos(angle), cy + r * math.sin(angle), cx + r * math.cos(a2), cy + r * math.sin(a2)
            x3, y3, x4, y4 = cx + r2 * math.cos(a2), cy + r2 * math.sin(a2), cx + r2 * math.cos(angle), cy + r2 * math.sin(angle)
            large = 1 if a2 - angle > math.pi else 0
            parts.append(f'<path d="M{x1:.1f},{y1:.1f} A{r},{r} 0 {large} 1 {x2:.1f},{y2:.1f} L{x3:.1f},{y3:.1f} A{r2},{r2} 0 {large} 0 {x4:.1f},{y4:.1f} Z" fill="{_COLORS[s]}"/>')
        angle = a2
    done = total - counts.get("not_run", 0)
    parts.append(f'<text x="{cx}" y="{cy + 6}" text-anchor="middle" font-size="22" font-family="Segoe UI,Arial" fill="#24292f">{round(100 * counts.get("pass", 0) / total)}%</text>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180" viewBox="0 0 180 180">{"".join(parts)}</svg>'


def _daily_svg(daily: list[dict[str, Any]]) -> str:
    if not daily:
        return ""
    w, h, left, bottom = 560, 200, 34, 24
    total = max((sum(d[s] for s in STATUSES) for d in daily), default=1) or 1
    bw = max(4, min(40, (w - left) / len(daily) - 4))
    parts = [f'<line x1="{left}" y1="{h - bottom}" x2="{w}" y2="{h - bottom}" stroke="#d0d7de"/>', f'<text x="2" y="12" font-size="10" fill="#57606a">{total}</text>']
    for i, d in enumerate(daily):
        x, y = left + i * (bw + 4), h - bottom
        for s in ("pass", "fail", "error", "inconclusive", "not_run"):
            hh = (h - bottom - 14) * d[s] / total
            if hh <= 0:
                continue
            y -= hh
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{hh:.1f}" fill="{_COLORS[s]}"><title>{d["date"]}: {_LABELS[s]} {d[s]}</title></rect>')
        if len(daily) <= 16 or i % max(1, len(daily) // 10) == 0:
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{h - 8}" text-anchor="middle" font-size="9" fill="#57606a">{d["date"][5:]}</text>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{"".join(parts)}</svg>'


def export_html(ws: Workspace, gid: str) -> str:
    g = read_group(ws, gid)
    t = track(ws, g)
    e = _html.escape
    c = t["counts"]
    legend = "".join(f'<span style="margin-right:14px"><span style="display:inline-block;width:10px;height:10px;background:{_COLORS[s]}"></span> {_LABELS[s]} {c[s]}</span>' for s in STATUSES)
    rows = "".join(
        f'<tr><td>{e(str(r["suite_id"]))}</td><td>{e(str(r["case_id"]))}</td><td>{e(r["title"])}</td><td style="color:{_COLORS.get(r["status"], "#000")}">{_LABELS.get(r["status"], r["status"])}</td>'
        f'<td>{e(_local(r["when"]))}</td><td>{r["attempts"]}</td><td>{e(((r["by"] or "") + (": " + r["comment"] if r["comment"] else "")) if r["kind"] == "manual" else (r["problem"] or ""))}</td></tr>' for r in t["cases"])
    members = "".join(f'<tr><td>{e(m["label"])}</td><td>{m["total"]}</td><td>{m["pass"]}</td><td>{m["fail"]}</td><td>{m["error"] + m["inconclusive"]}</td><td>{m["not_run"]}</td></tr>' for m in t["by_member"])
    period = f'{g.get("start") or "start"} to {g.get("end") or "today"}' + (f', environment {e(g["environment"])}' if g.get("environment") else "")
    signed = f'<p><b>Signed off (closed):</b> {e(_local(g["closed"]["at"]))}</p>' if g.get("closed") else ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{e(g["name"])}</title></head><body style="font-family:Segoe UI,Arial,sans-serif;max-width:1100px;margin:20px auto">'
            f'<h1>{e(g["name"])}</h1><p>{e(g.get("description", ""))}</p><p>Period: {period}. Generated {e(_local(datetime.now(timezone.utc).isoformat()))}.</p>{signed}'
            f'<p><b>{t["total"]}</b> test case(s): <b>{t["executed"]}</b> executed ({t["pct_executed"]}%), <b>{c["pass"]}</b> passed ({t["pct_pass"]}%), '
            f'{t["first_time_pass"]} passed at the first attempt.</p>'
            f'<div style="display:flex;gap:30px;align-items:center">{_donut_svg(c)}<div>{legend}<h3>Progress by day (final result of every case at the end of each day)</h3>{_daily_svg(t["daily"])}</div></div>'
            f'<h2>By member</h2><table border="1" cellpadding="4" cellspacing="0"><tr><th>Member</th><th>Cases</th><th>Pass</th><th>Fail</th><th>Error</th><th>Not run</th></tr>{members}</table>'
            f'<h2>Every test case</h2><table border="1" cellpadding="4" cellspacing="0"><tr><th>Suite</th><th>Case</th><th>Title</th><th>Final result</th><th>Final test</th><th>Attempts</th><th>Problem / manual note</th></tr>{rows}</table>'
            f'</body></html>')
