from __future__ import annotations

import sys
import types

import pytest

from framework.manager import dataaccess
from framework.manager.dataaccess import check_select_only

BYPASSES = [
    "SELECT 1 SHUTDOWN WITH NOWAIT",
    "SELECT 1 KILL 53",
    "SELECT 1 WAITFOR DELAY '00:10:00'",
    "WAITFOR DELAY '00:10:00'",
    "SELECT 1 BACKUP DATABASE APRISO TO DISK='C:\\x.bak'",
    "SELECT 1 RESTORE DATABASE APRISO FROM DISK='C:\\x.bak'",
    "SELECT * FROM OPENQUERY(LNK, 'DELETE FROM dbo.Orders')",
    "SELECT * FROM OPENROWSET('SQLNCLI', 'Server=x;Trusted_Connection=yes;', 'SELECT 1')",
    "SELECT * FROM OPENDATASOURCE('SQLNCLI', 'Data Source=x').db.dbo.t",
    "SELECT 1 DBCC CHECKDB",
    "SELECT 1 EXEC xp_cmdshell 'dir'",
    "SELECT 1 exec('DELETE FROM t')",
    "SELECT * FROM t; DROP TABLE t",
    "SELECT * INTO copy FROM t",
    "SELECT 1 DECLARE @x int",
    "SELECT 1 SET NOCOUNT ON",
    "SELECT 1 /* hide */ SHUTDOWN",
    "SELECT 1 --\nSHUTDOWN",
    "SELECT name FROM sys.objects UPDATE t SET a = 1",
    "SELECT 1 BEGIN TRAN",
    "SELECT sp_who",
    "DELETE FROM t",
    "UPDATE t SET a = 1",
    "",
    "   ;  ",
    "SELECT 'unterminated",
]


@pytest.mark.parametrize("query", BYPASSES)
def test_rejects_writes_and_bypasses(query):
    with pytest.raises(ValueError):
        check_select_only(query)


@pytest.mark.parametrize("query", [
    "SELECT 1",
    "select top 10 * from dbo.Orders where Status = 'DELETE ME; SHUTDOWN'",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SELECT [Update], [Set] FROM dbo.t",
    'SELECT "Delete" FROM t',
    "-- a comment ; with words like DROP\nSELECT 1",
    "SELECT COUNT(*) FROM ORDERS WHERE OrderNo = 'O''Brien'",
    "SELECT a FROM t ORDER BY a OFFSET 0 ROWS FETCH NEXT 5 ROWS ONLY",
    "SELECT UpdatedOn, CreatedBy, DataSet FROM t;",
])
def test_accepts_plain_selects(query):
    assert check_select_only(query)


class _FakeCursor:
    description = [("a",)]

    def __init__(self, conn):
        self.conn = conn

    def execute(self, q, *params):
        self.conn.executed.append((q, params))

    def fetchmany(self, n):
        return [(1,)]


class _FakeConn:
    def __init__(self):
        self.executed, self.rolled_back, self.closed, self.timeout = [], False, False, None

    def cursor(self):
        return _FakeCursor(self)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_run_select_uses_a_transaction_that_is_rolled_back(monkeypatch):
    calls = {}
    conn = _FakeConn()

    def connect(cs, **kw):
        calls.update(kw, cs=cs)
        return conn

    monkeypatch.setitem(sys.modules, "pyodbc", types.SimpleNamespace(connect=connect))
    monkeypatch.setenv("TESTBOT_DB_PW", "s3cret")
    out = dataaccess.run_select("Driver=x;PWD={env:TESTBOT_DB_PW};", "SELECT 1 AS a")
    assert out["columns"] == ["a"]
    assert calls["autocommit"] is False
    assert calls["cs"] == "Driver=x;PWD=s3cret;"
    assert conn.rolled_back and conn.closed


def test_run_select_refuses_before_connecting(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyodbc", types.SimpleNamespace(connect=lambda *a, **k: pytest.fail("connected")))
    with pytest.raises(ValueError):
        dataaccess.run_select("Driver=x;", "SELECT 1 SHUTDOWN WITH NOWAIT")
