"""Screenshots that carry the IACF mark.

The IACF logo (framework/brand_logo.py, hardcoded) is drawn into the page for the instant of the screenshot (a fixed, click-through element on top of everything) and removed
straight afterwards, so no image library is needed and the test's page is not changed. Switch it off with TESTBOT_WATERMARK=off."""
from __future__ import annotations

import os
from typing import Any

from framework.brand_logo import LOGO_BG, LOGO_DATA_URI, LOGO_H, LOGO_W
from framework.version import WATERMARK

_ARGS = {"logo": LOGO_DATA_URI, "bg": LOGO_BG, "w": LOGO_W * 6 // 10, "h": LOGO_H * 6 // 10, "text": WATERMARK}

_ADD = """(a) => {
  const old = document.getElementById('__tb_watermark'); if (old) old.remove();
  const d = document.createElement('div'); d.id = '__tb_watermark'; d.title = a.text;
  d.style.cssText = 'position:fixed;right:8px;bottom:6px;z-index:2147483647;pointer-events:none;padding:4px 6px;border-radius:4px;line-height:0;background:' + a.bg;
  const i = document.createElement('img'); i.src = a.logo; i.style.cssText = 'display:block;width:' + a.w + 'px;height:' + a.h + 'px;';
  d.appendChild(i); (document.body || document.documentElement).appendChild(d);
  return i.decode().catch(() => {});
}"""
_REMOVE = "() => { const d = document.getElementById('__tb_watermark'); if (d) d.remove(); }"


def enabled() -> bool:
    return os.environ.get("TESTBOT_WATERMARK", "on").strip().lower() not in ("0", "off", "false", "no")


def screenshot(page: Any, **kwargs: Any) -> bytes:
    """page.screenshot(**kwargs) with the watermark on it. Never fails because of the logo: without it the plain screenshot is taken."""
    stamped = False
    if enabled():
        try:
            page.evaluate(_ADD, _ARGS)
            stamped = True
        except Exception:  # noqa: BLE001 - page navigating/closed: take the screenshot unmarked
            stamped = False
    try:
        return page.screenshot(**kwargs)
    finally:
        if stamped:
            try:
                page.evaluate(_REMOVE)
            except Exception:  # noqa: BLE001
                pass
