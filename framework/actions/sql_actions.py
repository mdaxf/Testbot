from __future__ import annotations

import time
from typing import Any

import pyodbc

from framework import logs

log = logs.get("sql")


class SqlConnections:
    """Lazily opens and caches one ODBC connection per named connection string
    for the current environment (see config/environments.yaml)."""

    def __init__(self, connection_strings: dict[str, str]):
        self._defaults = dict(connection_strings)
        self._connection_strings = dict(connection_strings)
        self._open: dict[str, pyodbc.Connection] = {}

    def use_overrides(self, overrides: dict[str, str]) -> None:
        """Layer a suite's own connection strings over the environment's (suite wins).
        Replaces any previous overrides, so one file's settings never leak into the next."""
        merged = {**self._defaults, **overrides}
        for name in [n for n in self._open if merged.get(n) != self._connection_strings.get(n)]:
            self._open.pop(name).close()
        self._connection_strings = merged

    def get(self, name: str = "default") -> pyodbc.Connection:
        if name not in self._connection_strings:
            raise KeyError(f"No SQL connection named '{name}' configured for this environment")
        if name not in self._open:
            t0 = time.monotonic()
            try:
                self._open[name] = pyodbc.connect(self._connection_strings[name], autocommit=True)
            except Exception as exc:
                log.error("connection '%s' failed: %s", name, logs.clip(logs.redact(exc), 300))     # never the connection string: it holds the password
                raise
            log.debug("connection '%s' opened (%d ms)", name, int((time.monotonic() - t0) * 1000))
        return self._open[name]

    def close_all(self) -> None:
        for conn in self._open.values():
            conn.close()
        self._open.clear()


def run_query(conn: pyodbc.Connection, sql: str) -> list[dict[str, Any]]:
    t0 = time.monotonic()
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        columns = [col[0] for col in cursor.description] if cursor.description else []
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as exc:
        log.warning("query failed: %s -- %s", logs.clip(logs.redact(sql), 200), logs.clip(logs.redact(exc), 300))
        raise
    finally:
        cursor.close()
    log.debug("query: %s -> %d row(s) (%d ms)", logs.clip(logs.redact(sql), 300), len(rows), int((time.monotonic() - t0) * 1000))
    return rows


def run_exec(conn: pyodbc.Connection, sql: str) -> int:
    t0 = time.monotonic()
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        affected = cursor.rowcount
    except Exception as exc:
        log.warning("statement failed: %s -- %s", logs.clip(logs.redact(sql), 200), logs.clip(logs.redact(exc), 300))
        raise
    finally:
        cursor.close()
    log.info("statement executed: %s -> %d row(s) affected (%d ms)", logs.clip(logs.redact(sql), 200), affected, int((time.monotonic() - t0) * 1000))
    return affected
