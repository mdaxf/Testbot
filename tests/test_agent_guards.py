from __future__ import annotations

import pytest

from framework.agent.actions import ActionRecord, ToolError, ToolExecutor
from framework.agent.config import AgentConfig, Profile
from framework.agent.observer import ElementRef, Observation
from framework.agent.policy import Policy
from framework.variables.context import VariableContext


class _Handle:
    def __init__(self, name="Save", typ="text", visible=True):
        self.name = name
        self.typ = typ
        self.visible = visible
        self.clicked = False

    def evaluate(self, js):
        if "checked" in str(js):
            return False
        out = {"name": self.name}
        if self.typ is not None:
            out["type"] = self.typ
        return out

    def is_visible(self):
        return self.visible

    def click(self, timeout=None):
        self.clicked = True

    def scroll_into_view_if_needed(self, timeout=None):
        pass


class _Page:
    def __init__(self):
        self.url = "https://app.example/home"
        self.context = self
        self.pages = [self]

    def goto(self, url, timeout=None):
        self.url = "https://evil.example/phish"


class _Watch:
    def settle(self, *args, **kwargs):
        pass


class _Obs:
    def __init__(self, elements):
        self.last = Observation(url="https://app.example/home", title="", text="", elements=elements)

    def describe(self, ref):
        return {"strategy": "role", "value": "button", "name": ref.info.get("name") or ""}


def _executor(elements, ctx=None):
    policy = Policy(AgentConfig(), Profile(), ["https://app.example/home"], "check the page", [])
    return ToolExecutor(
        _Page(), _Obs(elements), _Watch(), policy, ctx or VariableContext(), None,
        AgentConfig(), Profile(), "https://app.example", set(), lambda n, s: None,
    )


def test_navigation_is_rechecked_after_redirect():
    ex = _executor({})
    with pytest.raises(ToolError, match="evil.example"):
        ex.t_navigate({"url": "https://app.example/home"}, ActionRecord(1, "navigate", {}, True, ""))


def test_click_judges_the_label_the_element_has_now():
    handle = _Handle(name="Delete")
    ref = ElementRef("e1", None, None, {"id": "e1", "name": "Save", "role": "button"}, handle)
    ex = _executor({"e1": ref})
    with pytest.raises(ToolError, match="destructive"):
        ex.t_click({"element": "e1"}, ActionRecord(1, "click", {}, True, ""))
    assert handle.clicked is False


def test_missing_handle_is_not_looked_up_by_attribute():
    ref = ElementRef("e1", None, None, {"id": "e1", "name": "Save", "role": "button"}, None)
    ex = _executor({"e1": ref})
    with pytest.raises(ToolError, match="no longer on the page"):
        ex.t_click({"element": "e1"}, ActionRecord(1, "click", {}, True, ""))


def test_assert_does_not_treat_the_label_as_a_press():
    handle = _Handle(name="Delete", visible=True)
    ref = ElementRef("e1", None, None, {"id": "e1", "name": "Delete", "role": "button"}, handle)
    ex = _executor({"e1": ref})
    out = ex.t_assert({"kind": "element_visible", "element": "e1"}, ActionRecord(1, "assert", {}, True, ""))
    assert out.text.startswith("PASS")


def test_agent_sql_binds_parameters_and_rejects_raw(monkeypatch):
    seen = []

    def run_query(conn, sql, params=None, rollback=False):
        seen.append((sql, list(params or []), rollback))
        return [{"a": "1"}]

    monkeypatch.setattr("framework.actions.sql_actions.run_query", run_query)

    class _Sql:
        def get_readonly(self, name="default"):
            return object()

    hostile = "O'Brien' UNION SELECT secret FROM users"
    ex = _executor({}, VariableContext({"name": hostile, "t": "Orders"}))
    ex.sql = _Sql()
    ex.t_query_db({"query": "SELECT a FROM t WHERE name = '{name}'"}, ActionRecord(1, "query_db", {}, True, ""))
    assert seen[0][1] == [hostile] and seen[0][2] is True and "UNION" not in seen[0][0]
    with pytest.raises(ToolError, match="raw"):
        ex.t_query_db({"query": "SELECT * FROM {raw:t}"}, ActionRecord(2, "query_db", {}, True, ""))


def test_allowed_hosts_ignore_case():
    policy = Policy(AgentConfig(allowed_hosts=["App.Example:443"]), Profile(), [], "look", [])
    assert policy.check_navigation("https://app.example:443/path") is None
    assert policy.check_navigation("https://other.example/path")


def test_a_harmless_label_change_is_still_the_same_element():
    handle = _Handle(name="Saving...")
    ref = ElementRef("e1", None, None, {"id": "e1", "name": "Save", "role": "button"}, handle)
    ex = _executor({"e1": ref})
    ex.t_click({"element": "e1"}, ActionRecord(1, "click", {}, True, ""))
    assert handle.clicked is True
