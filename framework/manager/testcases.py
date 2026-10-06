"""Reading, saving and checking test cases for the manager UI.

The editor works on the RAW suite dictionary (what is in the JSON file), not on the runner's normalised model,
so saving never rewrites a hand-written file into a different shape. The runner's own model is used only to validate."""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from framework import revisions, suitefix
from framework.loaders.errors import friendly
from framework.loaders.excel_loader import load_suite_from_excel
from framework.manager.catalog import BUILTIN_VARIABLES
from framework.manager.workspace import Workspace, WorkspaceError
from framework.models import TestSuite

SUITE_EXTENSIONS = {".json", ".xlsx", ".xlsm"}
NEEDS_TARGET = {"click", "type", "select", "hover", "press", "upload", "check", "wait_until"}
EXPECTED_NEEDS_TARGET = {"text_equals", "text_contains", "value_equals", "visible", "hidden", "count_equals",
                         "css_equals", "has_class", "list_matches"}
NEEDS_CONFIG = {"rest_call", "bus_subscribe", "bus_wait", "bus_publish"}
_VAR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConflictError(Exception):
    """The file changed on disk since the browser loaded it."""


# ------------------------------------------------------------------ normalisation helpers

def clean_suite_dict(suite: TestSuite) -> dict[str, Any]:
    """A TestSuite as a tidy, minimal dict (defaults and empty values removed) -- used when converting Excel/imports."""
    data = suite.model_dump(mode="json", exclude_none=True, by_alias=True)
    for key in ("variables", "connections"):
        if not data.get(key):
            data.pop(key, None)
    for case in data["cases"]:
        for key in ("variables", "device", "viewport"):
            if not case.get(key):
                case.pop(key, None)
        for step in case["steps"]:
            for key in [k for k, v in step.items() if v in ({}, [], "") and k != "input"]:
                step.pop(key)
            if step.get("connection") == "default":
                step.pop("connection")
            if step.get("timeout_ms") == 10_000:
                step.pop("timeout_ms")
            exp = step.get("expected")
            if exp is not None:
                for key in [k for k, v in exp.items() if v is None]:
                    exp.pop(key)
    return data


def renumber(suite: dict[str, Any]) -> dict[str, Any]:
    for case in suite.get("cases", []):
        for i, step in enumerate(case.get("steps", []), start=1):
            step["step_no"] = i
    return suite


# ------------------------------------------------------------------ listing

_summary_cache: dict[tuple[str, float], dict[str, Any]] = {}


def _summarise(path: Path) -> dict[str, Any]:
    key = (str(path), path.stat().st_mtime)
    if key in _summary_cache:
        return _summary_cache[key]
    info: dict[str, Any] = {"format": "unknown", "suite_id": None, "suite_name": None, "cases": 0, "steps": 0}
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict) and isinstance(data.get("cases"), list):
                info.update(format="testbot", suite_id=data.get("suite_id"), suite_name=data.get("suite_name"),
                            cases=len(data["cases"]), steps=sum(len(c.get("steps", [])) for c in data["cases"] if isinstance(c, dict)))
            elif isinstance(data, dict) and "Elements" in data:
                info.update(format="apriso", suite_id=str(data.get("TestCase")), suite_name=data.get("Description"), cases=1,
                            steps=len(data.get("Elements", [])))
            else:
                info["format"] = "other-json"
        else:
            suite = load_suite_from_excel(path)
            info.update(format="testbot-excel", suite_id=suite.suite_id, suite_name=suite.suite_name, cases=len(suite.cases),
                        steps=sum(len(c.steps) for c in suite.cases))
    except Exception as exc:  # noqa: BLE001 - an unreadable file is listed, flagged, and can be imported with mapping
        info.update(format="unreadable", error=str(exc)[:200])
    _summary_cache[key] = info
    return info


def list_tests(ws: Workspace) -> list[dict[str, Any]]:
    base = ws.dir("test_cases")
    out = []
    if not base.exists():
        return out
    for path in sorted(base.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUITE_EXTENSIONS and not path.name.startswith("~$") and ".testbot-bak" not in path.parts and revisions.DIR_NAME not in path.parts:
            if path.name.endswith(".bak"):
                continue
            st = path.stat()
            out.append({"path": ws.rel("test_cases", path), "name": path.name, "folder": ws.rel("test_cases", path.parent) if path.parent != base else "",
                        "modified": st.st_mtime, "size": st.st_size, **_summarise(path),
                        "history": revisions.quick_counts(base, ws.rel("test_cases", path)) if path.suffix.lower() == ".json" else None})
    return out


# ------------------------------------------------------------------ read / save

def read_test(ws: Workspace, rel: str) -> dict[str, Any]:
    path = ws.safe_path("test_cases", rel)
    if not path.is_file():
        raise WorkspaceError(f"'{rel}' does not exist")
    mtime = path.stat().st_mtime
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
            raise WorkspaceError("This file is not in testbot format. Use Import to map it.")
        out = {"path": rel, "suite": data, "mtime": mtime, "source": "json", "save_as": rel}
        try:
            root = ws.dir("test_cases")
            rel_t = ws.rel("test_cases", path)
            revisions.sync(root, rel_t)                         # a change made outside testbot becomes a revision
            out["revisions"] = revisions.editor_info(root, rel_t)
        except Exception as exc:  # noqa: BLE001 - the history must never stop a file from opening
            out["revisions"] = None
            out["revisions_error"] = str(exc)[:200]
        return out
    suite = clean_suite_dict(load_suite_from_excel(path))
    return {"path": rel, "suite": suite, "mtime": mtime, "source": "excel",
            "save_as": str(Path(rel).with_suffix(".json").as_posix())}


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def save_test(ws: Workspace, rel: str, suite: dict[str, Any], base_mtime: Optional[float] = None, *, overwrite: bool = True,
              source: str = "manual", note: str = "") -> dict[str, Any]:
    """Save `suite` as JSON at `rel` (an .xlsx source is saved as a sibling .json). Steps are renumbered.
    `base_mtime` = the modified time the browser loaded; if the file changed since, raise ConflictError."""
    if not isinstance(suite, dict) or not isinstance(suite.get("cases"), list):
        raise ValueError("a test suite needs a 'cases' list")
    target = Path(rel)
    if target.suffix.lower() != ".json":
        target = target.with_suffix(".json")
    path = ws.safe_path("test_cases", target.as_posix())
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"'{target.as_posix()}' already exists")
        if base_mtime is not None and abs(path.stat().st_mtime - base_mtime) > 0.001:
            raise ConflictError("The file was changed on disk after you opened it. Reload it (or save under another name).")
        backup = path.with_name(path.name + ".bak")
        shutil.copy2(path, backup)  # one-step undo for hand-written files
    normalized = suitefix.normalize_suite(suite)
    info: dict[str, Any] = {}
    try:
        info = revisions.save_default(ws.dir("test_cases"), ws.rel("test_cases", path), renumber(suite), source=source, note=note)   # writes the file too
    except Exception as exc:  # noqa: BLE001 - a problem with the history must never lose the user's save
        _write_json_atomic(path, renumber(suite))
        info = {"revision_error": str(exc)[:200]}
    return {"path": ws.rel("test_cases", path), "mtime": path.stat().st_mtime, "normalized": normalized, "revision": info}


def new_suite(suite_id: str, name: str, base_url: str = "") -> dict[str, Any]:
    suite: dict[str, Any] = {"suite_id": suite_id, "suite_name": name}
    if base_url:
        suite["base_url"] = base_url
    suite["on_case_fail"] = "continue"
    suite["cases"] = [{"id": "TC-001", "title": "New test case", "steps": []}]
    return suite


def create_test(ws: Workspace, rel: str, suite_id: str, name: str, base_url: str = "") -> dict[str, Any]:
    if not rel.lower().endswith(".json"):
        rel += ".json"
    return save_test(ws, rel, new_suite(suite_id, name, base_url), overwrite=False)


def delete_test(ws: Workspace, rel: str) -> None:
    path = ws.safe_path("test_cases", rel)
    if not path.is_file():
        raise WorkspaceError(f"'{rel}' does not exist")
    trash = ws.dir("test_cases") / ".testbot-bak"
    trash.mkdir(exist_ok=True)
    shutil.move(str(path), str(trash / f"{int(time.time())}_{path.name}"))  # recoverable


def duplicate_test(ws: Workspace, rel: str, new_rel: str) -> dict[str, Any]:
    src = read_test(ws, rel)
    suite = src["suite"]
    suite["suite_id"] = f"{suite.get('suite_id', 'SUITE')}-COPY"
    return save_test(ws, new_rel, suite, overwrite=False)


# ------------------------------------------------------------------ validation

def _location(loc: tuple) -> dict[str, Any]:
    where: dict[str, Any] = {}
    if len(loc) >= 2 and loc[0] == "cases":
        where["case"] = loc[1]
        if len(loc) >= 4 and loc[2] == "steps":
            where["step"] = loc[3]
            where["field"] = ".".join(str(x) for x in loc[4:])
        else:
            where["field"] = ".".join(str(x) for x in loc[2:])
    else:
        where["field"] = ".".join(str(x) for x in loc)
    return where


def validate(suite: dict[str, Any]) -> list[dict[str, Any]]:
    """Problems in a suite: level 'error' (the runner cannot run it) or 'warning' (probably wrong).
    Each has 'message' and a location {case, step, field} (0-based indexes; absent = suite level)."""
    problems: list[dict[str, Any]] = []
    try:
        TestSuite.model_validate(copy.deepcopy(suite))
    except ValidationError as exc:
        for err in exc.errors():
            where = _location(tuple(err["loc"]))
            problems.append({"level": "error", "message": (where.get("field", "") + " " if where.get("field") else "") + friendly(err), **where})
    except Exception as exc:  # noqa: BLE001
        problems.append({"level": "error", "message": str(exc)})

    cases = suite.get("cases") if isinstance(suite, dict) else None
    if not isinstance(cases, list):
        return problems + [{"level": "error", "message": "no 'cases' list"}]

    seen_ids: set[str] = set()
    suite_vars = set((suite.get("variables") or {}).keys())
    for ci, case in enumerate(cases):
        if not isinstance(case, dict):
            continue
        cid = case.get("id")
        if cid in seen_ids:
            problems.append({"level": "error", "case": ci, "field": "id", "message": f"duplicate case id '{cid}'"})
        seen_ids.add(cid)
        if not case.get("steps") and not (case.get("objective") or "").strip():
            problems.append({"level": "warning", "case": ci, "field": "objective", "message": "the case has neither steps nor a natural-language objective"})
        known = set(BUILTIN_VARIABLES) | {"run_id"} | suite_vars | set((case.get("variables") or {}).keys())
        for si, step in enumerate(case.get("steps") or []):
            if not isinstance(step, dict):
                continue
            here = {"case": ci, "step": si}
            action = step.get("action")
            target = step.get("target")
            if action in NEEDS_TARGET and not target:
                problems.append({"level": "error", **here, "field": "target", "message": f"'{action}' needs a target"})
            if action == "navigate" and not target and not step.get("input"):
                problems.append({"level": "error", **here, "field": "target", "message": "navigate needs a url target (or an address in Input)"})
            if action in ("sql_query", "sql_exec") and not (step.get("query") or "").strip():
                problems.append({"level": "error", **here, "field": "query", "message": f"'{action}' needs a query"})
            if action == "agent" and not (step.get("input") or "").strip():
                problems.append({"level": "error", **here, "field": "input", "message": "an agent step needs its instruction in Input"})
            if action in NEEDS_CONFIG and not step.get("config"):
                problems.append({"level": "error", **here, "field": "config", "message": f"'{action}' needs a config block"})
            if action == "wait_until" and step.get("input") and not re.match(r"^(hidden|visible|has_value|value:.*|text:.*|text_contains:.*)$", str(step["input"]).strip()):
                problems.append({"level": "error", **here, "field": "input", "message": "wait_until input must be hidden, visible, has_value, value:…, text:… or text_contains:…"})
            exp = step.get("expected")
            if isinstance(exp, dict) and exp.get("type") in EXPECTED_NEEDS_TARGET and not exp.get("target"):
                problems.append({"level": "error", **here, "field": "expected.target", "message": f"check '{exp['type']}' needs a target element"})
            if isinstance(exp, dict) and exp.get("type") == "list_matches" and not isinstance(exp.get("value"), list):
                problems.append({"level": "error", **here, "field": "expected.value", "message": "list_matches needs a list of row entries"})
            # variable references must be defined by something earlier
            texts = [step.get("input"), step.get("query"), (target or {}).get("value") if isinstance(target, dict) else None,
                     (exp or {}).get("value") if isinstance(exp, dict) and isinstance(exp.get("value"), str) else None]
            for text in texts:
                for name in _VAR.findall(text) if isinstance(text, str) else []:
                    if name not in known:
                        problems.append({"level": "warning", **here, "field": "input",
                                         "message": f"variable {{{name}}} is not defined before this step"})
            cap = step.get("capture")
            if isinstance(cap, dict) and cap.get("var"):
                known.add(cap["var"])
            if action == "set_var" and isinstance(cap, dict) and cap.get("var"):
                known.add(cap["var"])
    return problems


def extract_cases(ws: Workspace, rel: str, ids: list[str], new_rel: str) -> dict[str, Any]:
    """Copy some test cases of a file into a NEW test file (the suite settings -- base_url, variables, connections ... -- come along).
    Cases keep the order given; prerequisites (`depends_on`) are copied too, so the new file runs on its own. Never overwrites."""
    from framework.runner.selection import REPEAT_SEPARATOR, select_cases

    if not ids:
        raise ValueError("choose at least one test case to copy")
    src = read_test(ws, rel)["suite"]
    selection = select_cases(TestSuite.model_validate(copy.deepcopy(src)), ids)
    by_id = {c.get("id"): c for c in src.get("cases", [])}
    wanted: list[str] = []
    for case in selection:
        base = case.id.split(REPEAT_SEPARATOR)[0] if REPEAT_SEPARATOR in case.id and case.id not in by_id else case.id
        if base not in wanted:
            wanted.append(base)
    stem = Path(new_rel).stem
    suite = {k: copy.deepcopy(v) for k, v in src.items() if k != "cases"}
    suite["suite_id"] = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).upper()[:40] or "SELECTED"
    suite["suite_name"] = f"{src.get('suite_name') or src.get('suite_id')} - {len(wanted)} selected case(s)"
    suite["cases"] = [copy.deepcopy(by_id[i]) for i in wanted]
    try:
        ws.safe_path("test_cases", new_rel if new_rel.lower().endswith(".json") else new_rel + ".json")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(str(exc))
    saved = save_test(ws, new_rel if new_rel.lower().endswith(".json") else new_rel + ".json", suite, overwrite=False)
    return {**saved, "cases": wanted, "prerequisites_added": [c for c in selection.added]}
