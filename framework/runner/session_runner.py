from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, Page, sync_playwright

from pydantic import ValidationError

from framework import logs
from framework.loaders.errors import explain_file
from framework.loaders.excel_loader import load_suite_from_excel
from framework.loaders.json_loader import load_suite_from_json
from framework.models import CaseResult, SessionPlan, SessionResult, SuiteResult, TestSuite
from framework.runner.context_options import resolve_context_options
from framework.runner.orchestrator import Orchestrator
from framework.runner.selection import SelectionError, describe, select_cases
from framework.variables.context import VariableContext

log = logs.get("runner")


def _load_suite(path: str) -> TestSuite:
    suite_path = Path(path)
    if suite_path.suffix.lower() == ".json":
        return load_suite_from_json(suite_path)
    if suite_path.suffix.lower() in (".xlsx", ".xlsm"):
        return load_suite_from_excel(suite_path)
    raise ValueError(f"Unsupported suite file type: {suite_path.suffix}")


def resolve_plan(plan: SessionPlan) -> list:
    """Load every file of the plan and resolve its case selection. A wrong file or case id is reported (all of them at once)
    as a SelectionError before any browser starts. Returns [(entry, suite, selected cases)]."""
    prepared, problems = [], []
    for file_entry in plan.files:
        try:
            suite = _load_suite(file_entry.path)
            prepared.append((file_entry, suite, select_cases(suite, file_entry.cases, file_entry.tags, label=f"{file_entry.path}")))
        except SelectionError as exc:
            problems.append(str(exc))
        except ValidationError as exc:      # the suite file has invalid steps: say which, in words
            problems.append(f"{file_entry.path} has {len(exc.errors())} problem(s):\n      " + "\n      ".join(explain_file(file_entry.path, exc)))
        except Exception as exc:  # noqa: BLE001 - e.g. file not found
            problems.append(f"{file_entry.path}: {exc}")
    if problems:
        raise SelectionError("the session cannot start:\n  " + "\n  ".join(problems))
    return prepared


class SessionRunner:
    """Runs an ordered list of suite files as one session. Each file either starts
    clean (fresh browser context, fresh variables -- and its own cases stay isolated
    from each other, same as standalone run_suite(), each honoring its own
    device/viewport) or, when flagged `shares_state_with_previous`, continues the exact
    browser context and variable store left by the previous file's last case, and its
    own cases chain continuously too (no per-case reset, and no per-case device/viewport
    switching either -- cookies/storage continuity is the point of chaining).

    Per the chosen failure policy: if any case in a file fails or errors, the whole
    session stops before running the next file.
    """

    def __init__(self, orchestrator: Orchestrator):
        self.orchestrator = orchestrator

    def run(self, plan: SessionPlan) -> SessionResult:
        prepared = resolve_plan(plan)
        started_at = datetime.now(timezone.utc)
        self.orchestrator.start_run_dir(f"{plan.session_id}_{started_at.strftime('%Y%m%dT%H%M%SZ')}")

        log.info("session %s started: %d file(s)", plan.session_id, len(plan.files))

        result = SessionResult(
            session_id=plan.session_id,
            session_name=plan.session_name,
            started_at=started_at.isoformat(),
        )

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.orchestrator.headless)
            context: Optional[BrowserContext] = None
            page: Optional[Page] = None
            ctx: Optional[VariableContext] = None
            try:
                occurrences: dict[str, int] = {}
                for file_entry, suite, selected in prepared:
                    self.orchestrator.apply_suite_settings(suite)
                    self.orchestrator.suite_path = Path(file_entry.path)
                    chain = file_entry.shares_state_with_previous
                    policy = file_entry.on_fail or plan.on_fail
                    occurrences[suite.suite_id] = occurrences.get(suite.suite_id, 0) + 1
                    n_file = occurrences[suite.suite_id]          # the same file may appear several times: keep its screenshots apart
                    namespace = f"{suite.suite_id}__" if n_file == 1 else f"{suite.suite_id}-{n_file}__"
                    log.info("session %s: file %s (suite %s, %s%s)", plan.session_id, file_entry.path, suite.suite_id,
                             describe(suite, selected, file_entry.cases, file_entry.tags), ", continues the previous browser session" if chain else "")

                    if not chain or context is None:
                        if context is not None:
                            context.close()
                        first_case = selected[0] if selected else None
                        context_options = resolve_context_options(
                            pw,
                            suite,
                            case=first_case,
                            device_override=self.orchestrator.device,
                            viewport_override=self.orchestrator.viewport,
                        )
                        context = browser.new_context(**context_options)
                        page = context.new_page()
                        ctx = None
                    # a chained file/case inherits the previous context as-is (cookies/storage
                    # continuity is the point) -- its own device/viewport is ignored if it differs

                    case_results: list[CaseResult] = []
                    for i, case in enumerate(selected):
                        fresh = not chain and not (i > 0 and selected.chain[i])      # a prerequisite case continues in the browser it just used
                        if fresh and i > 0:
                            context.close()
                            context_options = resolve_context_options(
                                pw,
                                suite,
                                case=case,
                                device_override=self.orchestrator.device,
                                viewport_override=self.orchestrator.viewport,
                            )
                            context = browser.new_context(**context_options)
                            page = context.new_page()
                        if fresh:
                            ctx = None  # fresh variables for every case in a non-shared file

                        case_result, ctx = self.orchestrator.run_case(
                            page,
                            case,
                            ctx,
                            namespace=namespace,
                            default_step_delay_ms=suite.default_step_delay_ms,
                        )
                        case_results.append(case_result)
                        if case_result.status != "pass" and ((suite.on_case_fail == "stop" and policy != "continue") or policy == "stop_file"):
                            break

                    suite_started_at = datetime.now(timezone.utc).isoformat()
                    suite_result = SuiteResult(
                        suite_id=suite.suite_id,
                        environment=self.orchestrator.environment_label(suite),
                        started_at=suite_started_at,
                        finished_at=datetime.now(timezone.utc).isoformat(),
                        cases=case_results,
                        cases_in_file=len(suite.cases) if (file_entry.cases or file_entry.tags) else None,
                        selected=[c.id for c in selected] if (file_entry.cases or file_entry.tags) else None,
                    )
                    result.suites.append(suite_result)

                    log.log(30 if suite_result.failed else 20, "session %s: file %s -> %d/%d case(s) passed", plan.session_id, file_entry.path,
                            suite_result.passed, len(case_results))
                    if suite_result.failed > 0 and policy == "stop_session":
                        log.warning("session %s stopped after a failure in %s", plan.session_id, file_entry.path)
                        result.stopped_early = True
                        result.stopped_after_file = file_entry.path
                        break
            finally:
                if context is not None:
                    context.close()
                browser.close()

        result.finished_at = datetime.now(timezone.utc).isoformat()
        return result
