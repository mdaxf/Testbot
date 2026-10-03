"""Finding and reading test results (the files testbot.exe writes into the results folder)."""
from __future__ import annotations

import json
import re
import os
from pathlib import Path
from typing import Any

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


CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".html": "text/html; charset=utf-8", ".xml": "application/xml",
                 ".json": "application/json", ".txt": "text/plain; charset=utf-8", ".log": "text/plain; charset=utf-8"}


def file_for_serving(ws: Workspace, rel: str) -> tuple[Path, str]:
    path = ws.safe_path("results", rel)
    if not path.is_file():
        raise WorkspaceError("not found")
    return path, CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
