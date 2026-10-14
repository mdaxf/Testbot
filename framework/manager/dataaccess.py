"""Support for the Variables & SQL screen: try a SELECT safely, and read/write config/environments.yaml."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from framework import connstr
from framework.manager.workspace import Workspace, WorkspaceError

# Words that have no place in a single read-only SELECT. T-SQL runs several statements in one batch without ';'
# (e.g. "SELECT 1 SHUTDOWN WITH NOWAIT"), so statement keywords are refused wherever they appear outside string
# literals, comments and quoted identifiers. This is a second line of defence only: the real control is a database
# login that can only read (see docs/USER_MANUAL.md, "Database steps").
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|exec|execute|merge|grant|revoke|deny|into|"
    r"shutdown|kill|waitfor|backup|restore|dbcc|reconfigure|checkpoint|bulk|"
    r"openquery|openrowset|opendatasource|openxml|"
    r"declare|set|use|begin|commit|rollback|save|transaction|tran|"
    r"goto|while|if|return|print|raiserror|throw|revert|setuser|"
    r"readtext|writetext|updatetext|xp_\w+|sp_\w+)\b",
    re.I,
)
MAX_ROWS = 25


def _code_only(query: str) -> str:
    """The query with string literals, quoted identifiers and comments blanked out, so the checks below only see
    SQL keywords (a word inside 'text', [brackets], "quotes" or a comment cannot be executed)."""
    out: list[str] = []
    i, n = 0, len(query)
    while i < n:
        ch = query[i]
        if ch == "'":                                       # 'string' with '' escapes
            j = i + 1
            while j < n:
                if query[j] == "'":
                    if j + 1 < n and query[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise ValueError("unterminated string literal")
            out.append("''")
            i = j + 1
        elif ch in "[\"":                                  # [identifier] / "identifier"
            close = "]" if ch == "[" else '"'
            j = query.find(close, i + 1)
            if j < 0:
                raise ValueError("unterminated quoted identifier")
            out.append(" _q_ ")
            i = j + 1
        elif query.startswith("--", i):                     # line comment
            j = query.find("\n", i)
            out.append(" ")
            i = n if j < 0 else j + 1
        elif query.startswith("/*", i):                     # block comment
            j = query.find("*/", i + 2)
            if j < 0:
                raise ValueError("unterminated comment")
            out.append(" ")
            i = j + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def check_select_only(query: str) -> str:
    """Refuse anything but one read-only SELECT (or WITH ... SELECT). Returns the query to run."""
    q = (query or "").strip().rstrip(";").strip()
    if not q:
        raise ValueError("the query is empty")
    code = _code_only(q).strip()
    if ";" in code:
        raise ValueError("only a single statement is allowed")
    if not re.match(r"^(select|with)\b", code, re.I):
        raise ValueError("only SELECT queries can be tried here")
    found = _FORBIDDEN.search(code)
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
    conn = pyodbc.connect(connstr.expand(connection_string), timeout=10, autocommit=False)   # inside a transaction that is always rolled back
    try:
        conn.timeout = 15
        cur = conn.cursor()
        cur.execute(q)
        columns = [c[0] for c in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        return {"columns": columns, "rows": [["" if v is None else str(v) for v in r] for r in rows[:MAX_ROWS]], "truncated": truncated}
    finally:
        try:
            conn.rollback()
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


def masked_environments(envs: dict[str, Any]) -> dict[str, Any]:
    """A copy of the environments for the browser: connection-string passwords replaced by ***."""
    out: dict[str, Any] = {}
    for name, env in (envs or {}).items():
        if isinstance(env, dict) and isinstance(env.get("connections"), dict):
            env = {**env, "connections": {cn: mask_connection(cs) if isinstance(cs, str) else cs for cn, cs in env["connections"].items()}}
        out[name] = env
    return out


def find_stored_connection(envs: dict[str, Any], masked: str, env_name: str = "", conn_name: str = "") -> str:
    """The stored connection string that `masked` (as shown in the browser) stands for: the named one if it matches, else any
    stored string with exactly the same masked form. Returns `masked` unchanged when it holds no hidden password."""
    if not connstr.is_masked(masked):
        return masked
    named = ((envs.get(env_name) or {}).get("connections") or {}).get(conn_name) if env_name else None
    candidates = [named] + [cs for env in envs.values() if isinstance(env, dict) for cs in (env.get("connections") or {}).values()]
    for cs in candidates:
        if isinstance(cs, str) and mask_connection(cs) == masked:
            return cs
    raise ValueError("the connection string has a hidden password (***): type the password again, or save the environment first")


def write_environments(ws: Workspace, envs: dict[str, Any]) -> None:
    if not isinstance(envs, dict):
        raise ValueError("environments must be a mapping of name -> {base_url, connections}")
    for name, env in envs.items():
        if not isinstance(env, dict):
            raise ValueError(f"environment '{name}' must be a mapping")
        if "connections" in env and not isinstance(env["connections"], dict):
            raise ValueError(f"environment '{name}': connections must be a mapping of name -> connection string")
        if env.get("email"):
            from framework import emailer

            problems = emailer.check_block(env["email"], require_host=False)
            if problems:
                raise ValueError(f"environment '{name}': " + "; ".join(problems))
    old = read_environments(ws)
    for name, env in envs.items():               # a password the browser only saw as *** keeps its stored value
        for cn, cs in list((env.get("connections") or {}).items()):
            if isinstance(cs, str) and connstr.is_masked(cs):
                try:
                    env["connections"][cn] = connstr.unmask(cs, ((old.get(name) or {}).get("connections") or {}).get(cn))
                except ValueError:
                    try:                                  # renamed environment / connection: the same string stored under another name
                        env["connections"][cn] = find_stored_connection(old, cs)
                    except ValueError as exc:
                        raise ValueError(f"environment '{name}', connection '{cn}': {exc}") from None
    p = _env_file(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        (p.with_name(p.name + ".bak")).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    p.write_text(yaml.safe_dump(envs, sort_keys=False, allow_unicode=True), encoding="utf-8")


def mask_connection(cs: str) -> str:
    return connstr.mask(cs)
