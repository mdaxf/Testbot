from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from playwright.sync_api import Page, sync_playwright

import json
import re

from framework import branding, logs
from framework.locators import describe as element_describe, resolver
from framework.actions.executor import execute_action
from framework.actions.integration_actions import (
    INTEGRATION_ACTIONS, StepAssertionFailed, close_subscriptions, run_integration_action,
)
from framework.actions.sql_actions import SqlConnections, run_exec, run_query
from framework.assertions.engine import evaluate_expected
from framework.locators.resolver import resolve_target
from framework.models import CaseResult, StepResult, SuiteResult, TestCase, TestStep, TestSuite, Viewport
from framework.runner.context_options import resolve_context_options
from framework.runner.selection import describe, select_cases
from framework.variables.context import MissingVariableError, VariableContext
from framework.variables.generators import generate_faker_value

run_log = logs.get("runner")
step_log = logs.get("step")


class Orchestrator:
    def __init__(
        self,
        base_url: str,
        sql_connections: SqlConnections,
        *,
        headless: bool = True,
        report_dir: str = "reports",
        device: Optional[str] = None,
        viewport: Optional[Viewport] = None,
        extra_vars: Optional[dict[str, Any]] = None,
        screenshots: str = "all",
        mode: Optional[str] = None,
        env_name: Optional[str] = None,
    ):
        self.suite_path: Optional[Path] = None      # the suite file being run (the agent's recommendation is written back into it)
        self._heals: list[dict[str, Any]] = []
        self._current_step: Optional[Any] = None
        self.env_name = env_name        # the environment the run uses (--env, or the session's): recorded in the results
        self.cli_mode = mode            # --mode; TESTBOT_MODE and the suite/case `mode` are consulted by resolve_mode()
        self.suite_mode: Optional[str] = None
        self.current_suite: Optional[TestSuite] = None
        self.extra_vars = dict(extra_vars or {})  # e.g. {worker_id}, {iteration} in load runs
        self.screenshots = screenshots  # "all" | "fail" (failed/errored steps only) | "none"
        self.base_url = base_url
        self._default_base_url = base_url
        self.sql_connections = sql_connections
        self.headless = headless
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self._run_dir: Path = self.report_dir  # overwritten by start_run_dir()
        self.device = device  # CLI-level override; wins over a suite's own `device`/`viewport`
        self.viewport = viewport

    def environment_label(self, suite: TestSuite) -> str:
        """The environment recorded in the results: the one this run uses, else the suite's own, else 'inline'."""
        return self.env_name or suite.environment or "inline"

    def apply_suite_settings(self, suite: TestSuite) -> None:
        """Suite-level base_url / connections win over the environment's; reset per suite."""
        self.suite_mode = suite.mode
        self.current_suite = suite
        self.base_url = suite.base_url or self._default_base_url
        self.sql_connections.use_overrides(suite.connections)

    def start_run_dir(self, name: str) -> Path:
        """Every run (a standalone suite, or a whole multi-file session) gets its own
        screenshot folder under report_dir so evidence from repeated runs never overwrites."""
        self._run_dir = self.report_dir / name
        self._run_dir.mkdir(parents=True, exist_ok=True)
        logs.attach_run_log(self._run_dir)   # run.log next to the screenshots
        return self._run_dir

    def run_suite(self, suite: TestSuite, case_ids: Optional[list[str]] = None, tags: Optional[list[str]] = None) -> SuiteResult:
        """Run the suite. `case_ids` = only those cases, in that order; `tags` = the cases with those tags (see framework.runner.selection);
        neither = all cases in file order."""
        selected = select_cases(suite, case_ids, tags)     # an unknown id / tag fails here, before any browser starts
        if (suite.status or "default") != "default":
            run_log.warning("suite %s is marked '%s' (revision %s), not the default revision: running it as it is", suite.suite_id, suite.status, suite.revision)
        started_at = datetime.now(timezone.utc)
        self.apply_suite_settings(suite)
        self.start_run_dir(f"{suite.suite_id}_{started_at.strftime('%Y%m%dT%H%M%SZ')}")

        run_log.info("suite %s started: %d case(s), environment=%s, base_url=%s, mode=%s, headless=%s", suite.suite_id, len(suite.cases),
                     suite.environment_label or "-", self.base_url or "-", self.suite_mode or self.cli_mode or "script", self.headless)
        run_log.debug("report folder: %s", self._run_dir)

        result = SuiteResult(
            suite_id=suite.suite_id,
            environment=self.environment_label(suite),
            revision=suite.revision,
            started_at=started_at.isoformat(),
        )
        if case_ids or tags:
            result.cases_in_file, result.selected = len(suite.cases), [c.id for c in selected]
            run_log.info("suite %s: running %s", suite.suite_id, describe(suite, selected, case_ids, tags))

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            try:
                context = None
                ctx = None
                for idx, case in enumerate(selected):
                    if idx > 0 and selected.chain[idx] and context is not None:
                        pass      # a prerequisite just ran: continue in the same browser with its variables
                    else:
                        if context is not None:
                            context.close()
                        context_options = resolve_context_options(
                            pw, suite, case=case, device_override=self.device, viewport_override=self.viewport
                        )
                        context = browser.new_context(**context_options)
                        page = context.new_page()
                        ctx = None
                    case_result, ctx = self.run_case(page, case, ctx, default_step_delay_ms=suite.default_step_delay_ms)
                    result.cases.append(case_result)
                    if case_result.status != "pass" and suite.on_case_fail == "stop":
                        break
                if context is not None:
                    context.close()
            finally:
                browser.close()

        result.finished_at = datetime.now(timezone.utc).isoformat()
        run_log.info("suite %s finished: %d/%d case(s) passed in %.1f s", suite.suite_id, sum(1 for c in result.cases if c.status == "pass"), len(result.cases),
                     (datetime.now(timezone.utc) - started_at).total_seconds())
        return result

    def run_case(self, page: Page, case: TestCase, ctx: Optional[VariableContext] = None, **kwargs: Any) -> tuple[CaseResult, VariableContext]:
        run_log.info("case %s - %s: started (%d step(s)%s)", case.id, case.title, len(case.steps), ", natural language" if case.objective and not case.steps else "")
        started_at = datetime.now(timezone.utc)
        self._heals = []
        resolver.HEAL_SINK = self._record_heal
        try:
            result, ctx = self._run_case(page, case, ctx, **kwargs)
        finally:
            resolver.HEAL_SINK = None
        self._save_heals(case, result)
        result.started_at, result.finished_at = started_at.isoformat(), datetime.now(timezone.utc).isoformat()
        result.revision = case.revision
        level = logging.INFO if result.status == "pass" else (logging.ERROR if result.status == "error" else logging.WARNING)
        run_log.log(level, "case %s: %s in %.1f s (%d/%d step(s) passed)", case.id, result.status.upper(), result.duration_ms / 1000,
                    sum(1 for st in result.steps if st.status == "pass"), len(result.steps))
        return result, ctx

    def _run_case(
        self,
        page: Page,
        case: TestCase,
        ctx: Optional[VariableContext] = None,
        *,
        namespace: str = "",
        default_step_delay_ms: int = 0,
    ) -> tuple[CaseResult, VariableContext]:
        """Runs one case on the given page. Pass an existing `ctx` to continue a variable
        store carried over from a previous case/file (session mode); omit it for a fresh,
        isolated case (the default -- matches standalone run_suite() behavior). `namespace`
        prefixes screenshot filenames so a multi-file session can't collide on repeated case IDs.
        `default_step_delay_ms` is the suite's common per-step delay (see TestSuite.default_step_delay_ms).
        """
        started = time.monotonic()
        if ctx is None:
            ctx = VariableContext({**({"base_url": self.base_url} if self.base_url else {}), **self.extra_vars})
        screenshot_id = f"{namespace}{case.id}"
        case_result = CaseResult(case_id=case.id, title=case.title, status="pass")

        try:
            self._seed_variables(case, ctx)
        except Exception as exc:  # noqa: BLE001 - seeding failure fails the case, never crashes the run
            run_log.error("case %s: could not prepare the case variables: %s", case.id, logs.clip(exc))
            case_result.status = "error"
            case_result.steps.append(
                StepResult(
                    step_no=0,
                    description="Seed case variables",
                    action="seed_variables",
                    data_used=", ".join(f"{name}={src.source}" for name, src in case.variables.items()) or None,
                    status="error",
                    error=str(exc),
                    screenshot_path=self._capture_screenshot(page, screenshot_id, 0, "error"),
                )
            )
            case_result.duration_ms = int((time.monotonic() - started) * 1000)
            return case_result, ctx

        from framework.agent.config import resolve_mode

        if case.variables:
            run_log.debug("case %s variables: %s", case.id, ", ".join(f"{n}({v.source})" for n, v in case.variables.items()))
        mode = resolve_mode(self.cli_mode, self.suite_mode, case.mode)
        run_log.debug("case %s mode: %s", case.id, mode)
        if case.objective and (mode == "agentic" or (mode == "auto" and not case.steps)):
            return self._run_agent_case(page, case, ctx, screenshot_id, started), ctx
        if case.objective and not case.steps:
            run_log.warning("case %s has an objective but no steps and the mode is '%s': nothing to run (use --mode agentic)", case.id, mode)
        if case.objective and not case.steps:     # script mode: a natural-language-only test has nothing to run -- say so, never guess
            case_result.status = "inconclusive"
            case_result.steps.append(StepResult(
                step_no=1, description="Natural-language test case: no script to run", action="agent:skipped", status="inconclusive",
                error="This case has an objective but no steps. Run it with TESTBOT_MODE=agentic (or auto / --mode agentic), or generate a script from an agent run."))
            case_result.duration_ms = int((time.monotonic() - started) * 1000)
            return case_result, ctx

        try:
            for step in case.steps:
                self._current_step = step
                step_log.debug("%s step %s [%s] %s%s", case.id, step.step_no, step.action, logs.clip(step.description, 120), self._log_data(step, ctx))
                step_result = self._run_step(page, step, ctx, screenshot_id, default_step_delay_ms=default_step_delay_ms)
                case_result.steps.append(step_result)
                self._log_step(case.id, step, step_result)
                if step_result.status != "pass":
                    case_result.status = {"fail": "fail", "inconclusive": "inconclusive"}.get(step_result.status, "error")
                    break  # first failed step stops the case; later steps assume earlier state
        finally:
            close_subscriptions(ctx)  # bus subscriptions live for one case only

        case_result.duration_ms = int((time.monotonic() - started) * 1000)
        return case_result, ctx

    def _run_agent_case(self, page: Page, case: TestCase, ctx: VariableContext, screenshot_id: str, started: float) -> CaseResult:
        try:
            from framework.agent.integration import run_agent_case

            result = run_agent_case(self, page, case, ctx, screenshot_id, self.current_suite, None)
        except Exception as exc:  # noqa: BLE001 - e.g. no API key / unsupported provider: a clear error result, never a crash
            result = CaseResult(case_id=case.id, title=case.title, status="error", steps=[StepResult(
                step_no=1, description="The agent could not run", action="agent:error", status="error", error=f"{type(exc).__name__}: {exc}",
                screenshot_path=self._capture_screenshot(page, screenshot_id, 1, "error"))])
        result.duration_ms = int((time.monotonic() - started) * 1000)
        close_subscriptions(ctx)
        return result

    def _seed_variables(self, case: TestCase, ctx: VariableContext) -> None:
        for name, source in case.variables.items():
            if source.source == "constant":
                ctx.set(name, source.value)
            elif source.source == "faker":
                ctx.set(name, generate_faker_value(source.model_dump(exclude_none=True)))
            elif source.source == "sql":
                conn = self.sql_connections.get(source.connection)
                rows = run_query(conn, ctx.resolve_string(source.query or ""))
                if not rows:
                    raise ValueError(f"Variable '{name}': SQL query returned no rows")
                column = source.column or next(iter(rows[0]))
                ctx.set(name, rows[0][column])
            else:
                raise ValueError(f"Unknown variable source '{source.source}' for '{name}'")

    def _run_step(
        self,
        page: Page,
        step: TestStep,
        ctx: VariableContext,
        case_id: str,
        *,
        default_step_delay_ms: int = 0,
    ) -> StepResult:
        started = time.monotonic()
        data_used = self._describe_step_data(step, ctx)

        try:
            sql_row: Optional[dict[str, Any]] = None

            if step.action == "sql_query":
                conn = self.sql_connections.get(step.connection)
                query = ctx.resolve_string(step.query or "")
                rows = run_query(conn, query)
                sql_row = rows[0] if rows else None
                if step.config and step.config.get("poll") and step.expected is not None:
                    # config.poll: the app may still be committing -- re-run until the expectation holds
                    # or timeout_ms passes (only the final result is asserted below)
                    deadline = time.monotonic() + step.timeout_ms / 1000
                    while time.monotonic() < deadline and not evaluate_expected(page, step.expected, ctx, sql_row=sql_row).passed:
                        time.sleep(1)
                        rows = run_query(conn, query)
                        sql_row = rows[0] if rows else None
            elif step.action == "sql_exec":
                conn = self.sql_connections.get(step.connection)
                run_exec(conn, ctx.resolve_string(step.query or ""))
            elif step.action == "set_var":
                if step.capture is None:
                    raise ValueError("set_var step requires a 'capture' block naming the variable")
                ctx.set(step.capture.var, ctx.resolve_string(step.input or ""))
            elif step.action == "agent":
                from framework.agent.integration import run_agent_step

                return run_agent_step(self, page, step, ctx, case_id, self.current_suite)
            elif step.action in INTEGRATION_ACTIONS:
                summary = run_integration_action(step, ctx)
                logs.get("integration").info("%s step %s %s: %s", case_id, step.step_no, step.action, logs.clip(logs.redact(summary), 300))
                data_used = f"{data_used} | {summary}" if data_used else summary
            elif step.action != "assert":
                execute_action(page, step, ctx)

            # give the app time to finish navigation/business logic before checking the
            # result -- Playwright's own auto-waiting doesn't know about app-side async work
            effective_delay = step.delay_after_ms if step.delay_after_ms is not None else default_step_delay_ms
            if effective_delay > 0:
                page.wait_for_timeout(effective_delay)

            captured = self._maybe_capture(page, step, ctx, sql_row=sql_row)
            if captured is not None:
                var_name, var_value = captured
                data_used = f"{data_used} | captured {var_name}={var_value}" if data_used else f"captured {var_name}={var_value}"

            status, actual, expected = self._maybe_assert(page, step, ctx, sql_row=sql_row)
            screenshot_path = self._capture_screenshot(page, case_id, step.step_no, status)

            return StepResult(
                step_no=step.step_no,
                description=step.description,
                action=step.action,
                data_used=data_used,
                status=status,
                actual=actual,
                expected=expected,
                screenshot_path=screenshot_path,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except StepAssertionFailed as exc:
            return StepResult(
                step_no=step.step_no, description=step.description, action=step.action, data_used=data_used,
                status="fail", error=str(exc), screenshot_path=self._capture_screenshot(page, case_id, step.step_no, "fail"),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except MissingVariableError as exc:
            result = self._error_result(step, started, f"Missing variable: {exc}", data_used)
            result.screenshot_path = self._capture_screenshot(page, case_id, step.step_no, "error")
            return result
        except Exception as exc:  # noqa: BLE001 - any failure becomes a failed/errored step, never crashes the run
            result = self._error_result(step, started, str(exc), data_used)
            result.screenshot_path = self._capture_screenshot(page, case_id, step.step_no, "error")
            return result

    def _record_heal(self, target: Any, handle: Any, description: str) -> None:
        step = self._current_step
        new = element_describe.element_target(handle)
        if step is None or new is None or not step.target:
            return
        old = step.target.model_dump(exclude_none=True)
        if {k: new.get(k) for k in ("strategy", "value")} == {k: old.get(k) for k in ("strategy", "value")}:
            return
        self._heals.append({"step": step, "old": old, "new": new})

    def _save_heals(self, case: TestCase, result: CaseResult) -> None:
        """Self-healing: a PASSING scripted case whose target was found by the AI fallback gets a recommendation with the corrected targets (if `learn` is on)."""
        if not self._heals or result.status != "pass" or not self.suite_path:
            return
        from framework import learn
        from framework.agent.config import AgentConfig

        suite_agent = self.current_suite.agent if self.current_suite and self.current_suite.agent else {}
        try:
            if not AgentConfig.from_env({**suite_agent, **(case.agent or {})}).learn:
                return
        except Exception:  # noqa: BLE001
            return
        steps, notes, seen = [], [], set()
        for h in self._heals:
            st = h["step"]
            if st.step_no in seen:
                continue
            seen.add(st.step_no)
            d = json.loads(st.model_dump_json(exclude_none=True, exclude_defaults=True))
            d["target"] = {**h["new"], **({"name": h["old"]["name"]} if h["new"]["strategy"] == "role" and h["old"].get("name") else {})}
            d["replaces_step"] = st.step_no
            steps.append(d)
            notes.append(f"step {st.step_no}: the target {h['old'].get('strategy')}={h['old'].get('value')!r} no longer matched; the AI fallback found the element, now {h['new']['strategy']}={h['new']['value']!r}")
        rec = learn.build(steps, None, run=self._run_dir.name, model="self-healing", verdict="pass", base_revision=getattr(case, "revision", None), notes=notes, kind="heal")
        if learn.attach(Path(self.suite_path), re.split(r"~\d+$", case.id)[0], rec):
            run_log.info("case %s: %d healed target(s) saved as a recommendation on the test case, for review", case.id, len(steps))
        self._heals = []

    def _log_data(self, step: TestStep, ctx: VariableContext) -> str:
        """What a step is about to use, for the DEBUG line: the resolved data with anything secret-looking masked."""
        data = self._describe_step_data(step, ctx)
        if not data:
            return ""
        raw = " ".join(x for x in (step.input, step.target.value if step.target else None, step.target.name if step.target else None, step.description) if x)
        if logs.looks_secret(raw) and step.input is not None:
            try:
                data = data.replace(ctx.resolve_string(step.input), "***") if ctx.resolve_string(step.input) else data
            except MissingVariableError:
                data = data.replace(step.input, "***")
        return "  | " + logs.clip(logs.redact(data), 300)

    def _log_step(self, case_id: str, step: TestStep, r: StepResult) -> None:
        head = f"{case_id} step {step.step_no} {r.status.upper():<5} {step.action:<12} {logs.clip(step.description, 120)} ({r.duration_ms} ms)"
        if r.status == "pass":
            step_log.info(head)
        elif r.status == "error":
            step_log.error("%s -- %s", head, logs.clip(logs.redact(r.error or "error"), 400))
        else:       # fail / inconclusive
            detail = f"expected {logs.clip(r.expected, 120)!r}, actual {logs.clip(r.actual, 120)!r}" if r.expected is not None or r.actual is not None else logs.clip(logs.redact(r.error or ""), 400)
            step_log.warning("%s -- %s", head, detail)
        if r.screenshot_path:
            step_log.debug("%s step %s screenshot: %s", case_id, step.step_no, r.screenshot_path)

    def _describe_step_data(self, step: TestStep, ctx: VariableContext) -> Optional[str]:
        """Best-effort resolved view of what this step is about to use, for the result record.
        Resolution failures here are swallowed (shown as the raw {placeholder}) -- the real
        failure is raised and recorded by the actual dispatch in _run_step.
        """

        def _safe_resolve(text: str) -> str:
            try:
                return ctx.resolve_string(text)
            except MissingVariableError:
                return text

        parts: list[str] = []
        if step.target is not None:
            parts.append(f"target[{step.target.strategy}]={_safe_resolve(step.target.value)}")
        if step.input is not None:
            secret = logs.looks_secret(step.input, step.description, step.target.value if step.target else None, step.target.name if step.target else None)
            parts.append(f"input={'***' if secret else _safe_resolve(step.input)}")      # a password / token / key typed into a field never reaches the report
        if step.query is not None:
            parts.append(f"query={logs.redact(_safe_resolve(step.query))}")
        return " | ".join(parts) if parts else None

    def _maybe_capture(
        self, page: Page, step: TestStep, ctx: VariableContext, *, sql_row: Optional[dict[str, Any]]
    ) -> Optional[tuple[str, Any]]:
        capture = step.capture
        if capture is None or step.action == "set_var":
            return None  # set_var already wrote its own variable above

        if capture.from_ == "sql_column":
            if sql_row is None:
                raise ValueError(f"Step {step.step_no}: capture from_='sql_column' but the query returned no rows")
            column = capture.column or next(iter(sql_row))
            value = sql_row[column]
            ctx.set(capture.var, value)
            return capture.var, value

        if capture.from_ == "url":
            value = page.url
            ctx.set(capture.var, value)
            return capture.var, value

        if capture.from_ in ("element_text", "element_value"):
            if capture.target is None:
                raise ValueError(f"Step {step.step_no}: capture from_='{capture.from_}' requires a target")
            resolved_target = capture.target.model_copy(update={"value": ctx.resolve_string(capture.target.value)})
            element = resolve_target(page, resolved_target)
            raw_value = element.text_content() if capture.from_ == "element_text" else element.input_value()
            value = (raw_value or "").strip()
            ctx.set(capture.var, value)
            return capture.var, value

        raise ValueError(f"Step {step.step_no}: unknown capture source '{capture.from_}'")

    def _maybe_assert(
        self, page: Page, step: TestStep, ctx: VariableContext, *, sql_row: Optional[dict[str, Any]]
    ) -> tuple[str, Any, Any]:
        if step.expected is None:
            return "pass", None, None
        result = evaluate_expected(page, step.expected, ctx, sql_row=sql_row)
        return ("pass" if result.passed else "fail"), result.actual, result.expected

    def _error_result(self, step: TestStep, started: float, message: str, data_used: Optional[str]) -> StepResult:
        return StepResult(
            step_no=step.step_no,
            description=step.description,
            action=step.action,
            data_used=data_used,
            status="error",
            error=message,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _capture_screenshot(self, page: Page, case_id: str, step_no: int, status: str) -> Optional[str]:
        """Captures evidence for every step -- pass, fail, and error -- unless load mode trimmed it."""
        if self.screenshots == "none" or (self.screenshots == "fail" and status == "pass"):
            return None
        try:
            path = self._run_dir / f"{case_id}_step{step_no}_{status}.png"
            branding.screenshot(page, path=str(path))
            return path.relative_to(self.report_dir).as_posix()  # forward slashes: valid as both an HTML href and a JSON path
        except Exception:  # noqa: BLE001 - a screenshot failure must not mask the real result
            return None
