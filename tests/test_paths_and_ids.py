from __future__ import annotations

import json

import pytest

from framework import revisions
from framework.manager import chat, groups, schedules
from framework.manager.workspace import ID_RE, WorkspaceError, check_id, resolve_within

BAD_IDS = ["../testbot-workspace", "..", ".hidden", "a/b", "a\\b", "", "x" * 61, "../../etc/passwd", "a b"]


@pytest.mark.parametrize("bad", BAD_IDS)
def test_check_id_rejects(bad):
    with pytest.raises(WorkspaceError):
        check_id(bad)


@pytest.mark.parametrize("good", ["daily", "adhoc-login.json-1a2b3c4d", "UAT_cycle-2", "-x", "_y", "a" * 60])
def test_check_id_accepts(good):
    assert check_id(good) == good


def test_groups_and_schedules_share_one_id_rule():
    assert groups._ID is ID_RE
    assert groups.slug(".hidden name") == "hidden-name"


def test_resolve_within(tmp_path):
    assert resolve_within(tmp_path, "a", "b.json") == (tmp_path / "a" / "b.json").resolve()
    for parts in (("..", "x"), ("a/../../x",), (".",)):
        with pytest.raises(WorkspaceError):
            resolve_within(tmp_path, *parts)


def test_schedule_ids_are_validated(ws):
    victim = ws.root / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    for fn in (schedules.load_schedule, schedules.delete_schedule, schedules.read_state):
        with pytest.raises(WorkspaceError):
            fn(ws, "../victim")
    with pytest.raises(WorkspaceError):
        schedules.write_state(ws, "../victim", {"history": []})
    with pytest.raises(WorkspaceError):
        schedules.save_schedule(ws, {"id": "../victim", "trigger": {"type": "manual"}, "items": []})
    assert victim.exists()
    schedules.save_schedule(ws, {"id": "nightly", "trigger": {"type": "manual"}, "items": []})
    assert schedules.load_schedule(ws, "nightly")["id"] == "nightly"


def test_chat_path_is_validated(ws):
    for bad in ("../../..", "../x.json", ".."):
        with pytest.raises(WorkspaceError):
            chat.clear(ws, bad)
        with pytest.raises(WorkspaceError):
            chat.load(ws, bad)
    assert chat.load(ws, "orders.json") == {"messages": []}


def _suite_file(ws):
    root = ws.dir("test_cases")
    (root / "orders.json").write_text(json.dumps({"suite_id": "S", "cases": [{"id": "TC-1", "title": "t", "steps": []}]}), encoding="utf-8")
    return root


def test_revision_numbers_and_paths(ws):
    root = _suite_file(ws)
    revisions.ensure(root, "orders.json")
    assert revisions.case_content(root, "orders.json", "TC-1", "1")["id"] == "TC-1"
    for bad in ("../../../../x", "1.json", "-1", "0", "abc", None):
        with pytest.raises(revisions.RevisionError):
            revisions.case_content(root, "orders.json", "TC-1", bad)
    with pytest.raises(revisions.RevisionError):
        revisions.settings_content(root, "orders.json", "../../x")
    for bad_rel in ("../outside.json", "../../x", ".revisions/orders.json/index.json"):
        with pytest.raises(revisions.RevisionError):
            revisions.summary(root, bad_rel)


def test_case_id_dots_never_become_a_folder():
    assert revisions._safe("..") == "%2E%2E"
    assert revisions._safe("a/b") == "a%2Fb"
    assert revisions._safe("TC-1") == "TC-1"
