from __future__ import annotations

from typing import Optional

from playwright.sync_api import Playwright

from framework.models import TestCase, TestSuite, Viewport


def parse_viewport_arg(text: Optional[str]) -> Optional[Viewport]:
    """Parses a CLI --viewport value like "390x844" into a Viewport."""
    if not text:
        return None
    try:
        width_str, height_str = text.lower().split("x", 1)
        return Viewport(width=int(width_str), height=int(height_str))
    except ValueError as exc:
        raise ValueError(f"--viewport must look like WIDTHxHEIGHT, e.g. '390x844', got: {text!r}") from exc


def resolve_context_options(
    pw: Playwright,
    suite: TestSuite,
    *,
    case: Optional[TestCase] = None,
    device_override: Optional[str] = None,
    viewport_override: Optional[Viewport] = None,
) -> dict:
    """Builds the kwargs for browser.new_context(). Priority: a CLI-level override
    (--device/--viewport) wins over this case's own device/viewport, which wins over
    the suite's. If none are set, returns {} -- Playwright's default desktop viewport,
    unchanged from before this existed.

    A named device (Playwright's built-in descriptors, e.g. "iPhone 13", "Pixel 7")
    bundles viewport, user agent, device scale factor, is_mobile, and has_touch --
    real emulation, not just a resized window. `default_browser_type` is dropped: this
    framework always launches Chromium regardless of what the device usually pairs with,
    so an "iPhone" device here means Chromium rendering at iPhone viewport/UA/touch, not
    real Safari/WebKit.
    """
    device_name = device_override or (case.device if case else None) or suite.device
    if device_name:
        if device_name not in pw.devices:
            raise ValueError(
                f"Unknown device '{device_name}'. Examples: 'iPhone 13', 'Pixel 7', "
                "'iPad Mini', 'Desktop Chrome'. See Playwright's full device list."
            )
        options = dict(pw.devices[device_name])
        options.pop("default_browser_type", None)
        return options

    viewport = viewport_override or (case.viewport if case else None) or suite.viewport
    if viewport is not None:
        return {"viewport": {"width": viewport.width, "height": viewport.height}}

    return {}
