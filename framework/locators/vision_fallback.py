from __future__ import annotations

from playwright.sync_api import ElementHandle, Page

from framework.locators.vision_providers import VisionProviderError, call_vision_model

# Numbers every visible interactive element with a small red badge (a "set-of-marks"
# overlay) so a vision-capable model can point at one the same way a human would.
_MARK_ELEMENTS_JS = """
() => {
    const selector = 'button, a, input, select, textarea, [role], [onclick], [tabindex]';
    const elements = Array.from(document.querySelectorAll(selector)).filter((el) => {
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    });
    window.__testMarks = elements;
    elements.forEach((el, i) => {
        const rect = el.getBoundingClientRect();
        const badge = document.createElement('div');
        badge.textContent = String(i);
        badge.className = '__test_mark_badge';
        badge.style.position = 'fixed';
        badge.style.left = rect.left + 'px';
        badge.style.top = Math.max(rect.top - 14, 0) + 'px';
        badge.style.background = 'red';
        badge.style.color = 'white';
        badge.style.fontSize = '10px';
        badge.style.padding = '1px 3px';
        badge.style.zIndex = '2147483647';
        document.body.appendChild(badge);
    });
    return elements.length;
}
"""

_REMOVE_BADGES_JS = "() => { document.querySelectorAll('.__test_mark_badge').forEach((el) => el.remove()); }"
_CLEAR_MARKS_JS = "() => { window.__testMarks = undefined; }"


class VisionLocateError(Exception):
    pass


def locate_by_description(page: Page, description: str) -> ElementHandle:
    """AI_VISION_PROVIDER (env var, default "anthropic") picks which vision-capable
    LLM answers -- see framework/locators/vision_providers.py for the provider list."""
    mark_count = page.evaluate(_MARK_ELEMENTS_JS)
    if mark_count == 0:
        page.evaluate(_REMOVE_BADGES_JS)
        raise VisionLocateError("No interactive elements found on the page to mark")

    screenshot_bytes = page.screenshot()
    page.evaluate(_REMOVE_BADGES_JS)  # remove the visual badges before continuing; keep window.__testMarks

    prompt = (
        "The screenshot has small red numbered badges near interactive elements. "
        f'Which numbered badge marks: "{description}"? '
        "Reply with ONLY the integer number, nothing else."
    )

    try:
        try:
            raw_answer = call_vision_model(screenshot_bytes, prompt)
        except VisionProviderError as exc:
            raise VisionLocateError(str(exc)) from exc

        try:
            index = int(raw_answer)
        except ValueError as exc:
            raise VisionLocateError(f"Model did not return a usable index, got: {raw_answer!r}") from exc

        handle = page.evaluate_handle(f"window.__testMarks && window.__testMarks[{index}]")
        element = handle.as_element()
        if element is None:
            raise VisionLocateError(f"Model chose index {index}, but it did not map to a page element")
        return element
    finally:
        page.evaluate(_CLEAR_MARKS_JS)
