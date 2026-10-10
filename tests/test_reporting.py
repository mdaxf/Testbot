from __future__ import annotations

from framework.models import CaseResult, SessionResult, StepResult, SuiteResult
from framework.runner.reporting import write_html_report, write_session_html_report

EVIL = '<script>alert(document.cookie)</script>'
ATTR = '" onerror="alert(1)'


def _suite() -> SuiteResult:
    step = StepResult(step_no=1, description=EVIL, action="click", data_used=EVIL, status="fail", actual=EVIL, expected=EVIL,
                      error=EVIL, screenshot_path=ATTR)
    case = CaseResult(case_id="TC-<1>", title=EVIL, status="fail", steps=[step], finished_at="2026-10-10T10:00:00+00:00")
    return SuiteResult(suite_id=EVIL, environment=EVIL, started_at="2026-10-10T09:00:00+00:00", finished_at="2026-10-10T10:00:00+00:00",
                       cases=[case], cases_in_file=3, selected=[EVIL])


def test_suite_report_escapes_every_value(tmp_path):
    out = tmp_path / "r.html"
    write_html_report(_suite(), out)
    html = out.read_text(encoding="utf-8")
    assert "<script>" not in html
    assert "&lt;script&gt;alert(document.cookie)&lt;/script&gt;" in html
    assert 'onerror="alert' not in html
    assert "&quot; onerror=&quot;alert(1)" in html
    assert "TC-&lt;1&gt;" in html


def test_session_report_escapes_every_value(tmp_path):
    out = tmp_path / "s.html"
    sess = SessionResult(session_id=EVIL, session_name=EVIL, started_at="x", finished_at="y", suites=[_suite()], stopped_early=True,
                         stopped_after_file=EVIL)
    write_session_html_report(sess, out)
    html = out.read_text(encoding="utf-8")
    assert "<script>" not in html
    assert html.count("&lt;script&gt;") >= 8


def test_report_still_has_structure(tmp_path):
    out = tmp_path / "r.html"
    write_html_report(_suite(), out)
    html = out.read_text(encoding="utf-8")
    assert "<table" in html and "<td" in html and '<a href="' in html
