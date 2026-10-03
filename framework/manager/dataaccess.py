"""Support for the Variables & SQL screen: try a SELECT safely, and read/write config/environments.yaml."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from framework.manager.workspace import Workspace, WorkspaceError

_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|exec|execute|merge|grant|revoke|into|xp_\w+|sp_\w+)\b", re.I)
MAX_ROWS = 25


def check_select_only(query: str) -> str:
    q = query.strip().rstrip(";").strip()
    if not q:
        raise ValueError("the query is empty")
    if ";" in q:
        raise ValueError("only a single statement is allowed")
    if not re.match(r"^(select|with)\b", q, re.I):
        raise ValueError("only SELECT queries can be tried here")
    q_no_strings = re.sub(r"'(?:[^']|'')*'", "''", q)          # ignore words inside string literals
    found = _FORBIDDEN.search(q_no_strings)
    if found:
        raise ValueError(f"'{found.group(1)}' is not allowed in a try-out query (read-only)")
    return q


def run_select(connection_string: str, query: str) -> dict[str, Any]:
    q = check_select_only(query)
    if not connection_string.strip():
        raise ValueError("no connection string")
    try:
        import pyodbc
    except ImportError as exc:  # pragma: no cover
        raise WorkspaceError("pyodbc is not available in this build") from exc
    conn = pyodbc.connect(connection_string, timeout=10, autocommit=True)
    try:
        conn.timeout = 15
        cur = conn.cursor()
        cur.execute(q)
        columns = [c[0] for c in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        return {"columns": columns, "rows": [["" if v is None else str(v) for v in r] for r in rows[:MAX_ROWS]], "truncated": truncated}
    finally:
        conn.close()


def odbc_drivers() -> list[str]:
    try:
        import pyodbc

        return [d for d in pyodbc.drivers() if "SQL Server" in d or "Oracle" in d]
    except Exception:  # noqa: BLE001
        return []


# ------------------------------------------------------------------ environments.yaml

def _env_file(ws: Workspace) -> Path:
    return ws.dir("config") / "environments.yaml"


def read_environments(ws: Workspace) -> dict[str, Any]:
    p = _env_file(ws)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def write_environments(ws: Workspace, envs: dict[str, Any]) -> None:
    if not isinstance(envs, dict):
        raise ValueError("environments must be a mapping of name -> {base_url, connections}")
    for name, env in envs.items():
        if not isinstance(env, dict):
            raise ValueError(f"environment '{name}' must be a mapping")
        if "connections" in env and not isinstance(env["connections"], dict):
            raise ValueError(f"environment '{name}': connections must be a mapping of name -> connection string")
    p = _env_file(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        (p.with_name(p.name + ".bak")).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    p.write_text(yaml.safe_dump(envs, sort_keys=False, allow_unicode=True), encoding="utf-8")


def mask_connection(cs: str) -> str:
    return re.sub(r"(?i)(pwd|password)=([^;]*)", r"\1=***", cs or "")
