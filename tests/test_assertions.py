from __future__ import annotations

from framework.assertions.engine import evaluate_expected
from framework.models import Expected, Target
from framework.variables.context import VariableContext


class _Node:
    def __init__(self, visible: bool):
        self._visible = visible

    def is_visible(self) -> bool:
        return self._visible


class _Locator:
    def __init__(self, flags: list[bool]):
        self.flags = flags

    def count(self) -> int:
        return len(self.flags)

    def nth(self, index: int) -> _Node:
        return _Node(self.flags[index])


def test_visible_and_hidden_look_at_every_match(monkeypatch):
    monkeypatch.setattr("framework.assertions.engine.build_semantic_locator", lambda page, target: _Locator([False, True]))
    target = Target(strategy="css", value=".row")
    ctx = VariableContext()
    visible = evaluate_expected(None, Expected(type="visible", target=target), ctx)
    hidden = evaluate_expected(None, Expected(type="hidden", target=target), ctx)
    assert visible.passed is True
    assert hidden.passed is False
