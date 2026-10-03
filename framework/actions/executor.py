from __future__ import annotations

import re

from playwright.sync_api import Error as PlaywrightError, Page, expect

from framework import branding
from framework.locators.resolver import resolve_target
from framework.locators.semantic import build_semantic_locator
from framework.models import TestStep
from framework.variables.context import VariableContext

_SCROLL_DIRECTIONS = {"down": (0, 1), "up": (0, -1), "right": (1, 0), "left": (-1, 0)}
_SCROLL_CLAMP = 10_000_000  # scrollBy/window.scrollBy clamp to valid range, so this reliably means "all the way"


def _parse_scroll(text: str) -> tuple[int, int]:
    text = text.strip().lower()
    if text == "top":
        return (0, -_SCROLL_CLAMP)
    if text == "bottom":
        return (0, _SCROLL_CLAMP)
    direction, _, amount_str = text.partition(":")
    if direction not in _SCROLL_DIRECTIONS:
        raise ValueError(f"Unknown scroll spec '{text}'. Use 'down'/'up'/'left'/'right'[:amount], or 'top'/'bottom'.")
    amount = int(amount_str) if amount_str else 500
    dx_sign, dy_sign = _SCROLL_DIRECTIONS[direction]
    return (dx_sign * amount, dy_sign * amount)


def _wait_until(page: Page, step: TestStep, ctx: VariableContext) -> None:
    """Poll a target element until a condition holds, up to step.timeout_ms.

    input: "hidden" (default -- e.g. a "Loading..." spinner is gone, or absent from the DOM),
    "visible", "value:<text>" (input value equals), "has_value" (input value non-empty),
    "text:<text>" (element text equals), "text_contains:<text>".
    """
    if step.target is None:
        raise ValueError(f"Step {step.step_no}: action 'wait_until' requires a target")
    condition = ctx.resolve_string(step.input or "hidden").strip()
    kind, _, arg = condition.partition(":")
    kind = kind.lower()

    if step.target.strategy == "ai":
        raise ValueError(f"Step {step.step_no}: wait_until does not support the 'ai' target strategy")
    resolved_target = step.target.model_copy(update={"value": ctx.resolve_string(step.target.value)})
    # A raw locator, not resolve_target(): the element may not exist yet (or ever, once it
    # has disappeared), and resolve_target would fall back to AI vision on zero matches.
    locator = build_semantic_locator(page, resolved_target).first
    timeout = step.timeout_ms

    if kind == "hidden":
        expect(locator).to_be_hidden(timeout=timeout)  # also passes when the element is absent
    elif kind == "visible":
        expect(locator).to_be_visible(timeout=timeout)
    elif kind == "value":
        expect(locator).to_have_value(arg, timeout=timeout)
    elif kind == "has_value":
        expect(locator).to_have_value(re.compile(r".+"), timeout=timeout)
    elif kind == "text":
        expect(locator).to_have_text(arg, timeout=timeout)
    elif kind == "text_contains":
        expect(locator).to_contain_text(arg, timeout=timeout)
    else:
        raise ValueError(
            f"Unknown wait_until condition '{condition}'. Use hidden / visible / value:<text> / "
            "has_value / text:<text> / text_contains:<text>."
        )


def execute_action(page: Page, step: TestStep, ctx: VariableContext) -> None:
    """Handles UI actions. sql_query/sql_exec/set_var/assert are handled directly
    by the orchestrator, since they need the SQL connection / assertion engine.
    """
    action = step.action

    if action == "navigate":
        url = ctx.resolve_string(step.target.value) if step.target is not None else ctx.resolve_string(step.input or "")
        page.goto(url)
        return

    if action == "wait":
        page.wait_for_timeout(int(ctx.resolve_string(step.input or "1000")))
        return

    if action == "wait_until":
        _wait_until(page, step, ctx)
        return

    if action == "screenshot":
        branding.screenshot(page, path=ctx.resolve_string(step.input or "screenshot.png"))
        return

    if action == "scan":
        # Deliberately targetless: a real barcode/keyboard-wedge scanner has no idea which
        # field is focused, it just types into whatever has focus after the screen decided
        # where to put the cursor. Use `expected`/`capture` with a target on THIS step to
        # verify the value actually landed in the field that was supposed to be required/focused.
        page.keyboard.type(ctx.resolve_string(step.input or ""))
        return

    if action == "scroll":
        # With a target and no input: scroll that element into view (the common case --
        # "scroll down until this button is visible"). With input ("down:500", "right:300",
        # "top", "bottom"): scroll that specific container, or the whole page if no target.
        resolved_input = ctx.resolve_string(step.input) if step.input else None
        if step.target is not None:
            resolved_target = step.target.model_copy(update={"value": ctx.resolve_string(step.target.value)})
            element = resolve_target(page, resolved_target, description=step.description, wait_ms=step.timeout_ms)
            if resolved_input is None:
                element.scroll_into_view_if_needed()
            else:
                dx, dy = _parse_scroll(resolved_input)
                element.evaluate("(el, [dx, dy]) => el.scrollBy(dx, dy)", [dx, dy])
        else:
            dx, dy = _parse_scroll(resolved_input or "down:600")
            page.evaluate("([dx, dy]) => window.scrollBy(dx, dy)", [dx, dy])
        return

    if action in ("click", "type", "select", "hover", "press", "upload", "check"):
        if step.target is None:
            raise ValueError(f"Step {step.step_no}: action '{action}' requires a target")

        resolved_target = step.target.model_copy(update={"value": ctx.resolve_string(step.target.value)})

        def perform() -> None:
            element = resolve_target(page, resolved_target, description=step.description, wait_ms=step.timeout_ms)

            if action == "click":
                if step.config and step.config.get("js_click"):
                    element.dispatch_event("click")  # DOM click: works when something overlays the element
                else:
                    element.click()
            elif action == "type":
                element.fill(ctx.resolve_string(step.input or ""))
            elif action == "select":
                if element.evaluate("e => e.tagName") == "INPUT":  # HTML5 <datalist> "dropdown": type the value
                    element.fill(ctx.resolve_string(step.input or ""))
                else:
                    element.select_option(ctx.resolve_string(step.input or ""))
            elif action == "hover":
                element.hover()
            elif action == "press":
                element.press(ctx.resolve_string(step.input or ""))
            elif action == "upload":
                element.set_input_files(ctx.resolve_string(step.input or ""))
            elif action == "check":
                # target is the checkbox's clickable <label> (custom-styled boxes hide the <input>);
                # click it only when the box isn't already in the wanted state -- idempotent, unlike click.
                wanted = ctx.resolve_string(step.input or "true").strip().lower() in ("true", "1", "yes", "on")
                state = element.evaluate("el => { const i = el.control || el.parentElement.querySelector('input'); return !!(i && i.checked); }")
                if state != wanted:
                    element.click()


        # Re-rendering pages (Apriso trees/grids) can detach the element between lookup and action;
        # look it up again and retry a few times before reporting the failure.
        for attempt in range(4):
            try:
                perform()
                break
            except PlaywrightError as exc:
                stale = any(w in str(exc) for w in ("not attached", "detached", "Element is not stable"))
                if not stale or attempt == 3:
                    raise
                page.wait_for_timeout(500)
        return

    raise ValueError(f"execute_action does not handle action type: {action}")
