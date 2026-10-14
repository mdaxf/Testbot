from __future__ import annotations

import time
from typing import Any, Optional, Sequence

import pyodbc

from framework import connstr, logs

log = logs.get("sql")

CONNECT_TIMEOUT_S = 15      # login timeout; without it an unreachable server hangs the case (and its schedule)


class SqlConnections:
    """Lazily opens and caches one ODBC connection per named connection string
    for the current environment (see config/environments.yaml)."""

    def __init__(self, connection_strings: dict[str, str]):
        self._defaults = dict(connection_strings)
        self._connection_strings = dict(connection_strings)
        self._open: dict[str, pyodbc.Connection] = {}
        self._readonly: dict[str, pyodbc.Connection] = {}    # separate, never-committed connections for read-only lookups (the agent)

    def use_overrides(self, overrides: dict[str, str]) -> None:
        """Layer a suite's own connection strings over the environment's (suite wins).
        Replaces any previous overrides, so one file's settings never leak into the next."""
        merged = {**self._defaults, **overrides}
        for name in [n for n in self._open if merged.get(n) != self._connection_strings.get(n)]:
            self._open.pop(name).close()
        for name in [n for n in self._readonly if merged.get(n) != self._connection_strings.get(n)]:
            self._readonly.pop(name).close()
        self._connection_strings = merged

    def get(self, name: str = "default") -> pyodbc.Connection:
        if name not in self._connection_strings:
            raise KeyError(f"No SQL connection named '{name}' configured for this environment")
        if name not in self._open:
            t0 = time.monotonic()
            try:
                self._open[name] = pyodbc.connect(connstr.expand(self._connection_strings[name]), autocommit=True, timeout=CONNECT_TIMEOUT_S)
            except Exception as exc:
                log.error("connection '%s' failed: %s", name, logs.clip(logs.redact(exc), 300))     # never the connection string: it holds the password
                raise
            log.debug("connection '%s' opened (%d ms)", name, int((time.monotonic() - t0) * 1000))
        return self._open[name]

    def get_readonly(self, name: str = "default") -> pyodbc.Connection:
        """A second connection to the same database with autocommit off. Use it with run_query(..., rollback=True)
        so nothing it runs is ever committed (for read-only lookups such as the agent's query_db)."""
        if name not in self._connection_strings:
            raise KeyError(f"No SQL connection named '{name}' configured for this environment")
        if name not in self._readonly:
            try:
                self._readonly[name] = pyodbc.connect(connstr.expand(self._connection_strings[name]), autocommit=False, timeout=CONNECT_TIMEOUT_S)
            except Exception as exc:
                log.error("connection '%s' (read-only) failed: %s", name, logs.clip(logs.redact(exc), 300))
                raise
        return self._readonly[name]

    def close_all(self) -> None:
        for conn in [*self._open.values(), *self._readonly.values()]:
            conn.close()
        self._open.clear()
        self._readonly.clear()


def _execute(cursor, sql: str, params: Optional[Sequence[Any]]) -> None:
    if params:
        cursor.execute(sql, list(params))
    else:
        cursor.execute(sql)


def run_query(conn: pyodbc.Connection, sql: str, params: Optional[Sequence[Any]] = None, *, rollback: bool = False) -> list[dict[str, Any]]:
    """Run a query and return its rows. `params` are bound to the `?` markers in `sql`; `rollback=True` rolls the
    transaction back afterwards (for a connection from SqlConnections.get_readonly)."""
    t0 = time.monotonic()
    cursor = conn.cursor()
    try:
        _execute(cursor, sql, params)
        columns = [col[0] for col in cursor.description] if cursor.description else []
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as exc:
        log.warning("query failed: %s -- %s", logs.clip(logs.redact(sql), 200), logs.clip(logs.redact(exc), 300))
        raise
    finally:
        cursor.close()
        if rollback:
            conn.rollback()
    log.debug("query: %s -> %d row(s) (%d ms)", logs.clip(logs.redact(sql), 300), len(rows), int((time.monotonic() - t0) * 1000))
    return rows


def run_exec(conn: pyodbc.Connection, sql: str, params: Optional[Sequence[Any]] = None) -> int:
    t0 = time.monotonic()
    cursor = conn.cursor()
    try:
        _execute(cursor, sql, params)
        affected = cursor.rowcount
    except Exception as exc:
        log.warning("statement failed: %s -- %s", logs.clip(logs.redact(sql), 200), logs.clip(logs.redact(exc), 300))
        raise
    finally:
        cursor.close()
    log.info("statement executed: %s -> %d row(s) affected (%d ms)", logs.clip(logs.redact(sql), 200), affected, int((time.monotonic() - t0) * 1000))
    return affected
