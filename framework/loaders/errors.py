"""Turn pydantic's validation dump into sentences a tester can act on:

    case 1702637 (#2), step 2 "Enter username and password…": target strategy: 'name' is not supported (use one of: role, label, text, ...)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError


def friendly(err: dict[str, Any]) -> str:
    """One pydantic error as a short phrase (without the location)."""
    kind = err.get("type")
    if kind == "literal_error":
        allowed = (err.get("ctx") or {}).get("expected", "")
        return f"'{err.get('input')}' is not supported (use one of: {allowed})"
    if kind == "missing":
        return "is required but missing"
    if kind in ("string_type", "int_parsing", "bool_parsing", "list_type", "dict_type"):
        return f"has the wrong type ({err.get('msg')})"
    return str(err.get("msg", "invalid"))


def _where(loc: tuple, data: Optional[dict[str, Any]]) -> tuple[str, str]:
    """('case 1702637 (#2), step 2 "…"', 'target strategy') from a pydantic location."""
    parts = list(loc)
    head: list[str] = []
    rest = parts
    if len(parts) >= 2 and parts[0] == "cases" and isinstance(parts[1], int):
        i = parts[1]
        case = (data or {}).get("cases", [])[i] if data and i < len(data.get("cases", [])) else {}
        head.append(f"case {case.get('id', i + 1)} (#{i + 1})")
        rest = parts[2:]
        if len(rest) >= 2 and rest[0] == "steps" and isinstance(rest[1], int):
            j = rest[1]
            steps = case.get("steps", []) if isinstance(case, dict) else []
            st = steps[j] if j < len(steps) else {}
            desc = str(st.get("description", ""))
            head.append(f"step {st.get('step_no', j + 1)}" + (f' "{desc[:40]}{"…" if len(desc) > 40 else ""}"' if desc else ""))
            rest = rest[2:]
    return ", ".join(head) or "the suite", " ".join(str(p) for p in rest)


def explain(exc: ValidationError, data: Optional[dict[str, Any]] = None) -> list[str]:
    out: list[str] = []
    for err in exc.errors():
        where, field = _where(tuple(err.get("loc", ())), data)
        line = f"{where}: {field + ' ' if field else ''}{friendly(err)}"
        if line not in out:
            out.append(line)
    return out


def explain_file(path: str | Path, exc: BaseException) -> list[str]:
    """The problems of a suite file that failed to load, one line each (falls back to the plain message)."""
    if isinstance(exc, ValidationError):
        data = None
        try:
            if Path(path).suffix.lower() == ".json":
                data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            data = None
        return explain(exc, data)
    return [str(exc)]
