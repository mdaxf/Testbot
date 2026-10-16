"""Regression tests for the findings in issue #4 (re-review of main at b62dba9)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from framework.agent.actions import ActionRecord, ToolError
from framework.agent.observer import ElementRef, Observer
from framework.manager import dataaccess
from framework.manager.dataaccess import check_select_only
from framework.manager.groups import _excel_cell
from framework.models import TestSuite as SuiteModel
from framework.runner.selection import SelectionError, check_report_id
from framework.variables.context import VariableContext

sys.path.insert(0, str(Path(__file__).parent))
from test_agent_guards import _Handle, _executor  # noqa: E402
from test_server import api  # noqa: E402,F401 - the fixture


# ---------------------------------------------------------------- 1. agent: allowed host before the action, frames
class _Field(_Handle):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.filled = None

    def fill(self, text, timeout=None):
        self.filled = text


class _Popup:
    def __init__(self, url):
        self.url = url
        self.closed = False

    def close(self):
        self.closed = True


def _rec(tool="type"):
    return ActionRecord(1, tool, {}, True, "")


def test_secret_is_not_typed_on_a_disallowed_host():
    field = _Field(name="Password", typ="password")
    ref = ElementRef("e1", None, None, {"id": "e1", "tag": "input", "type": "password", "name": "Password"}, field)
    ex = _executor({"e1": ref}, VariableContext({"password": "fixture-pw"}))
    ex.page.url = "https://evil.example/phish"            # an earlier click left the app
    with pytest.raises(ToolError, match="Nothing was done"):
        ex.t_type({"element": "e1", "text": "{password}"}, _rec())
    assert field.filled is None


def test_click_is_not_done_while_a_popup_is_on_a_disallowed_host():
    handle = _Handle(name="Save")
    ref = ElementRef("e1", None, None, {"id": "e1", "name": "Save", "role": "button"}, handle)
    ex = _executor({"e1": ref})
    popup = _Popup("https://evil.example/ad")
    ex.page.pages = [ex.page, popup]
    with pytest.raises(ToolError, match="Nothing was done"):
        ex.t_click({"element": "e1"}, _rec("click"))
    assert handle.clicked is False and popup.closed is True


def test_main_page_is_taken_back_off_a_disallowed_host():
    ex = _executor({})
    ex.page.url = "https://evil.example/phish"
    ex.page.go_back = lambda timeout=None: setattr(ex.page, "url", "https://app.example/home")
    with pytest.raises(ToolError):
        ex.t_press({"key": "Enter"}, _rec("press"))
    assert ex.page.url == "https://app.example/home" and ex.location_problem() is None


def test_element_in_a_disallowed_frame_is_refused():
    field = _Field(name="Card")
    frame = SimpleNamespace(url="https://evil.example/frame")
    ref = ElementRef("e1", frame, "iframe", {"id": "e1", "tag": "input", "type": "text", "name": "Card"}, field)
    ex = _executor({"e1": ref}, VariableContext({"card": "4111"}))
    with pytest.raises(ToolError, match="frame"):
        ex.t_type({"element": "e1", "text": "{card}"}, _rec())
    assert field.filled is None


class _Frame:
    def __init__(self, url, parent=None):
        self.url, self.parent_frame = url, parent

    def frame_element(self):
        return SimpleNamespace(bounding_box=lambda: {"width": 100, "height": 100}, evaluate=lambda js: "iframe#x")


def _observer(page_url, frames, url_problem):
    main = _Frame(page_url)
    children = [_Frame(u, main) for u in frames]
    page = SimpleNamespace(main_frame=main, frames=[main, *children], url=page_url)
    return Observer(page, SimpleNamespace(frame=None), SimpleNamespace(settle=lambda: None), url_problem=url_problem), children


def _problem(url):
    return None if "app.example" in url else f"'{url}' is not allowed"


def test_frames_from_disallowed_sites_are_not_observed():
    obs, (ok, bad) = _observer("https://app.example/", ["https://app.example/inner", "https://evil.example/ad"], _problem)
    frames = [f for f, _, _ in obs._frames()]
    assert ok in frames and bad not in frames


def test_page_on_a_disallowed_site_is_not_described():
    obs, _ = _observer("https://evil.example/phish", [], _problem)
    out = obs.snapshot(settle=False)
    assert out.elements == {} and "evil.example/phish" not in out.text.split("\n")[0] and "not allowed" in out.text


def test_previous_element_handles_are_released():
    obs, _ = _observer("https://evil.example/phish", [], _problem)
    disposed = []
    handle = SimpleNamespace(dispose=lambda: disposed.append(1))
    obs.last = SimpleNamespace(elements={"e1": ElementRef("e1", None, None, {}, handle)})
    obs.snapshot(settle=False)
    assert disposed == [1]


# ---------------------------------------------------------------- 2. suite / session ids name report files
@pytest.mark.parametrize("bad", ["../../escaped", "a/b", "..", ".hidden", "x" * 61, "with space"])
def test_report_ids_outside_the_rule_are_refused(bad):
    with pytest.raises(SelectionError):
        check_report_id(bad)


def test_normal_report_ids_are_accepted():
    assert check_report_id("LOGIN-001") == "LOGIN-001"


def test_run_suite_refuses_a_traversing_suite_id_before_the_browser(tmp_path):
    from framework.runner.orchestrator import Orchestrator

    suite = SuiteModel.model_validate({"suite_id": "../../escaped", "suite_name": "x", "cases": []})
    orch = Orchestrator.__new__(Orchestrator)
    with pytest.raises(SelectionError, match="suite_id"):
        Orchestrator.run_suite(orch, suite)


def test_run_dir_must_stay_in_the_reports_folder(tmp_path):
    from framework.runner.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.report_dir = tmp_path / "reports"
    with pytest.raises(ValueError):
        orch.start_run_dir("../escaped_20261016T000000Z")
    assert not (tmp_path / "escaped_20261016T000000Z").exists()


def test_report_file_must_stay_in_the_reports_folder(tmp_path):
    from framework.cli import _report_file

    assert _report_file(tmp_path, "LOGIN-001.html") == tmp_path / "LOGIN-001.html"
    with pytest.raises(ValueError):
        _report_file(tmp_path, "../../escaped.html")


def test_session_plan_with_a_traversing_id_cannot_start(tmp_path):
    from framework.models import SessionPlan
    from framework.runner.session_runner import resolve_plan

    plan = SessionPlan.model_validate({"session_id": "../x", "session_name": "s", "files": []})
    with pytest.raises(SelectionError, match="session_id"):
        resolve_plan(plan)


# ---------------------------------------------------------------- 3. Try SQL and stored {env:} passwords
SAVED = "Driver=x;Server=db;UID=u;PWD={env:TESTBOT_DB_PW};"


def test_try_reads_env_password_only_for_a_saved_connection():
    envs = {"qa": {"connections": {"default": SAVED}}}
    assert dataaccess.connection_for_try(envs, SAVED, "qa", "default") == SAVED
    with pytest.raises(ValueError, match="not saved"):
        dataaccess.connection_for_try(envs, "Driver=x;Server=other;UID=u;PWD={env:TESTBOT_DB_PW};", "qa", "default")
    typed = "Driver=x;Server=other;UID=u;PWD=fixture-pw;"
    assert dataaccess.connection_for_try(envs, typed) == typed


def test_try_endpoint_refuses_env_password_for_another_server(ws, api, monkeypatch):  # noqa: F811 - pytest fixture
    called = []
    monkeypatch.setattr(dataaccess, "run_select", lambda cs, q: called.append(cs) or {"columns": [], "rows": [], "truncated": False})
    dataaccess.write_environments(ws, {"qa": {"connections": {"default": SAVED}}})
    status, _, body = api("POST", "/api/sql/test", {"connection_string": "Driver=x;Server=other;PWD={env:TESTBOT_DB_PW};", "query": "SELECT 1"})
    assert status == 400 and b"not saved" in body and called == []
    status, _, _ = api("POST", "/api/sql/test", {"env": "qa", "connection": "default", "query": "SELECT 1"})
    assert status == 200 and called == [SAVED]


# ---------------------------------------------------------------- 4. CSV export
def test_empty_csv_cells_stay_empty():
    assert _excel_cell("") == ""
    assert _excel_cell("   ") == "   "
    assert _excel_cell(" =1") == "' =1"
    assert _excel_cell("\r\n+1") == "'\r\n+1"


def test_group_export_keeps_empty_cells_empty(ws):
    from framework.manager.groups import export_csv
    import csv
    import io

    (ws.dir("test_cases") / "s.json").write_text(json.dumps({"suite_id": "s", "suite_name": "s", "cases": [{"id": "TC-1", "title": "t", "steps": []}]}), encoding="utf-8")
    (ws.dir("groups") / "g1.json").write_text(json.dumps({"id": "g1", "name": "g", "members": [{"type": "suite", "path": "s.json"}]}), encoding="utf-8")
    rows = list(csv.reader(io.StringIO(export_csv(ws, "g1"))))
    assert "'" not in rows[1]


# ---------------------------------------------------------------- 5. read-only SQL check
@pytest.mark.parametrize("query", [
    "SELECT pg_terminate_backend(123)",
    "SELECT pg_cancel_backend(123)",
    "SELECT set_config('a', 'b', false)",
    "SELECT nextval('s')",
    "SELECT setval('s', 1)",
    "SELECT SLEEP(100)",
    "SELECT pg_sleep(100)",
    "SELECT pg_sleep_for('5 minutes')",
    "SELECT BENCHMARK(1000000000, MD5(1))",
    "SELECT NEXT VALUE FOR dbo.seq",
    "SELECT next   value\nfor dbo.seq",
    "SELECT lo_unlink(1)",
    "SELECT pg_reload_conf()",
    "SELECT pg_advisory_lock(1)",
    "SELECT GET_LOCK('x', 10)",
    "SELECT LOAD_FILE('/etc/passwd')",
])
def test_side_effecting_functions_are_refused(query):
    with pytest.raises(ValueError, match="not allowed"):
        check_select_only(query)


@pytest.mark.parametrize("query", [
    "SELECT sleep_minutes FROM shifts",
    "SELECT 'nextval' AS label",
    "SELECT [sleep] FROM t",
    "SELECT value FROM t ORDER BY value",
])
def test_ordinary_names_are_still_allowed(query):
    assert check_select_only(query) == query


# ---------------------------------------------------------------- 6. minor
def test_run_exec_keeps_the_statement_error_when_close_fails():
    from framework.actions import sql_actions

    class _Cursor:
        rowcount = 0

        def execute(self, *a):
            raise RuntimeError("08S01 communication link failure")

        def close(self):
            raise RuntimeError("connection is closed")

    conn = SimpleNamespace(cursor=lambda: _Cursor())
    with pytest.raises(RuntimeError, match="communication link failure"):
        sql_actions.run_exec(conn, "UPDATE t SET a = 1")
    assert sql_actions.consume_broken(SimpleNamespace(close=lambda: None)) is False


def test_stop_worker_terminates_then_kills(monkeypatch):
    from framework.runner import load

    calls = []

    class _Proc:
        pid, alive = 12345, True

        def join(self, timeout=None):
            calls.append(("join", timeout))

        def is_alive(self):
            return self.alive

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")
            self.alive = False

    monkeypatch.setattr(load.os, "name", "posix")
    load._stop_worker(_Proc(), 0.0)
    assert [c for c in calls if isinstance(c, str)] == ["terminate", "kill"]


def test_worker_unwinds_on_sigterm():
    from framework.runner import load

    with pytest.raises(SystemExit):
        load._exit_on_sigterm(15, None)
