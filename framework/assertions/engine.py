from __future__ import annotations

from typing import Any, Optional

from playwright.sync_api import Page

from framework.locators.resolver import resolve_target
from framework.locators.semantic import build_semantic_locator
from framework.assertions.style_checks import css_equals, has_classes, list_matches
from framework.models import Expected
from framework.variables.context import VariableContext


class AssertionResult:
    def __init__(self, passed: bool, actual: Any, expected: Any):
        self.passed = passed
        self.actual = actual
        self.expected = expected


def _any_visible(locator) -> bool:
    """True when any match is visible. `target.nth` has already narrowed the locator when it is set."""
    return any(locator.nth(i).is_visible() for i in range(locator.count()))


def evaluate_expected(
    page: Page,
    expected: Expected,
    ctx: VariableContext,
    *,
    sql_row: Optional[dict[str, Any]] = None,
    element: Any = None,
) -> AssertionResult:
    if expected.type == "none":
        return AssertionResult(True, None, None)

    expected_value = ctx.resolve(expected.value) if isinstance(expected.value, (str, list, dict)) else expected.value

    if expected.type == "url_equals":
        actual = page.url
        return AssertionResult(actual == expected_value, actual, expected_value)

    if expected.type == "url_contains":
        actual = page.url
        return AssertionResult(str(expected_value) in actual, actual, expected_value)

    if expected.type == "sql_result_equals":
        if sql_row is None:
            raise ValueError("expected.type == 'sql_result_equals' requires a SQL row (run a sql_query step first)")
        column = expected.column or next(iter(sql_row))
        actual = sql_row.get(column)
        return AssertionResult(actual == expected_value or str(actual) == str(expected_value), actual, expected_value)

    if expected.target is None:
        raise ValueError(f"expected.type == '{expected.type}' requires a target")

    resolved_target = expected.target.model_copy(update={"value": ctx.resolve_string(expected.target.value)})

    if expected.type == "count_equals":
        if resolved_target.nth is not None:
            raise ValueError("expected.type == 'count_equals' counts all matches -- don't set target.nth for it")
        actual = build_semantic_locator(page, resolved_target).count()
        return AssertionResult(actual == int(expected_value), actual, expected_value)

    if expected.type == "visible":
        if element is not None:
            actual = bool(element.is_visible())
        else:
            actual = _any_visible(build_semantic_locator(page, resolved_target))
        return AssertionResult(actual, actual, True)

    if expected.type == "hidden":
        if element is not None:
            actual = not bool(element.is_visible())
        else:
            actual = not _any_visible(build_semantic_locator(page, resolved_target))
        return AssertionResult(actual, actual, True)

    if element is None:
        element = resolve_target(page, resolved_target)

    if expected.type == "text_equals":
        actual = (element.text_content() or "").strip()
        return AssertionResult(actual == str(expected_value).strip(), actual, expected_value)

    if expected.type == "text_contains":
        actual = element.text_content() or ""
        return AssertionResult(str(expected_value) in actual, actual, expected_value)

    if expected.type == "value_equals":
        actual = element.input_value()
        return AssertionResult(actual == str(expected_value), actual, expected_value)

    if expected.type == "css_equals":
        ok, actual, wanted = css_equals(element, expected.property or "background-color", str(expected_value))
        return AssertionResult(ok, actual, wanted)

    if expected.type == "has_class":
        ok, actual, wanted = has_classes(element, expected_value)
        return AssertionResult(ok, actual, wanted)

    if expected.type == "list_matches":
        ok, actual, wanted = list_matches(element, expected_value, rows=expected.rows, item=expected.item,
                                          style_item=expected.style_item)
        return AssertionResult(ok, actual, wanted)

    raise ValueError(f"Unhandled expected type: {expected.type}")
