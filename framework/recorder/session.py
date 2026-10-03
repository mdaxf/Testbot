from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from framework.recorder.injected import INJECTED_JS

BROWSERS = {"chromium": None, "msedge": "msedge", "chrome": "chrome"}  # name -> Playwright channel
_NAV_WINDOW_S = 5.0     # a page change this soon after a click/Enter is that action's result
_REDIRECT_WINDOW_S = 2.0  # follow-up navigations this close together are one redirect chain


def _label(target: dict[str, Any]) -> str:
    if target["strategy"] == "role":
        return f"{target.get('name', '')} {target['value']}".strip()
    return target["value"]


class RecordingSession:
    """Owns the recorder browser (in its own thread -- Playwright's sync API is thread-bound)
    and turns raw page events into testbot step dicts. `steps` may be read from another
    thread (the UI) under `lock`; `version` bumps on every change so a poller can cheaply
    tell whether to redraw."""

    def __init__(self, start_url: str, browser: str = "chromium", *, headless: bool = False,
                 on_ready: Optional[Callable[[Any], None]] = None):
        if browser not in BROWSERS:
            raise ValueError(f"browser must be one of {sorted(BROWSERS)}")
        self.start_url = start_url
        self.browser_name = browser
        self.headless = headless
        self.on_ready = on_ready  # test hook: called with the first page once recording is live
        self.steps: list[dict[str, Any]] = []
        self.variables: dict[str, dict[str, Any]] = {}
        self.base_url = ""
        self.version = 0
        self.error: Optional[str] = None
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._finished = threading.Event()
        self._started = threading.Event()
        self._last_action_at = 0.0
        self._last_nav_at = 0.0
        self._password_count = 0

    # ---------------------------------------------------------------- lifecycle
    def start(self, timeout: float = 30) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._started.wait(timeout):
            raise TimeoutError("The recorder browser did not start in time")
        if self.error:
            raise RuntimeError(self.error)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=self.headless, channel=BROWSERS[self.browser_name])
                try:
                    context = browser.new_context(no_viewport=True) if not self.headless else browser.new_context()
                    context.expose_binding("__tbRecord", lambda _source, event: self._on_event(event))
                    context.add_init_script(INJECTED_JS)
                    context.on("page", self._watch_page)
                    page = context.new_page()
                    self._watch_page(page)
                    page.goto(self.start_url)
                    self._started.set()
                    if self.on_ready is not None:
                        self.on_ready(page)
                    while not self._stop.is_set() and browser.is_connected():
                        live = [p for p in context.pages if not p.is_closed()]
                        if not live:
                            break  # the user closed the last tab/window
                        live[0].wait_for_timeout(200)  # pumps Playwright events (bindings, navigations)
                finally:
                    try:
                        browser.close()
                    except Exception:  # noqa: BLE001 - already closed by the user
                        pass
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI instead of dying silently
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self._started.set()
            self._finished.set()

    def _watch_page(self, page: Any) -> None:
        page.on("framenavigated", lambda frame: self._on_navigated(page, frame))

    # ---------------------------------------------------------------- step building
    def _add(self, step: dict[str, Any]) -> None:
        with self.lock:
            self.steps.append(step)
            self.version += 1

    def add_manual_step(self, step: dict[str, Any]) -> None:
        """Steps the user inserts by hand (e.g. wait_until for a loading indicator)."""
        self._add(step)

    def delete_step(self, index: int) -> None:
        with self.lock:
            if 0 <= index < len(self.steps):
                del self.steps[index]
                self.version += 1

    def clear(self) -> None:
        with self.lock:
            self.steps.clear()
            self.variables.clear()
            self._password_count = 0
            self.version += 1

    def _on_navigated(self, page: Any, frame: Any) -> None:
        try:
            if frame != page.main_frame:
                return
            parts = urlsplit(frame.url)
            if parts.scheme not in ("http", "https", "file"):
                return
            now = time.monotonic()
            with self.lock:
                if not self.base_url and parts.scheme != "file":
                    self.base_url = f"{parts.scheme}://{parts.netloc}"
                path = parts.path or "/"
                last = self.steps[-1] if self.steps else None
                caused = last is not None and last["action"] in ("click", "press", "select") and (now - self._last_action_at) < _NAV_WINDOW_S
                redirect = last is not None and last.get("_auto_nav") and (now - self._last_nav_at) < _REDIRECT_WINDOW_S
                if caused or redirect:
                    last["expected"] = {"type": "url_contains", "value": path}
                    last["_auto_nav"] = True
                elif last is None or last["action"] != "navigate" or last["target"]["value"] != f"{{base_url}}{path}":
                    prefix = "{base_url}" if self.base_url and frame.url.startswith(self.base_url) else ""
                    value = f"{prefix}{path}" if prefix else frame.url
                    self._add({
                        "description": f"Open {path}", "action": "navigate",
                        "target": {"strategy": "url", "value": value},
                        "expected": {"type": "url_contains", "value": path}, "_auto_nav": True,
                    })
                self._last_nav_at = now
                self.version += 1
        except Exception:  # noqa: BLE001 - a recording glitch must never break the user's browsing
            pass

    def _on_event(self, event: dict[str, Any]) -> None:
        try:
            now = time.monotonic()
            kind, target = event["type"], event.get("target")
            with self.lock:
                if kind == "click":
                    self._add({"description": f"Click '{_label(target)}'", "action": "click", "target": target})
                elif kind == "type":
                    value = event.get("value", "")
                    if event.get("isPassword"):
                        self._password_count += 1
                        name = "password" if self._password_count == 1 else f"password_{self._password_count}"
                        self.variables[name] = {"source": "constant", "value": "CHANGE_ME"}
                        value = "{" + name + "}"
                    last = self.steps[-1] if self.steps else None
                    if last and last["action"] == "type" and last["target"] == target:
                        last["input"] = value  # same field edited again: keep only the final value
                        self.version += 1
                    else:
                        self._add({"description": f"Enter '{_label(target)}'", "action": "type", "target": target, "input": value})
                elif kind == "select":
                    self._add({"description": f"Select '{event.get('label') or event.get('value')}' in '{_label(target)}'",
                               "action": "select", "target": target, "input": event.get("value", "")})
                elif kind == "press":
                    self._add({"description": f"Press {event['key']} in '{_label(target)}'", "action": "press",
                               "target": target, "input": event["key"]})
                elif kind == "upload":
                    self._add({"description": f"Upload a file in '{_label(target)}' (set the file path in Input)",
                               "action": "upload", "target": target, "input": ""})
                else:
                    return
                self._last_action_at = now
        except Exception:  # noqa: BLE001
            pass
