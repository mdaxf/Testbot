from __future__ import annotations

import pytest

from framework.actions import sql_actions
from framework.variables.context import MissingVariableError, VariableContext


def ctx(**values):
    return VariableContext(values)


def test_quoted_placeholder_becomes_one_parameter():
    sql, params = ctx(OrderNo="O'Brien'; DROP TABLE t --").resolve_sql("SELECT COUNT(*) FROM ORDERS WHERE OrderNo = '{OrderNo}'")
    assert sql == "SELECT COUNT(*) FROM ORDERS WHERE OrderNo = ?"
    assert params == ["O'Brien'; DROP TABLE t --"]


def test_literal_with_text_around_placeholder():
    sql, params = ctx(n="42", name="x").resolve_sql("SELECT 1 WHERE a = 'ORD-{n}' AND b LIKE N'%{name}%' AND c = 'it''s'")
    assert sql == "SELECT 1 WHERE a = ? AND b LIKE ? AND c = 'it''s'"
    assert params == ["ORD-42", "%x%"]


def test_bare_placeholder_is_bound_unless_a_number():
    sql, params = ctx(id="5 OR 1=1", n=10, neg="-3").resolve_sql("SELECT TOP {n} * FROM t WHERE id = {id} AND x = {neg}")
    assert sql == "SELECT TOP 10 * FROM t WHERE id = ? AND x = (-3)"
    assert params == ["5 OR 1=1"]


def test_identifier_and_raw_placeholders():
    c = ctx(t="Orders", bad="x]; DROP TABLE y --")
    assert c.resolve_sql("SELECT * FROM [{t}] JOIN {raw:t} o ON 1=1") == ("SELECT * FROM [Orders] JOIN Orders o ON 1=1", [])
    with pytest.raises(ValueError):
        c.resolve_sql("SELECT * FROM [{bad}]")


def test_comments_and_plain_sql_unchanged():
    sql = "SELECT 1 -- '{x}' it's\n/* {y} */"
    assert ctx().resolve_sql(sql) == (sql, [])


def test_missing_variable():
    with pytest.raises(MissingVariableError):
        ctx().resolve_sql("SELECT '{nope}'")


class _Cur:
    description = [("a",)]
    rowcount = 1

    def __init__(self, log):
        self.log = log

    def execute(self, *args):
        self.log.append(args)

    def fetchall(self):
        return [(1,)]

    def close(self):
        pass


class _Conn:
    def __init__(self):
        self.log, self.rollbacks = [], 0

    def cursor(self):
        return _Cur(self.log)

    def rollback(self):
        self.rollbacks += 1


def test_run_query_and_exec_pass_params():
    conn = _Conn()
    assert sql_actions.run_query(conn, "SELECT ? AS a", ["v"]) == [{"a": 1}]
    sql_actions.run_query(conn, "SELECT 1 AS a")
    sql_actions.run_exec(conn, "UPDATE t SET a = ?", ["v"])
    assert conn.log == [("SELECT ? AS a", ["v"]), ("SELECT 1 AS a",), ("UPDATE t SET a = ?", ["v"])]
    assert conn.rollbacks == 0
    sql_actions.run_query(conn, "SELECT 1 AS a", rollback=True)
    assert conn.rollbacks == 1


def test_sql_connections_readonly_and_timeout(monkeypatch):
    opened = []
    monkeypatch.setattr(sql_actions.pyodbc, "connect", lambda cs, **kw: opened.append((cs, kw)) or _Conn())
    conns = sql_actions.SqlConnections({"default": "Driver=x;"})
    conns.get()
    conns.get_readonly()
    assert opened[0][1]["autocommit"] is True and opened[0][1]["timeout"] == sql_actions.CONNECT_TIMEOUT_S
    assert opened[1][1]["autocommit"] is False and opened[1][1]["timeout"] == sql_actions.CONNECT_TIMEOUT_S
