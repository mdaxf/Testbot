from __future__ import annotations

from pathlib import Path
from typing import Iterable
from xml.etree.ElementTree import Element, ElementTree, SubElement

from framework.models import CaseResult, SessionResult, StepResult, SuiteResult
from framework.version import COPYRIGHT, PRODUCT, __version__

_CASE_COLORS = {"pass": "#1a7f37", "fail": "#cf222e", "error": "#9a6700", "inconclusive": "#6f42c1"}
_STEP_COLORS = {"pass": "#1a7f37", "fail": "#cf222e", "error": "#9a6700", "skipped": "#57606a", "inconclusive": "#6f42c1"}


def write_json_result(result: SuiteResult, path: str | Path) -> None:
    """The machine-readable evidence record. Mirrors the original suite/case/step
    structure -- same step_no/description order as the source test case -- with the
    action executed, the resolved data used, pass/fail/error status, actual vs.
    expected, and a screenshot path filled in on every step."""
    Path(path).write_text(result.model_dump_json(indent=2), encoding="utf-8")


def write_session_json_result(result: SessionResult, path: str | Path) -> None:
    """Same idea as write_json_result, but for a whole multi-file session: one suite
    entry per file, in the order they ran."""
    Path(path).write_text(result.model_dump_json(indent=2), encoding="utf-8")


def _build_testsuite_element(suite_result: SuiteResult) -> Element:
    testsuite = Element(
        "testsuite",
        {
            "name": suite_result.suite_id,
            "tests": str(len(suite_result.cases)),
            "failures": str(sum(1 for c in suite_result.cases if c.status == "fail")),
            "errors": str(sum(1 for c in suite_result.cases if c.status in ("error", "inconclusive"))),  # inconclusive must not look like a pass in CI
            "time": str(sum(c.duration_ms for c in suite_result.cases) / 1000),
        },
    )

    for case in suite_result.cases:
        testcase = SubElement(
            testsuite,
            "testcase",
            {
                "classname": suite_result.suite_id,
                "name": f"{case.case_id} - {case.title}",
                "time": str(case.duration_ms / 1000),
            },
        )
        if case.status == "fail":
            failing_step = next((s for s in case.steps if s.status == "fail"), None)
            message = f"Step {failing_step.step_no}: {failing_step.description}" if failing_step else "Failed"
            failure = SubElement(testcase, "failure", {"message": message})
            failure.text = _format_steps(case.steps)
        elif case.status in ("error", "inconclusive"):
            erroring_step = next((s for s in case.steps if s.status in ("error", "inconclusive")), None)
            message = erroring_step.error or erroring_step.description if erroring_step else "Error"
            error_el = SubElement(testcase, "error", {"message": message, "type": case.status})
            error_el.text = _format_steps(case.steps)

    return testsuite


def write_junit_xml(result: SuiteResult, path: str | Path) -> None:
    """Azure Pipelines' 'Publish Test Results' task ingests this directly into
    the ADO Tests tab -- no REST integration needed for CI-run suites."""
    ElementTree(_build_testsuite_element(result)).write(path, encoding="utf-8", xml_declaration=True)


def write_session_junit_xml(result: SessionResult, path: str | Path) -> None:
    """One <testsuite> per file, nested under <testsuites>, in the order they ran."""
    root = Element("testsuites", {"name": result.session_id})
    for suite in result.suites:
        root.append(_build_testsuite_element(suite))
    ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _format_steps(steps: Iterable[StepResult]) -> str:
    lines: list[str] = []
    for step in steps:
        lines.append(f"[{step.status.upper()}] step {step.step_no} ({step.action}): {step.description}")
        if step.data_used:
            lines.append(f"    data used: {step.data_used}")
        if step.error:
            lines.append(f"    error: {step.error}")
        if step.actual is not None or step.expected is not None:
            lines.append(f"    expected={step.expected!r} actual={step.actual!r}")
    return "\n".join(lines)


def _footer() -> str:
    return f'<hr><p style="color:#666;font-size:12px">{PRODUCT} {__version__} &middot; {COPYRIGHT}</p>'


def _step_row(step: StepResult) -> str:
    color = _STEP_COLORS.get(step.status, "#000000")
    evidence = f'<a href="{step.screenshot_path}">screenshot</a>' if step.screenshot_path else ""
    return (
        "<tr>"
        f"<td>{step.step_no}</td>"
        f"<td>{step.description}</td>"
        f"<td>{step.action}</td>"
        f"<td>{step.data_used or ''}</td>"
        f'<td style="color:{color}">{step.status.upper()}</td>'
        f"<td>{step.expected if step.expected is not None else ''}</td>"
        f"<td>{step.actual if step.actual is not None else ''}</td>"
        f"<td>{step.error or ''}</td>"
        f"<td>{evidence}</td>"
        "</tr>"
    )


def _case_block(case: CaseResult) -> str:
    color = _CASE_COLORS.get(case.status, "#000000")
    rows = "".join(_step_row(step) for step in case.steps)
    return (
        f'<h3 style="color:{color}">{case.case_id} — {case.title} [{case.status.upper()}]</h3>'
        "<table border='1' cellpadding='4' cellspacing='0'>"
        "<tr><th>Step</th><th>Description</th><th>Action</th><th>Data Used</th><th>Status</th>"
        "<th>Expected</th><th>Actual</th><th>Error</th><th>Evidence</th></tr>"
        f"{rows}</table>"
    )


def write_html_report(result: SuiteResult, path: str | Path) -> None:
    body = "".join(_case_block(case) for case in result.cases)
    html = (
        f"<html><head><title>Test Report - {result.suite_id}</title></head><body>"
        f"<h1>{result.suite_id} ({result.environment})</h1>"
        f"<p>Started: {result.started_at} — Finished: {result.finished_at}</p>"
        f"<p>Passed: {result.passed} / {len(result.cases)}</p>"
        f"{body}{_footer()}</body></html>"
    )
    Path(path).write_text(html, encoding="utf-8")


def write_session_html_report(result: SessionResult, path: str | Path) -> None:
    sections = []
    for suite in result.suites:
        body = "".join(_case_block(case) for case in suite.cases)
        sections.append(
            f"<h2>{suite.suite_id} ({suite.environment})</h2>"
            f"<p>Passed: {suite.passed} / {len(suite.cases)}</p>"
            f"{body}"
        )

    stopped_notice = (
        f"<p style='color:#cf222e'>Session stopped early after a failure in: {result.stopped_after_file}</p>"
        if result.stopped_early
        else ""
    )

    html = (
        f"<html><head><title>Session Report - {result.session_id}</title></head><body>"
        f"<h1>{result.session_id} — {result.session_name}</h1>"
        f"<p>Started: {result.started_at} — Finished: {result.finished_at}</p>"
        f"<p>Passed: {result.passed_cases} / {result.total_cases} cases across {len(result.suites)} file(s)</p>"
        f"{stopped_notice}"
        f"{''.join(sections)}{_footer()}</body></html>"
    )
    Path(path).write_text(html, encoding="utf-8")
