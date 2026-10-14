from __future__ import annotations

import pytest

from framework import models
from framework.runner.selection import SelectionError, select_cases


def _case(**kwargs):
    return models.TestCase(**kwargs)


def _suite(*cases) -> models.TestSuite:
    return models.TestSuite(suite_id="orders", suite_name="Orders", cases=list(cases))


def test_full_run_skips_non_default_revisions():
    suite = _suite(_case(id="A", title="a", status="draft"), _case(id="B", title="b"))
    assert [c.id for c in select_cases(suite)] == ["B"]


def test_explicit_draft_is_refused():
    suite = _suite(_case(id="A", title="a", status="superseded"), _case(id="B", title="b"))
    with pytest.raises(SelectionError, match="not the default revision"):
        select_cases(suite, ["A"])
    assert [c.id for c in select_cases(suite, ["B"])] == ["B"]


def test_depends_on_draft_is_refused():
    suite = _suite(
        _case(id="LOGIN", title="login", status="draft"),
        _case(id="A", title="a", depends_on=["LOGIN"]),
    )
    with pytest.raises(SelectionError, match="depends on LOGIN"):
        select_cases(suite, ["A"])
