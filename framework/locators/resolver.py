from __future__ import annotations

import time

from playwright.sync_api import ElementHandle, Page

from framework.locators.semantic import build_semantic_locator
from framework.locators.vision_fallback import VisionLocateError, locate_by_description
from framework.models import Target

_SEMANTIC_STRATEGIES = {
    "role", "label", "placeholder", "text", "testid", "css", "xpath",
    "row_containing", "table_cell",
}


class LocateError(Exception):
    pass


def resolve_target(page: Page, target: Target, *, description: str = "", wait_ms: int = 0) -> ElementHandle:
    """Hybrid resolution: semantic locator first; AI vision only when that fails
    or resolves ambiguously, or when the step explicitly asks for strategy="ai".
    `wait_ms` lets a not-yet-rendered element appear before giving up on the semantic locator.
    """
    if target.strategy == "ai":
        try:
            return locate_by_description(page, target.value)
        except VisionLocateError as exc:
            raise LocateError(f"AI vision could not locate: {target.value!r} ({exc})") from exc

    if target.strategy not in _SEMANTIC_STRATEGIES:
        raise LocateError(f"Unknown target strategy: {target.strategy!r}")

    # Poll for up to wait_ms before concluding "not there": pages render asynchronously, and the
    # locator is rebuilt each round so a container that appears later (an open popup) is honoured.
    deadline = time.monotonic() + wait_ms / 1000
    while True:
        locator = build_semantic_locator(page, target)
        count = locator.count()
        if count > 0 or time.monotonic() >= deadline:
            break
        time.sleep(0.2)

    if count == 1:
        element = locator.element_handle()
        if element is None:
            raise LocateError(f"Locator for '{target.strategy}={target.value}' matched but yielded no handle")
        return element

    fallback_description = description or target.value
    try:
        return locate_by_description(page, fallback_description)
    except VisionLocateError as exc:
        if count == 0:
            raise LocateError(
                f"Semantic locator '{target.strategy}={target.value}' matched 0 elements, "
                f"and AI fallback failed: {exc}"
            ) from exc
        raise LocateError(
            f"Semantic locator '{target.strategy}={target.value}' matched {count} elements (ambiguous), "
            f"and AI fallback failed: {exc}"
        ) from exc
