"""The agent's tools. Every tool that acts on the page maps onto an ordinary testbot step, is checked by the policy first,
and is recorded so the run can be exported as a script. Assertions are executed by testbot's own assertion engine -- a pass is
never just the model's opinion."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from playwright.sync_api import Error as PlaywrightError, Page

from framework import logs
from framework.agent.config import AgentConfig, Profile
from framework.agent.observer import ElementRef, Observer, PageWatch
from framework.agent.policy import Policy, is_secret_name
from framework.assertions.engine import evaluate_expected
from framework.models import Expected, Target
from framework.variables.context import MissingVariableError, VariableContext

ACTION_TIMEOUT_MS = 8000

log = logs.get("agent")


@dataclass
class ActionRecord:
    n: int
    tool: str
    args: dict[str, Any]
    ok: bool
    message: str
    reason: str = ""
    status: str = "pass"                 # pass | fail | error
    target: Optional[dict[str, Any]] = None
    shot: Optional[str] = None
    assertion: Optional[dict[str, Any]] = None
    export: Optional[list[dict[str, Any]]] = None     # testbot step(s) equivalent to this action
    duration_ms: int = 0


@dataclass
class Assertion:
    id: str
    kind: str
    args: dict[str, Any]
    passed: bool
    actual: Any
    expected: Any
    covers: list[int]
    key: str


@dataclass
class ToolOutcome:
    text: str
    ok: bool = True
    observe: bool = True          # show the model the new page state
    image: bool = False           # attach a screenshot


class ToolExecutor:
    def __init__(self, page: Page, observer: Observer, watch: PageWatch, policy: Policy, ctx: VariableContext, sql_conns: Any,
                 cfg: AgentConfig, profile: Profile, base_url: str, secret_values: set[str], shot: Callable[[int, str], Optional[str]]):
        self.page, self.observer, self.watch, self.policy, self.ctx = page, observer, watch, policy, ctx
        self.sql, self.cfg, self.profile, self.base_url, self.secrets, self._shot = sql_conns, cfg, profile, base_url, secret_values, shot
        self.records: list[ActionRecord] = []
        self.assertions: list[Assertion] = []
        self.data_log: list[dict[str, Any]] = []
        self.notes: list[str] = []

    # ------------------------------------------------------------------ helpers
    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            for s in self.secrets:
                if s and len(s) >= 3:
                    value = value.replace(s, "***")
            return value
        if isinstance(value, dict):
            return {k: self.redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        return value

    def _ref(self, element_id: Any) -> ElementRef:
        obs = self.observer.last
        ref = obs.elements.get(str(element_id)) if obs else None
        if ref is None:
            raise ToolError(f"unknown element '{element_id}'. Element ids change whenever the page changes -- use an id from the latest list.")
        return ref

    def _loc(self, ref: ElementRef):
        return ref.frame.locator(f'[data-tb-agent="{ref.id}"]').first

    def _resolve_text(self, text: str) -> str:
        try:
            return self.ctx.resolve_string(text)
        except MissingVariableError as exc:
            names = sorted(k for k in self.ctx.as_dict())
            raise ToolError(f"{exc}. Variables you can use: {', '.join('{' + n + '}' for n in names)}") from exc

    def _export_target(self, ref: ElementRef) -> Optional[dict[str, Any]]:
        return self.observer.describe(ref)

    def _spinner_step(self, ref: Optional[ElementRef]) -> list[dict[str, Any]]:
        if not self.profile.spinner:
            return []
        t: dict[str, Any] = {"strategy": "css", "value": self.profile.spinner}
        if ref is not None and ref.frame_css:
            t["frame"] = ref.frame_css
        return [{"description": "Wait for the busy indicator to clear", "action": "wait_until", "target": t, "input": "hidden", "timeout_ms": 10000}]

    # ------------------------------------------------------------------ dispatch
    def execute(self, name: str, args: dict[str, Any], reason: str) -> ToolOutcome:
        n = len(self.records) + 1
        t0 = time.monotonic()
        rec = ActionRecord(n=n, tool=name, args=self.redact(dict(args)), ok=True, message="", reason=reason)
        try:
            handler = getattr(self, f"t_{name}", None)
            if handler is None:
                raise ToolError(f"unknown tool '{name}'")
            outcome: ToolOutcome = handler(args, rec)
            rec.message = outcome.text.splitlines()[0][:300] if outcome.text else "ok"
        except ToolError as exc:
            outcome = ToolOutcome(f"NOT DONE: {exc}", ok=False, observe=name != "query_db")
            rec.ok, rec.status, rec.message = False, "error", str(exc)[:300]
        except PlaywrightError as exc:
            msg = str(exc).splitlines()[0][:250]
            outcome = ToolOutcome(f"NOT DONE: the page did not allow this ({msg}). Look at the current page state and try something else.", ok=False)
            rec.ok, rec.status, rec.message = False, "error", msg
        rec.duration_ms = int((time.monotonic() - t0) * 1000)
        if name not in ("note", "set_variable"):
            self.policy.actions += 1
        if rec.assertion is not None and not rec.assertion["passed"]:
            rec.status = "fail"
        if name not in ("note", "set_variable", "look"):
            rec.shot = self._shot(n, rec.status)
        self.records.append(rec)
        self._log(rec)
        return outcome

    def _log(self, rec: ActionRecord) -> None:
        args = ", ".join(f"{k}={logs.clip(v, 80)!r}" for k, v in rec.args.items() if k not in ("covers",) and v not in (None, ""))
        head = f"action {rec.n} {rec.status.upper():<5} {rec.tool}({logs.clip(logs.redact(args), 160)}) ({rec.duration_ms} ms)"
        if rec.status == "pass":
            log.info("%s -- %s", head, logs.clip(rec.message, 160))
        elif rec.status == "fail":
            a = rec.assertion or {}
            log.warning("%s -- expected %r, actual %r", head, logs.clip(a.get("expected"), 100), logs.clip(a.get("actual"), 100))
        else:
            log.warning("%s -- NOT DONE: %s", head, logs.clip(rec.message, 200))
        if rec.reason:
            log.debug("action %d reason: %s", rec.n, logs.clip(rec.reason, 300))
        if rec.shot:
            log.debug("action %d screenshot: %s", rec.n, rec.shot)

    # ------------------------------------------------------------------ page tools
    def t_navigate(self, a, rec) -> ToolOutcome:
        url = self._resolve_text(str(a.get("url", "")))
        problem = self.policy.check_navigation(url)
        if problem:
            raise ToolError(problem)
        self.page.goto(url, timeout=30000)
        self.watch.settle()
        value = "{base_url}" + url[len(self.base_url):] if self.base_url and url.startswith(self.base_url) else url
        rec.export = [{"description": f"Open {url}", "action": "navigate", "target": {"strategy": "url", "value": value}}]
        return ToolOutcome(f"Opened {url}")

    def t_click(self, a, rec) -> ToolOutcome:
        ref = self._ref(a.get("element"))
        problem = self.policy.check_press(ref.info)
        if problem:
            raise ToolError(problem)
        stuck = self.policy.note_action(f"click:{ref.id}:{self.observer.last.url}")
        if stuck:
            raise ToolError(stuck)
        rec.target = self._export_target(ref)
        loc = self._loc(ref)
        loc.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)
        loc.click(timeout=ACTION_TIMEOUT_MS)
        self.watch.settle()
        label = ref.info.get("name") or ref.id
        rec.export = ([{"description": f"Click '{label}'", "action": "click", "target": rec.target}] + self._spinner_step(ref)) if rec.target else None
        return ToolOutcome(f"Clicked '{label}'")

    def t_type(self, a, rec) -> ToolOutcome:
        ref = self._ref(a.get("element"))
        raw = str(a.get("text", ""))
        problem = self.policy.check_type(ref.info, raw)
        if problem:
            raise ToolError(problem)
        text = self._resolve_text(raw)
        rec.target = self._export_target(ref)
        loc = self._loc(ref)
        loc.fill(text, timeout=ACTION_TIMEOUT_MS)
        self.watch.settle(2.0)
        name = ref.info.get("name") or ref.id
        secret = ref.info.get("type") == "password" or bool(re.search(r"\{(\w+)\}", raw) and any(is_secret_name(v) for v in re.findall(r"\{(\w+)\}", raw)))
        self.data_log.append({"field": name, "value": "***" if secret else raw, "source": a.get("data_source") or "unspecified"})
        rec.export = [{"description": f"Enter '{name}'", "action": "type", "target": rec.target, "input": raw}] if rec.target else None
        return ToolOutcome(f"Entered text into '{name}'")

    def t_select(self, a, rec) -> ToolOutcome:
        ref = self._ref(a.get("element"))
        option = self._resolve_text(str(a.get("option", "")))
        rec.target = self._export_target(ref)
        loc = self._loc(ref)
        if ref.info.get("tag") == "select":
            loc.select_option(option, timeout=ACTION_TIMEOUT_MS)
        else:                                   # HTML5 datalist "dropdown": type the value
            loc.fill(option, timeout=ACTION_TIMEOUT_MS)
        self.watch.settle(2.0)
        name = ref.info.get("name") or ref.id
        self.data_log.append({"field": name, "value": option, "source": a.get("data_source") or "unspecified"})
        rec.export = [{"description": f"Select '{option}' in '{name}'", "action": "select", "target": rec.target, "input": option}] if rec.target else None
        return ToolOutcome(f"Selected '{option}' in '{name}'")

    def t_check(self, a, rec) -> ToolOutcome:
        ref = self._ref(a.get("element"))
        want = bool(a.get("checked", True))
        rec.target = self._export_target(ref)
        loc = self._loc(ref)
        try:
            loc.set_checked(want, timeout=3000)
        except PlaywrightError:                 # custom-styled box whose real input is hidden: click its label
            state = loc.evaluate("el => !!el.checked")
            if state != want:
                loc.evaluate("el => { const l = el.labels && el.labels[0]; (l || el).click(); }")
        self.watch.settle(1.5)
        name = ref.info.get("name") or ref.id
        rec.export = [{"description": f"Set '{name}' to {str(want).lower()}", "action": "check", "target": rec.target, "input": str(want).lower()}] if rec.target else None
        return ToolOutcome(f"'{name}' is now {'ticked' if want else 'unticked'}")

    def t_press(self, a, rec) -> ToolOutcome:
        key = str(a.get("key", "Enter"))
        if a.get("element"):
            ref = self._ref(a["element"])
            rec.target = self._export_target(ref)
            self._loc(ref).press(key, timeout=ACTION_TIMEOUT_MS)
            rec.export = [{"description": f"Press {key}", "action": "press", "target": rec.target, "input": key}] if rec.target else None
        else:
            self.page.keyboard.press(key)
        self.watch.settle()
        return ToolOutcome(f"Pressed {key}")

    def t_wait_for_text(self, a, rec) -> ToolOutcome:
        text, want = str(a.get("text", "")), bool(a.get("visible", True))
        timeout = min(float(a.get("timeout_s", 10)), 30.0)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            found = self._text_in_any_frame(text)
            if found == want:
                self.watch.settle(1.0)
                rec.export = [{"description": f"Wait until the text '{text}' is {'shown' if want else 'gone'}", "action": "wait_until",
                               "target": {"strategy": "text", "value": text}, "input": "visible" if want else "hidden", "timeout_ms": int(timeout * 1000)}]
                return ToolOutcome(f"The text '{text}' is now {'shown' if want else 'gone'}")
            self.page.wait_for_timeout(250)
        raise ToolError(f"after {timeout:.0f} s the text '{text}' is still {'missing' if want else 'shown'}")

    def t_scroll(self, a, rec) -> ToolOutcome:
        self.page.mouse.wheel(0, 600 if a.get("direction", "down") == "down" else -600)
        self.watch.settle(1.0)
        return ToolOutcome("Scrolled")

    def t_look(self, a, rec) -> ToolOutcome:
        if self.cfg.vision == "off":
            return ToolOutcome("Screenshots are switched off for this test; rely on the element list and text.")
        return ToolOutcome("Here is the current screen.", image=True)

    # ------------------------------------------------------------------ data tools
    def t_set_variable(self, a, rec) -> ToolOutcome:
        name, value = str(a.get("name", "")).strip(), str(a.get("value", ""))
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            raise ToolError("a variable name is letters, digits and underscores")
        if is_secret_name(name):
            raise ToolError("secrets cannot be created by the agent")
        self.ctx.set(name, value)
        self.data_log.append({"field": f"{{{name}}}", "value": value, "source": a.get("data_source") or "generated"})
        return ToolOutcome(f"Saved {{{name}}} = {value}", observe=False)

    def t_note(self, a, rec) -> ToolOutcome:
        self.notes.append(str(a.get("text", ""))[:500])
        return ToolOutcome("Noted.", observe=False)

    def t_query_db(self, a, rec) -> ToolOutcome:
        from framework.actions.sql_actions import run_query
        from framework.manager.dataaccess import check_select_only

        try:
            query = check_select_only(self._resolve_text(str(a.get("query", ""))))
        except ValueError as exc:
            raise ToolError(f"{exc} (the agent may only read the database)") from exc
        try:
            rows = run_query(self.sql.get(a.get("connection") or "default"), query)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"the query failed: {str(exc)[:200]}") from exc
        shown = [" | ".join(str(v) for v in r.values()) for r in rows[:15]]
        rec.message = f"{len(rows)} row(s)"
        return ToolOutcome(f"{len(rows)} row(s)" + (":\n" + "\n".join(shown) if shown else ""), observe=False)

    # ------------------------------------------------------------------ assertions (run by the deterministic engine)
    def _text_in_any_frame(self, text: str) -> bool:
        needle = re.sub(r"\s+", " ", text).strip().lower()
        for frame in self.page.frames:
            try:
                body = frame.evaluate("() => (document.body ? document.body.innerText : '')")
            except Exception:  # noqa: BLE001
                continue
            if needle in re.sub(r"\s+", " ", body).lower():
                return True
        return False

    def _element_target(self, ref: ElementRef) -> Target:
        return Target(strategy="css", value=f'[data-tb-agent="{ref.id}"]', frame=ref.frame_css)

    def t_assert(self, a, rec) -> ToolOutcome:
        kind = str(a.get("kind", ""))
        covers = [int(x) for x in (a.get("covers") or []) if str(x).isdigit()]
        export_expected: Optional[dict[str, Any]] = None
        export_target: Optional[dict[str, Any]] = None
        if kind in ("text_present", "text_absent"):
            text = self._resolve_text(str(a.get("text", "")))
            found = self._text_in_any_frame(text)
            passed = found if kind == "text_present" else not found
            actual, expected = ("found" if found else "not found"), ("shown" if kind == "text_present" else "not shown")
            export_target = {"strategy": "text", "value": text}
            export_expected = {"type": "visible" if kind == "text_present" else "hidden"}
        elif kind in ("url_contains", "url_equals"):
            value = self._resolve_text(str(a.get("value", "")))
            res = evaluate_expected(self.page, Expected(type=kind, value=value), self.ctx)
            passed, actual, expected = res.passed, res.actual, res.expected
            export_expected = {"type": kind, "value": value}
        elif kind == "sql":
            passed, actual, expected = self._assert_sql(a)
        else:
            ref = self._ref(a.get("element"))
            target = self._element_target(ref)
            export_target = self._export_target(ref)
            if kind == "element_text":
                etype, value = ("text_equals", a["equals"]) if a.get("equals") is not None else ("text_contains", a.get("contains", ""))
                exp = Expected(type=etype, value=self._resolve_text(str(value)), target=target)
            elif kind == "element_value":
                exp = Expected(type="value_equals", value=self._resolve_text(str(a.get("equals", ""))), target=target)
            elif kind in ("element_visible", "element_hidden"):
                exp = Expected(type="visible" if kind == "element_visible" else "hidden", target=target)
            elif kind == "element_checked":
                want = bool(a.get("checked", True))
                state = self._loc(ref).evaluate("el => !!el.checked")
                passed, actual, expected = state == want, state, want
                return self._record_assertion(rec, kind, a, passed, actual, expected, covers, export_target, None)
            elif kind == "css":
                exp = Expected(type="css_equals", property=a.get("property") or "background-color", value=str(a.get("equals", "")), target=target)
            elif kind == "table_rows":
                exp = Expected(type="list_matches", value=a.get("rows"), rows=a.get("rows_selector"), item=a.get("item"), style_item=a.get("style_item"), target=target)
            else:
                raise ToolError(f"unknown assertion kind '{kind}'. Kinds: text_present, text_absent, url_contains, url_equals, element_text, element_value, "
                                "element_visible, element_hidden, element_checked, css, table_rows, sql")
            res = evaluate_expected(self.page, exp, self.ctx)
            passed, actual, expected = res.passed, res.actual, res.expected
            export_expected = exp.model_dump(exclude_none=True, exclude={"target"})
        return self._record_assertion(rec, kind, a, passed, actual, expected, covers, export_target, export_expected)

    def _assert_sql(self, a) -> tuple[bool, Any, Any]:
        from framework.actions.sql_actions import run_query
        from framework.manager.dataaccess import check_select_only

        try:
            query = check_select_only(self._resolve_text(str(a.get("query", ""))))
        except ValueError as exc:
            raise ToolError(f"{exc} (the agent may only read the database)") from exc
        want = str(a.get("equals", ""))
        end, actual = time.monotonic() + 10, None
        while True:                               # the application may still be committing: retry for a few seconds
            try:
                rows = run_query(self.sql.get(a.get("connection") or "default"), query)
            except Exception as exc:  # noqa: BLE001
                raise ToolError(f"the query failed: {str(exc)[:200]}") from exc
            col = a.get("column")
            actual = (rows[0].get(col) if col else next(iter(rows[0].values()))) if rows else None
            if str(actual) == want or time.monotonic() > end:
                return str(actual) == want, actual, want
            time.sleep(1)

    def _record_assertion(self, rec, kind, args, passed, actual, expected, covers, export_target, export_expected) -> ToolOutcome:
        key = hashlib.sha1((kind + json.dumps({k: v for k, v in args.items() if k not in ("covers",)}, sort_keys=True, default=str)).encode()).hexdigest()[:12]
        aid = f"a{len(self.assertions) + 1}"
        self.assertions.append(Assertion(aid, kind, self.redact(args), bool(passed), self.redact(actual), self.redact(expected), covers, key))
        rec.assertion = {"id": aid, "kind": kind, "passed": bool(passed), "actual": self.redact(actual), "expected": self.redact(expected), "covers": covers}
        if export_expected is not None and kind != "sql":
            step: dict[str, Any] = {"description": f"Check: {kind.replace('_', ' ')}", "action": "assert", "expected": dict(export_expected)}
            if export_target:
                step["expected"]["target"] = export_target
            rec.export = [step]
        elif kind == "sql":
            rec.export = [{"description": "Check the database", "action": "sql_query", "query": args.get("query", ""), "config": {"poll": True},
                           "expected": {"type": "sql_result_equals", "value": str(args.get("equals", "")), **({"column": args["column"]} if args.get("column") else {})}}]
        text = f"{'PASS' if passed else 'FAIL'} assertion {aid} ({kind})" + ("" if passed else f": expected {self.redact(expected)!r}, actual {self.redact(actual)!r}")
        return ToolOutcome(text, ok=True, observe=False)


class ToolError(Exception):
    pass
