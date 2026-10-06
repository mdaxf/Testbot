"""Finding and reading test results (the files testbot.exe writes into the results folder)."""
from __future__ import annotations

import json
import re
import os
from pathlib import Path
from typing import Any, Optional

from framework.manager.workspace import Workspace, WorkspaceError

_cache: dict[tuple[str, float], dict[str, Any]] = {}


def _counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    return {"cases": len(cases), "passed": sum(1 for c in cases if c.get("status") == "pass"),
            "failed": sum(1 for c in cases if c.get("status") == "fail"), "errors": sum(1 for c in cases if c.get("status") in ("error", "inconclusive"))}


def _summary(path: Path, rel: str) -> dict[str, Any]:
    key = (str(path), path.stat().st_mtime)
    if key in _cache:
        return _cache[key]
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    info: dict[str, Any] = {"path": rel, "dir": str(Path(rel).parent.as_posix()) if str(Path(rel).parent) != "." else ""}
    if path.name == "load-summary.json":
        info.update(kind="load", id=path.parent.name, name=f"Load run: {data.get('settings', {}).get('workers', '?')} worker(s)",
                    started=None, cases=data.get("iterations_run", 0), passed=data.get("passed", 0),
                    failed=data.get("failed", 0), errors=0, elapsed_s=data.get("elapsed_s"))
    elif "suites" in data:
        cases = [c for s in data["suites"] for c in s.get("cases", [])]
        info.update(kind="session", id=data.get("session_id"), name=data.get("session_name"), started=data.get("started_at"),
                    finished=data.get("finished_at"), **_counts(cases))
    else:
        info.update(kind="suite", id=data.get("suite_id"), name=data.get("suite_id"), started=data.get("started_at"),
                    finished=data.get("finished_at"), environment=data.get("environment"), **_counts(data.get("cases", [])))
    info["status"] = "pass" if info.get("cases") and not info.get("failed") and not info.get("errors") else ("empty" if not info.get("cases") else "fail")
    _cache[key] = info
    return info


def list_runs(ws: Workspace, limit: int = 300) -> list[dict[str, Any]]:
    base = ws.dir("results")
    if not base.exists():
        return []
    load_dirs = {p.parent for p in base.rglob("load-summary.json")}
    runs: list[dict[str, Any]] = []
    for path in base.rglob("*.json"):
        if not (path.name.endswith("-result.json") or path.name == "load-summary.json"):
            continue
        if path.name != "load-summary.json" and any(d in path.parents for d in load_dirs):
            continue  # a single worker iteration: shown inside its load run, not as a run of its own
        try:
            info = _summary(path, ws.rel("results", path))
        except Exception:  # noqa: BLE001 - a damaged/foreign json file is simply not a result
            continue
        info = dict(info)
        info["modified"] = path.stat().st_mtime
        if not info.get("started"):
            info["started"] = None
        runs.append(info)
    runs.sort(key=lambda r: r["modified"], reverse=True)
    return runs[:limit]


def read_run(ws: Workspace, rel: str) -> dict[str, Any]:
    path = ws.safe_path("results", rel)
    if not path.is_file():
        raise WorkspaceError(f"'{rel}' does not exist")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    folder = Path(rel).parent.as_posix()
    html = next((p.name for p in path.parent.glob("*.html") if p.stem == path.name.replace("-result.json", "")), None)
    return {"path": rel, "folder": "" if folder == "." else folder, "data": data, "html_report": html,
            "kind": "load" if path.name == "load-summary.json" else ("session" if "suites" in data else "suite")}


def worker_results(ws: Workspace, rel_dir: str) -> list[dict[str, Any]]:
    """The per-iteration results inside a load run's folder."""
    base = ws.safe_path("results", rel_dir)
    out = []
    for path in sorted(base.rglob("*-result.json")):
        try:
            info = dict(_summary(path, ws.rel("results", path)))
        except Exception:  # noqa: BLE001
            continue
        out.append(info)
    return out


_LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(\S+)\s+(.*)$")


def read_log(ws: Workspace, rel: str, limit: int = 20000) -> dict[str, Any]:
    """The step log (run.log) written by the runner for the run behind a result file, as structured lines:
    {t, level, area, msg}. Lines that do not start with a timestamp (a long message wrapped) belong to the previous line."""
    result = ws.safe_path("results", rel)
    folder = result.parent
    stem = result.name.replace("-result.json", "")
    candidates = sorted(folder.glob(f"{stem}_*/run.log"), key=lambda p: p.stat().st_mtime, reverse=True) + [folder / "run.log"]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        return {"found": False, "lines": [], "file": None}
    lines: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LOG_LINE.match(raw)
        if m:
            lines.append({"t": m.group(1), "level": m.group(2), "area": m.group(3), "msg": m.group(4)})
        elif lines and raw.strip():
            lines[-1]["msg"] += "\n" + raw
        if len(lines) >= limit:
            break
    return {"found": True, "file": ws.rel("results", path), "lines": lines, "truncated": len(lines) >= limit}


# ------------------------------------------------------------------ results organised by test case, suite and session
#
# One record per time a test case ran ("occurrence"), read from the result files and cached per file. From those:
#   * by test case:  the FINAL result = the most recent occurrence (by finish time), over standalone suite runs and session runs alike
#   * by suite:      the most recent run of the suite (standalone, or an entry of a session) and the final result of each of its cases
#   * by session:    the most recent run of the session
# A case is identified by (suite_id, case_id): ids repeat across files. A repeat inside one run ("TC-1~2") counts under "TC-1".

from datetime import datetime, timezone

_occ_cache: dict[tuple[str, float], list[dict[str, Any]]] = {}
_REPEAT = re.compile(r"^(.*)~(\d+)$")


def _ts(text: Any, fallback: float = 0.0) -> float:
    """ISO time -> epoch seconds (a time without zone is taken as UTC, as testbot writes it)."""
    try:
        d = datetime.fromisoformat(str(text))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except (TypeError, ValueError):
        return fallback


def _first_problem(case: dict[str, Any]) -> str:
    for st in case.get("steps", []):
        if st.get("status") not in ("pass", "skipped"):
            msg = st.get("error") or (f"expected {st.get('expected')!r}, actual {st.get('actual')!r}" if st.get("expected") is not None or st.get("actual") is not None else "")
            return f"step {st.get('step_no')}: {st.get('description', '')}" + (f" -- {str(msg)[:160]}" if msg else "")
    return ""


def _result_files(ws: Workspace):
    base = ws.dir("results")
    if not base.exists():
        return
    load_dirs = {p.parent for p in base.rglob("load-summary.json")}
    for path in base.rglob("*-result.json"):
        if any(d in path.parents for d in load_dirs):
            continue      # one iteration of a load run: it belongs to the load run, not to the history of a test case
        yield path


def _occurrences(ws: Workspace, path: Path) -> list[dict[str, Any]]:
    key = (str(path), path.stat().st_mtime)
    if key in _occ_cache:
        return _occ_cache[key]
    out: list[dict[str, Any]] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        session = "suites" in data
        suites = data["suites"] if session else [data]
        mtime = path.stat().st_mtime
        rel = ws.rel("results", path)
        run_id = data.get("session_id") if session else data.get("suite_id")
        run_when = data.get("finished_at") or data.get("started_at")
        for entry, suite in enumerate(suites, start=1):
            suite_when = suite.get("finished_at") or suite.get("started_at") or run_when
            for order, c in enumerate(suite.get("cases", [])):
                raw = str(c.get("case_id", ""))
                m = _REPEAT.match(raw)
                base, occ = (m.group(1), int(m.group(2))) if m else (raw, 1)
                when = c.get("finished_at") or c.get("started_at") or suite_when
                out.append({
                    "suite_id": suite.get("suite_id"), "case_id": base, "raw_id": raw, "occurrence": occ, "title": c.get("title", ""),
                    "status": c.get("status"), "when": when, "ts": _ts(when, mtime), "started": c.get("started_at") or suite.get("started_at"),
                    "duration_ms": c.get("duration_ms", 0), "steps": len(c.get("steps", [])), "problem": _first_problem(c) if c.get("status") != "pass" else "",
                    "run_path": rel, "run_kind": "session" if session else "suite", "run_id": run_id,
                    "session_id": data.get("session_id") if session else None, "session_name": data.get("session_name") if session else None,
                    "entry": entry if session else None, "entries": len(suites) if session else None, "order": order,
                    "selected": suite.get("selected"), "cases_in_file": suite.get("cases_in_file"),
                    "environment": suite.get("environment"), "run_when": run_when, "revision": c.get("revision"), "suite_revision": suite.get("revision"),
                })
    except Exception:  # noqa: BLE001 - a damaged or foreign json file is simply not a result
        out = []
    _occ_cache[key] = out
    return out


def all_occurrences(ws: Workspace) -> list[dict[str, Any]]:
    occ: list[dict[str, Any]] = []
    for path in _result_files(ws):
        occ += _occurrences(ws, path)
    return occ


def _final(status_list: list[str]) -> str:
    if not status_list:
        return "empty"
    if all(s == "pass" for s in status_list):
        return "pass"
    return "fail" if all(s in ("pass", "fail") for s in status_list) else "error"


def _latest(items: list[dict[str, Any]]) -> dict[str, Any]:
    return max(items, key=lambda o: (o["ts"], o["run_path"], o["order"]))


def _brief(o: dict[str, Any]) -> dict[str, Any]:
    keys = ("suite_id", "case_id", "title", "status", "when", "duration_ms", "problem", "run_path", "run_kind", "run_id", "session_id", "session_name", "entry", "occurrence", "selected", "cases_in_file", "revision", "suite_revision")
    return {k: o.get(k) for k in keys}


def by_case(ws: Workspace) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for o in all_occurrences(ws):
        groups.setdefault((o["suite_id"], o["case_id"]), []).append(o)
    rows = []
    for (suite_id, case_id), items in groups.items():
        last = _latest(items)
        ordered = sorted(items, key=lambda o: (o["ts"], o["order"]))
        rows.append({**_brief(last), "runs": len(items), "passed": sum(1 for o in items if o["status"] == "pass"),
                     "failed": sum(1 for o in items if o["status"] == "fail"), "errors": sum(1 for o in items if o["status"] not in ("pass", "fail")),
                     "recent": [o["status"] for o in ordered[-12:]], "sessions": sorted({o["session_id"] for o in items if o["session_id"]})})
    rows.sort(key=lambda r: _ts(r["when"]), reverse=True)
    return rows


def _instances(items: list[dict[str, Any]], key) -> list[list[dict[str, Any]]]:
    """Group case occurrences into runs: (result file, entry) for suites; the result file for sessions."""
    inst: dict[Any, list[dict[str, Any]]] = {}
    for o in items:
        inst.setdefault(key(o), []).append(o)
    return list(inst.values())


def _instance_row(group: list[dict[str, Any]]) -> dict[str, Any]:
    last = _latest(group)
    statuses = [o["status"] for o in group]
    return {"status": _final(statuses), "when": max((o["when"] for o in group if o["when"]), key=_ts, default=None), "path": last["run_path"], "kind": last["run_kind"],
            "session_id": last["session_id"], "session_name": last["session_name"], "entry": last["entry"], "cases": len({o["case_id"] for o in group}),
            "passed": sum(1 for s in statuses if s == "pass"), "failed": sum(1 for s in statuses if s == "fail"), "errors": sum(1 for s in statuses if s not in ("pass", "fail")),
            "selected": last["selected"], "cases_in_file": last["cases_in_file"], "environment": last["environment"],
            "duration_s": round(sum(o["duration_ms"] for o in group) / 1000, 1)}


def by_suite(ws: Workspace) -> list[dict[str, Any]]:
    occ = all_occurrences(ws)
    groups: dict[str, list[dict[str, Any]]] = {}
    for o in occ:
        groups.setdefault(o["suite_id"], []).append(o)
    final_cases = {}
    for r in by_case(ws):
        final_cases.setdefault(r["suite_id"], []).append(r)
    rows = []
    for suite_id, items in groups.items():
        runs = sorted((_instance_row(g) for g in _instances(items, lambda o: (o["run_path"], o["entry"]))), key=lambda r: _ts(r["when"]))
        last = runs[-1]
        finals = final_cases.get(suite_id, [])
        rows.append({"suite_id": suite_id, "status": last["status"], "when": last["when"], "path": last["path"], "kind": last["kind"], "session_id": last["session_id"],
                     "runs": len(runs), "recent": [r["status"] for r in runs[-12:]], "cases": len(finals),
                     "cases_pass": sum(1 for c in finals if c["status"] == "pass"), "cases_fail": sum(1 for c in finals if c["status"] == "fail"),
                     "cases_error": sum(1 for c in finals if c["status"] not in ("pass", "fail")), "environment": last["environment"]})
    rows.sort(key=lambda r: _ts(r["when"]), reverse=True)
    return rows


def by_session(ws: Workspace) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for o in all_occurrences(ws):
        if o["session_id"]:
            groups.setdefault(o["session_id"], []).append(o)
    rows = []
    for sid, items in groups.items():
        runs = sorted((_instance_row(g) for g in _instances(items, lambda o: o["run_path"])), key=lambda r: _ts(r["when"]))
        last = runs[-1]
        entries = max((o["entries"] or 0) for o in items)
        rows.append({"session_id": sid, "name": last["session_name"], "status": last["status"], "when": last["when"], "path": last["path"], "runs": len(runs),
                     "recent": [r["status"] for r in runs[-12:]], "entries": entries, "cases": last["cases"], "passed": last["passed"], "failed": last["failed"], "errors": last["errors"]})
    rows.sort(key=lambda r: _ts(r["when"]), reverse=True)
    return rows


def suite_detail(ws: Workspace, suite_id: str) -> dict[str, Any]:
    items = [o for o in all_occurrences(ws) if o["suite_id"] == suite_id]
    if not items:
        raise WorkspaceError(f"no results for suite '{suite_id}'")
    runs = sorted((_instance_row(g) for g in _instances(items, lambda o: (o["run_path"], o["entry"]))), key=lambda r: _ts(r["when"]), reverse=True)
    return {"suite_id": suite_id, "final": runs[0], "runs": runs, "cases": [r for r in by_case(ws) if r["suite_id"] == suite_id]}


def session_detail(ws: Workspace, session_id: str) -> dict[str, Any]:
    items = [o for o in all_occurrences(ws) if o["session_id"] == session_id]
    if not items:
        raise WorkspaceError(f"no results for session '{session_id}'")
    runs = sorted((_instance_row(g) for g in _instances(items, lambda o: o["run_path"])), key=lambda r: _ts(r["when"]), reverse=True)
    return {"session_id": session_id, "name": runs[0]["session_name"], "final": runs[0], "runs": runs}


def _session_definitions(ws: Workspace, suite_id: str, case_id: str) -> list[dict[str, Any]]:
    """Saved session plans that include this suite (and this case, or all of the suite's cases / a tag selection)."""
    from framework.manager import schedules, testcases

    latest = {r["session_id"]: r for r in by_session(ws)}
    out = []
    for s in schedules.list_sessions(ws):
        try:
            plan = schedules.read_session(ws, s["path"])
        except Exception:  # noqa: BLE001
            continue
        hits = []
        for n, f in enumerate(plan.get("files", []), start=1):
            try:
                sid = testcases._summarise(ws.safe_path("test_cases", f.get("path", ""))).get("suite_id")
            except Exception:  # noqa: BLE001
                continue
            cases, tags = f.get("cases"), f.get("tags")
            if sid == suite_id and (not cases and not tags or (cases and case_id in cases) or tags):
                hits.append({"entry": n, "selection": cases or (["tag: " + ", ".join(tags)] if tags else None)})
        if hits:
            run = latest.get(plan.get("session_id"))
            out.append({"path": s["path"], "session_id": plan.get("session_id"), "name": plan.get("session_name"), "entries": hits,
                        "last_status": run["status"] if run else None, "last_when": run["when"] if run else None, "last_run_path": run["path"] if run else None})
    return out


def case_detail(ws: Workspace, suite_id: str, case_id: str) -> dict[str, Any]:
    items = [o for o in all_occurrences(ws) if o["suite_id"] == suite_id and o["case_id"] == case_id]
    if not items:
        raise WorkspaceError(f"no results for test case '{suite_id} / {case_id}'")
    items.sort(key=lambda o: (o["ts"], o["run_path"], o["order"]), reverse=True)
    return {"suite_id": suite_id, "case_id": case_id, "title": items[0]["title"], "final": _brief(items[0]),
            "counts": {"runs": len(items), "passed": sum(1 for o in items if o["status"] == "pass"), "failed": sum(1 for o in items if o["status"] == "fail"),
                       "errors": sum(1 for o in items if o["status"] not in ("pass", "fail"))},
            "recent": [o["status"] for o in reversed(items[:12])], "occurrences": [_brief(o) | {"steps": o["steps"], "started": o["started"], "environment": o["environment"]} for o in items],
            "sessions": _session_definitions(ws, suite_id, case_id)}


def case_run(ws: Workspace, rel: str, suite_id: str, case_id: str, occurrence: int = 1, entry: Optional[int] = None) -> dict[str, Any]:
    """The step-level result of one test case in one run file."""
    path = ws.safe_path("results", rel)
    if not path.is_file():
        raise WorkspaceError(f"'{rel}' does not exist")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    suites = data["suites"] if "suites" in data else [data]
    raw = case_id if occurrence <= 1 else f"{case_id}~{occurrence}"
    for n, suite in enumerate(suites, start=1):
        if suite.get("suite_id") != suite_id or (entry and n != entry):
            continue
        for c in suite.get("cases", []):
            if c.get("case_id") == raw:
                folder = Path(rel).parent.as_posix()
                html = next((p.name for p in path.parent.glob("*.html") if p.stem == path.name.replace("-result.json", "")), None)
                return {"case": c, "suite": {k: suite.get(k) for k in ("suite_id", "environment", "started_at", "finished_at", "selected", "cases_in_file")},
                        "run": {"path": rel, "kind": "session" if "suites" in data else "suite", "id": data.get("session_id") or data.get("suite_id"), "entry": n if "suites" in data else None,
                                "started": data.get("started_at"), "finished": data.get("finished_at")},
                        "folder": "" if folder == "." else folder, "html_report": html}
    raise WorkspaceError(f"test case '{suite_id} / {case_id}' is not in {rel}")


CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".html": "text/html; charset=utf-8", ".xml": "application/xml",
                 ".json": "application/json", ".txt": "text/plain; charset=utf-8", ".log": "text/plain; charset=utf-8"}


def file_for_serving(ws: Workspace, rel: str) -> tuple[Path, str]:
    path = ws.safe_path("results", rel)
    if not path.is_file():
        raise WorkspaceError("not found")
    return path, CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
