"""Import test cases from files that are not (exactly) in testbot format.

Three routes, all previewed before anything is saved:
  * a file already in testbot format (JSON/Excel)  -> used as is,
  * an Apriso AutomaticTest scenario               -> converted with config/apriso_control_map.yaml,
  * anything else (Excel/CSV table or any JSON)    -> a user-defined MAPPING of source columns/keys onto step fields,
    with optional value maps ("Enter text" -> type), grouping into cases, and defaults."""
from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import yaml

from framework.manager import testcases
from framework.manager.catalog import ACTIONS, EXPECTED, STRATEGIES
from framework.manager.workspace import Workspace, WorkspaceError, app_dir

# testbot step fields a source column can be mapped to
FIELDS = {
    "description": "Step description",
    "context": "Step heading / context (natural-language import)",
    "action": "Action",
    "target_strategy": "Target: how to find (strategy)",
    "target_value": "Target: value / locator",
    "target_name": "Target: accessible name (role)",
    "input": "Input",
    "expected_type": "Check: type",
    "expected_value": "Check: value",
    "expected_target_value": "Check: target element (css)",
    "query": "SQL query",
    "connection": "SQL connection name",
    "timeout_ms": "Timeout (ms)",
    "delay_after_ms": "Delay after (ms)",
    "capture_var": "Capture: variable name",
    "capture_from": "Capture: from",
    "capture_target_value": "Capture: target element (css)",
}
VALUE_MAPPED = ("action", "target_strategy", "expected_type", "capture_from")

_SYNONYMS = {
    "description": ["descriptiondesignsteps", "designstepdescription", "description", "stepdescription", "step", "name", "title", "summary", "teststep", "stepname"],
    "action": ["action", "keyword", "command", "operation", "type", "actiontype", "verb"],
    "context": ["description", "heading", "section", "group", "scenario"],
    "target_strategy": ["targetstrategy", "locatortype", "locatorstrategy", "findby", "by", "strategy", "selectortype"],
    "target_value": ["targetvalue", "locator", "selector", "element", "target", "xpath", "css", "object", "identifier", "locatorvalue"],
    "target_name": ["targetname", "accessiblename"],
    "input": ["input", "data", "value", "testdata", "text", "parameter", "argument", "inputvalue"],
    "expected_type": ["expectedtype", "checktype", "assertion", "verifytype"],
    "expected_value": ["expecteddesignsteps", "expectedresultdesignsteps", "expected", "expectedvalue", "expectedresult", "result", "verify", "checkvalue"],
    "query": ["query", "sql", "sqlquery", "statement"],
    "connection": ["connection", "connectionname", "database"],
    "timeout_ms": ["timeout", "timeoutms", "wait"],
    "delay_after_ms": ["delay", "delayms", "delayafterms", "pause"],
    "capture_var": ["capturevar", "savevariable", "variable", "storeas"],
    "capture_from": ["capturefrom"],
}
_CASE_COLS = {"case": ["testcase", "case", "caseid", "testcaseid", "tcid", "tc", "scenario", "scenarioid", "testid"],
              "title": ["casetitle", "testcasename", "testname", "scenarioname", "testtitle", "casename"]}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


# ------------------------------------------------------------------ reading source files

def load_source(ws: Workspace, req: dict[str, Any]) -> tuple[str, bytes]:
    """(filename, bytes) from either a file in the test-cases folder ('path') or an upload ('filename' + 'content_b64')."""
    if req.get("path"):
        p = ws.safe_path("test_cases", req["path"])
        if not p.is_file():
            raise WorkspaceError(f"'{req['path']}' does not exist")
        return p.name, p.read_bytes()
    if req.get("content_b64") and req.get("filename"):
        return req["filename"], base64.b64decode(req["content_b64"])
    raise WorkspaceError("give either a 'path' or an upload ('filename' and 'content_b64')")


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                out.update(_flatten(v, key + "."))
            elif not isinstance(v, list):
                out[key] = v
    return out


def get_path(obj: Any, path: str) -> Any:
    for token in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        obj = obj[int(token[1:-1])] if token.startswith("[") else obj[token]
    return obj


def _find_arrays(data: Any, prefix: str = "", depth: int = 0) -> list[dict[str, Any]]:
    """Every list of objects in a JSON document (candidates for 'the steps' or 'the cases')."""
    found: list[dict[str, Any]] = []
    if depth > 4:
        return found
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        keys = list(dict.fromkeys(k for x in data for k in _flatten(x)))
        distinct = {k: sorted({str(_flatten(x).get(k)) for x in data if _flatten(x).get(k) not in (None, "")})[:30] for k in keys}
        found.append({"path": prefix or "$", "count": len(data), "keys": keys, "distinct": distinct})
    if isinstance(data, dict):
        for k, v in data.items():
            found += _find_arrays(v, f"{prefix}.{k}" if prefix else k, depth + 1)
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        found += _find_arrays(data[0], f"{prefix}[0]", depth + 1)  # e.g. cases[0].steps
    return found


def _tables(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
    if name.lower().endswith((".csv", ".txt")):
        return {"csv": pd.read_csv(io.BytesIO(raw), header=None, dtype=str, keep_default_na=False, encoding="utf-8-sig", sep=None, engine="python")}
    return pd.read_excel(io.BytesIO(raw), sheet_name=None, header=None, dtype=str, keep_default_na=False)


def _header_and_rows(df: pd.DataFrame, header_row: int) -> tuple[list[str], list[list[str]]]:
    headers = [str(h).strip() or f"Column {i + 1}" for i, h in enumerate(df.iloc[header_row].tolist())]
    rows = [[str(c) for c in r] for r in df.iloc[header_row + 1:].values.tolist()]
    return headers, rows


def _is_boilerplate(cells: list[str], headers: list[str]) -> bool:
    """Template guidance inside the data (ALM/QC exports: a row of '... (Required)' hints, or the header repeated)."""
    texts = [str(c).strip() for c in cells if str(c).strip()]
    if not texts:
        return False
    if any(re.search(r"\((required|optional)\)", t, re.I) for t in texts):
        return True
    hn = {_norm(h) for h in headers}
    return len(texts) >= 2 and sum(1 for t in texts if _norm(t) in hn) >= max(2, len(texts) // 2)


def _guess_header_row(df: pd.DataFrame) -> int:
    for i in range(min(len(df), 15)):
        cells = [_norm(c) for c in df.iloc[i].tolist()]
        if sum(1 for c in cells if c in {s for syn in _SYNONYMS.values() for s in syn}) >= 2:
            return i
    return 0


def suggest_mapping(headers: list[str]) -> dict[str, Any]:
    columns: dict[str, str] = {}
    normalised = {h: _norm(h) for h in headers}
    for field, synonyms in _SYNONYMS.items():
        for syn in synonyms:   # earlier synonyms win, whatever the column order
            hit = next((h for h, n in normalised.items() if n == syn and h not in columns.values()), None)
            if hit:
                columns[field] = hit
                break
    case_col = next((h for h, n in normalised.items() if n in _CASE_COLS["case"] and h not in columns.values()), None)
    title_col = next((h for h, n in normalised.items() if n in _CASE_COLS["title"] and h not in columns.values()), None)
    return {"columns": columns, "case_column": case_col, "case_title_column": title_col}


# ------------------------------------------------------------------ inspect

def inspect(ws: Workspace, req: dict[str, Any]) -> dict[str, Any]:
    name, raw = load_source(ws, req)
    low = name.lower()
    result: dict[str, Any] = {"filename": name}
    if low.endswith(".json"):
        data = json.loads(raw.decode("utf-8-sig"))
        result["kind"] = "json"
        if isinstance(data, dict) and isinstance(data.get("cases"), list):
            result["detected"] = "testbot"
        elif isinstance(data, dict) and isinstance(data.get("Elements"), list):
            result["detected"] = "apriso"
        else:
            result["detected"] = "unknown"
        arrays = _find_arrays(data)
        result["arrays"] = arrays
        best = max(arrays, key=lambda a: len(a["keys"]) * min(a["count"], 5), default=None)
        if best:
            result["suggested"] = {"steps_path": best["path"], **suggest_mapping(best["keys"])}
        result["scalars"] = {k: v for k, v in _flatten(data).items()} if isinstance(data, dict) else {}
        return result
    tables = _tables(name, raw)
    result["kind"] = "table"
    sheets = []
    detected = "unknown"
    for sname, df in tables.items():
        if df.empty:
            continue
        hrow = int(req["header_row"]) if req.get("sheet") == sname and req.get("header_row") is not None else _guess_header_row(df)
        hrow = max(0, min(hrow, len(df) - 1))
        headers, rows = _header_and_rows(df, hrow)
        rows = [r for r in rows if not _is_boilerplate(r, headers)]
        distinct ={h: sorted({r[i] for r in rows if i < len(r) and r[i].strip()})[:30] for i, h in enumerate(headers)}
        suggested = suggest_mapping(headers)
        acol = suggested["columns"].get("action")
        if acol and len(distinct.get(acol, [])) <= 1 and not any(v.strip().lower() in ACTIONS for v in distinct.get(acol, [])):
            suggested["columns"].pop("action")   # e.g. ALM's "Type" column that only ever says MANUAL is not an action column
        sheets.append({"name": sname, "header_row": hrow, "columns": headers, "row_count": len(rows),
                       "preview": rows[:8], "distinct": distinct, "suggested": suggested})
    if any(s["name"] == "Suite" for s in sheets) and any(s["name"] == "Index" for s in sheets):
        detected = "testbot-excel"
    result["detected"] = detected
    result["sheets"] = sheets
    return result


# ------------------------------------------------------------------ convert

def _cell(row: dict[str, str], mapping: dict[str, str], field: str) -> Optional[str]:
    col = mapping.get(field)
    if not col:
        return None
    value = row.get(col)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _to_int(text: Optional[str]) -> Optional[int]:
    try:
        return int(float(text)) if text not in (None, "") else None
    except ValueError:
        return None


def _mapped(value: Optional[str], value_map: dict[str, str]) -> Optional[str]:
    if value is None:
        return None
    lookup = {k.strip().lower(): v for k, v in (value_map or {}).items()}
    return lookup.get(value.strip().lower(), value.strip())


def _build_step(row: dict[str, str], m: dict[str, Any], warnings: list[str], where: str) -> Optional[dict[str, Any]]:
    cols, vmaps, defaults = m.get("columns", {}), m.get("value_maps", {}), m.get("defaults", {})
    action = _mapped(_cell(row, cols, "action"), vmaps.get("action", {})) or defaults.get("action")
    desc = _cell(row, cols, "description")
    if not action and not desc:
        return None
    step: dict[str, Any] = {"description": desc or (action or "step")}
    if action:
        canon = action.strip().lower()
        if canon in ACTIONS:
            action = canon
        else:
            warnings.append(f"{where}: action '{action}' is not a testbot action -- map it, or fix it in the editor")
        step["action"] = action
    tval = _cell(row, cols, "target_value")
    if tval:
        strat = _mapped(_cell(row, cols, "target_strategy"), vmaps.get("target_strategy", {})) or defaults.get("target_strategy") or "css"
        strat = strat.strip().lower()
        if strat not in STRATEGIES:
            warnings.append(f"{where}: target strategy '{strat}' is not a testbot strategy")
        target: dict[str, Any] = {"strategy": strat, "value": tval}
        name = _cell(row, cols, "target_name")
        if name:
            target["name"] = name
        step["target"] = target
    inp = _cell(row, cols, "input")
    if inp is not None:
        step["input"] = inp
    query = _cell(row, cols, "query")
    if query:
        step["query"] = query
    conn = _cell(row, cols, "connection")
    if conn and conn != "default":
        step["connection"] = conn
    for field, key in (("timeout_ms", "timeout_ms"), ("delay_after_ms", "delay_after_ms")):
        n = _to_int(_cell(row, cols, field))
        if n is not None:
            step[key] = n
    etype = _mapped(_cell(row, cols, "expected_type"), vmaps.get("expected_type", {}))
    evalue = _cell(row, cols, "expected_value")
    if etype:
        etype = etype.strip().lower()
        if etype not in EXPECTED:
            warnings.append(f"{where}: check type '{etype}' is not a testbot check")
        exp: dict[str, Any] = {"type": etype}
        if evalue is not None:
            exp["value"] = evalue
        etarget = _cell(row, cols, "expected_target_value")
        if etarget:
            exp["target"] = {"strategy": "css", "value": etarget}
        step["expected"] = exp
    elif evalue is not None:
        warnings.append(f"{where}: an expected value was given but no check type -- pick a check type mapping or default")
    cvar = _cell(row, cols, "capture_var")
    if cvar:
        cap: dict[str, Any] = {"var": cvar}
        cfrom = _mapped(_cell(row, cols, "capture_from"), vmaps.get("capture_from", {})) or defaults.get("capture_from") or "element_text"
        cap["from"] = cfrom
        ctarget = _cell(row, cols, "capture_target_value")
        if ctarget:
            cap["target"] = {"strategy": "css", "value": ctarget}
        step["capture"] = cap
    return step


def _group_rows(rows: list[dict[str, str]], m: dict[str, Any]) -> list[tuple[str, str, list[dict[str, str]]]]:
    case_col, title_col = m.get("case_column"), m.get("case_title_column")
    if not case_col:
        return [(m.get("case_id") or "TC-001", m.get("case_title") or m.get("suite", {}).get("suite_name") or "Imported test case", rows)]
    order: list[str] = []
    grouped: dict[str, list[dict[str, str]]] = {}
    titles: dict[str, str] = {}
    last = ""
    for r in rows:
        key = str(r.get(case_col, "")).strip() or last  # blank = continues the previous case (merged cells)
        if not key:
            continue
        last = key
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(r)
        if title_col and r.get(title_col, "").strip() and key not in titles:
            titles[key] = r[title_col].strip()
    return [(k, titles.get(k, k), grouped[k]) for k in order]


def _sheet_mapping(m: dict[str, Any], headers: list[str], warnings: list[str], sheet: str) -> dict[str, Any]:
    """The shared column mapping, applied to one sheet by column NAME. A field mapped to a column this sheet does not have
    falls back to the sheet's own suggestion (same meaning, different spelling); if there is none, the field is dropped with a warning."""
    cols = dict(m.get("columns", {}))
    own = suggest_mapping(headers)["columns"]
    for field, col in list(cols.items()):
        if col in headers:
            continue
        if field in own:
            cols[field] = own[field]
        else:
            cols.pop(field)
            warnings.append(f"sheet '{sheet}': column '{col}' ({FIELDS.get(field, field)}) not found -- that field is left empty on this sheet")
    return {**m, "columns": cols}


def _case_id(label: str, used: set[str], index: int) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-")[:40] or f"TC-{index:03d}"
    cid, n = base, 2
    while cid in used:
        cid, n = f"{base}-{n}", n + 1
    used.add(cid)
    return cid


NL_INDENT = chr(10) + "    "


def _step_phrase(row: dict[str, str], m: dict[str, Any], n: int) -> tuple[Optional[str], Optional[str]]:
    """One row as natural language: ('3. Click Save (target: #ok; input: abc)', 'Step 3: the order is shown')."""
    cols = m.get("columns", {})
    desc, action = _cell(row, cols, "description"), _cell(row, cols, "action")
    if not desc and not action:
        return None, None
    text = desc or action or ""
    text = NL_INDENT.join(x.strip() for x in text.splitlines() if x.strip())      # a multi-line cell stays under its step number
    ctx = _cell(row, cols, "context")
    if ctx and " ".join(ctx.split()).lower() != " ".join(text.split()).lower():
        text = f"{' '.join(ctx.split())}: {text}"
    details = []
    if action and desc and action.lower() not in desc.lower():
        details.append(f"action: {action}")
    for field, label in (("target_value", "target"), ("input", "input")):
        v = _cell(row, cols, field)
        if v:
            details.append(f"{label}: {v}")
    line = f"{n}. {text}" + (f" ({'; '.join(details)})" if details else "")
    exp = _cell(row, cols, "expected_value")
    return line, (f"Step {n}: {exp}" if exp else None)


def _agentic_case(cid: str, title: str, rows: list[dict[str, str]], m: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """A natural-language case: the rows become the objective (numbered, in order) and the expected results become `expect`."""
    if not m.get("columns", {}).get("description") and not m.get("columns", {}).get("action"):
        warnings.append(f"case {cid}: no 'Step description' (or 'Action') column is mapped -- the objective will be empty")
    lines, expect = [], []
    for r in rows:
        line, exp = _step_phrase(r, m, len(lines) + 1)
        if line:
            lines.append(line)
            if exp:
                expect.append(exp)
    objective = ("Carry out these steps in order and check the result of each:\n" + "\n".join(lines)) if lines else ""
    case: dict[str, Any] = {"id": cid, "title": title, "objective": objective, "steps": []}
    if expect:
        case["expect"] = expect
    return case


def convert(ws: Workspace, req: dict[str, Any]) -> dict[str, Any]:
    """Build a testbot suite from the source + mapping. Returns {suite, warnings, problems}; nothing is saved."""
    name, raw = load_source(ws, req)
    m = req.get("mapping") or {}
    warnings: list[str] = []
    kind = m.get("kind")

    if kind == "testbot":
        suite = json.loads(raw.decode("utf-8-sig")) if name.lower().endswith(".json") else None
        if suite is None:
            from framework.loaders.excel_loader import load_suite_from_excel
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=Path(name).suffix, delete=False) as tmp:
                tmp.write(raw)
            try:
                suite = testcases.clean_suite_dict(load_suite_from_excel(Path(tmp.name)))
            finally:
                Path(tmp.name).unlink(missing_ok=True)
    elif kind == "apriso":
        from framework.converters.apriso_bpa import convert as convert_apriso
        map_path = next((p for p in (ws.dir("config") / "apriso_control_map.yaml", app_dir() / "config" / "apriso_control_map.yaml") if p.exists()), None)
        if map_path is None:
            raise WorkspaceError("config/apriso_control_map.yaml not found (needed to convert Apriso scenarios)")
        suite, warnings = convert_apriso(json.loads(raw.decode("utf-8-sig")), yaml.safe_load(map_path.read_text(encoding="utf-8")))
    elif kind in ("table", "json"):
        agentic = m.get("style") == "agentic"
        used: set[str] = set()
        groups: list[tuple[str, str, list[dict[str, str]], dict[str, Any]]] = []   # (case id, title, rows, mapping for those rows)
        if kind == "table":
            tables = _tables(name, raw)
            wanted = [x for x in (m.get("sheets") or [m.get("sheet")]) if x in tables] or [next(iter(tables))]
            for si, sname in enumerate(wanted, start=1):
                df = tables[sname]
                if df.empty:
                    warnings.append(f"sheet '{sname}' is empty -- skipped")
                    continue
                hrow = (m.get("header_rows") or {}).get(sname, m.get("header_row"))
                hrow = _guess_header_row(df) if hrow is None else max(0, min(int(hrow), len(df) - 1))
                headers, data_rows = _header_and_rows(df, hrow)
                rows = [dict(zip(headers, r)) for r in data_rows if any(str(c).strip() for c in r) and not _is_boilerplate(r, headers)]
                sm = _sheet_mapping(m, headers, warnings, sname) if len(wanted) > 1 else m
                if m.get("case_per_sheet"):
                    tcol = sm.get("columns", {}).get("__title") or m.get("case_title_column")
                    tcol = tcol if tcol in headers else suggest_mapping(headers)["case_title_column"] if tcol else None
                    title = next((r[tcol].strip() for r in rows if tcol and r.get(tcol, "").strip()), "") or sname
                    groups.append((_case_id(sname, used, si), title, rows, sm))
                else:
                    groups += [(cid, title, grows, sm) for cid, title, grows in _group_rows(rows, m)]
        else:
            data = json.loads(raw.decode("utf-8-sig"))
            arr = data if m.get("steps_path") in (None, "", "$") else get_path(data, m["steps_path"])
            rows = [{k: ("" if v is None else str(v)) for k, v in _flatten(x).items()} for x in arr]
            groups = [(cid, title, grows, m) for cid, title, grows in _group_rows(rows, m)]
        suite_meta = m.get("suite", {})
        cases = []
        if not agentic and not m.get("columns", {}).get("action") and not m.get("defaults", {}).get("action"):
            warnings.append("No 'Action' column is mapped, so the rows cannot become script steps (a script step needs an action). "
                            "If these are manual test steps written in plain language, import them as a natural-language (agentic) test case instead.")
        for cid, title, grows, gm in groups:
            if agentic:
                cases.append(_agentic_case(cid, title, grows, gm, warnings))
                continue
            steps = []
            for i, r in enumerate(grows, start=1):
                st = _build_step(r, gm, warnings, f"case {cid} row {i}")
                if st:
                    steps.append(st)
            cases.append({"id": cid, "title": title, "steps": steps})
        suite = {"suite_id": suite_meta.get("suite_id") or Path(name).stem.upper()[:24] or "IMPORTED",
                 "suite_name": suite_meta.get("suite_name") or Path(name).stem}
        if suite_meta.get("base_url"):
            suite["base_url"] = suite_meta["base_url"]
        if agentic:
            suite["mode"] = "agentic"
        suite["on_case_fail"] = "continue"
        suite["cases"] = cases
    else:
        raise WorkspaceError("mapping.kind must be one of: testbot, apriso, table, json")

    testcases.renumber(suite)
    return {"suite": suite, "warnings": warnings, "problems": testcases.validate(suite)}
