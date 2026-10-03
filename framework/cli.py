from __future__ import annotations

import argparse
import sys
from typing import Optional
from pathlib import Path

import yaml

from framework.actions.sql_actions import SqlConnections
from framework.loaders.excel_loader import load_suite_from_excel
from framework.loaders.json_loader import load_suite_from_json
from framework.loaders.session_loader import load_session_plan
from framework.runner.context_options import parse_viewport_arg
from framework.runner.load import CONFIRM_ABOVE_WORKERS, LoadSettings, format_summary, resolve_load, run_load
from framework.runner.orchestrator import Orchestrator
from framework.runner.reporting import (
    write_html_report,
    write_json_result,
    write_junit_xml,
    write_session_html_report,
    write_session_json_result,
    write_session_junit_xml,
)
from framework.runner.session_runner import SessionRunner


def app_root() -> Path:
    """Where config/ and (by convention) test_cases/ live. When frozen by PyInstaller,
    that's next to the .exe itself, not inside the temp extraction dir sys.executable's
    module machinery would otherwise imply -- a packaged exe ships with an editable
    config/environments.yaml sitting beside it, not baked in read-only."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def load_environment_config(env_name: Optional[str]) -> dict:
    """Optional fallback: with no environment name, everything must come from the suite itself."""
    if not env_name:
        return {}
    config_path = app_root() / "config" / "environments.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"No config/environments.yaml found at {config_path}")
    all_envs = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if env_name not in all_envs:
        raise KeyError(f"Environment '{env_name}' not found in {config_path}")
    return all_envs[env_name]


def add_common_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--log-level", choices=["debug", "info", "warning", "error"],
                        help="How much to log (default: info = one line per step). Overrides TESTBOT_LOG_LEVEL. debug adds the data of each step, SQL and agent reasoning.")
    parser.add_argument("--headed", action="store_true", help="Run with a visible browser window")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--device", help="Playwright device name, e.g. 'iPhone 13' -- overrides every file/case's own Device")
    parser.add_argument(
        "--viewport",
        help="Custom size WIDTHxHEIGHT, e.g. '390x844' -- overrides every file/case's own Viewport; ignored if --device is set",
    )
    parser.add_argument("--mode", choices=["script", "agentic", "auto"],
                        help="script = run scripted steps (default) | agentic = natural-language cases are executed by the AI agent | auto = script when there "
                             "is one, agent otherwise. Overrides TESTBOT_MODE and the suite/case `mode`.")
    load = parser.add_argument_group("load test (run the same test in several independent worker processes)")
    load.add_argument("--workers", type=int, help="Parallel worker processes, each running the whole test on its own (default 1)")
    load.add_argument("--iterations", type=int, help="Times each worker repeats the run (default 1)")
    load.add_argument("--duration", type=float, help="Seconds each worker keeps starting new iterations")
    load.add_argument("--ramp-up", type=float, help="Seconds over which the workers are started, evenly spread (default 0)")
    load.add_argument("--screenshots", choices=["all", "fail", "none"],
                      help="Step screenshots to keep (default: all; fail in load runs)")
    load.add_argument("--confirm-load", action="store_true",
                      help=f"Required to run more than {CONFIRM_ABOVE_WORKERS} workers (protects shared servers from accidents)")


def build_orchestrator(
    env_config: dict, args: argparse.Namespace, *, report_dir: str | Path | None = None,
    extra_vars: dict | None = None, screenshots: str = "all",
) -> tuple[Orchestrator, SqlConnections]:
    sql_connections = SqlConnections(env_config.get("connections", {}))
    orchestrator = Orchestrator(
        base_url=env_config.get("base_url", ""),
        sql_connections=sql_connections,
        headless=not args.headed,
        report_dir=str(report_dir if report_dir is not None else args.report_dir),
        device=args.device,
        viewport=parse_viewport_arg(args.viewport),
        extra_vars=extra_vars,
        screenshots=screenshots,
        mode=getattr(args, "mode", None),
    )
    return orchestrator, sql_connections


def _load_suite_file(path: Path):
    if path.suffix.lower() == ".json":
        return load_suite_from_json(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        return load_suite_from_excel(path)
    raise ValueError(f"Unsupported suite file type: {path.suffix}")


def _screenshot_mode(args: argparse.Namespace, load: LoadSettings) -> str:
    return getattr(args, "screenshots", None) or ("fail" if load.is_load else "all")


def _run_suite_once(args: argparse.Namespace, suite, report_dir: Path, extra_vars: dict, screenshots: str):
    env_config = load_environment_config(args.env or suite.environment)
    orchestrator, sql_connections = build_orchestrator(
        env_config, args, report_dir=report_dir, extra_vars=extra_vars, screenshots=screenshots)
    try:
        result = orchestrator.run_suite(suite)
    finally:
        sql_connections.close_all()
    report_dir.mkdir(parents=True, exist_ok=True)
    write_html_report(result, report_dir / f"{suite.suite_id}.html")
    write_junit_xml(result, report_dir / f"{suite.suite_id}-junit.xml")
    write_json_result(result, report_dir / f"{suite.suite_id}-result.json")
    return result


def _run_session_once(args: argparse.Namespace, plan, report_dir: Path, extra_vars: dict, screenshots: str):
    env_config = load_environment_config(plan.environment)
    orchestrator, sql_connections = build_orchestrator(
        env_config, args, report_dir=report_dir, extra_vars=extra_vars, screenshots=screenshots)
    try:
        result = SessionRunner(orchestrator).run(plan)
    finally:
        sql_connections.close_all()
    report_dir.mkdir(parents=True, exist_ok=True)
    write_session_html_report(result, report_dir / f"{plan.session_id}.html")
    write_session_junit_xml(result, report_dir / f"{plan.session_id}-junit.xml")
    write_session_json_result(result, report_dir / f"{plan.session_id}-result.json")
    return result


# ---- load-mode workers (module-level so they can be sent to the worker processes) ------------------------

def _worker_report_dir(args_dict: dict, worker_id: int, iteration: int) -> Path:
    return Path(args_dict["report_dir"]) / f"worker-{worker_id:02d}" / f"iter-{iteration:03d}"


def _status_of(bad_cases: list) -> str:
    if not bad_cases:
        return "pass"
    return "fail" if all(c.status == "fail" for c in bad_cases) else "error"


def suite_iteration(args_dict: dict, worker_id: int, iteration: int) -> dict:
    args = argparse.Namespace(**args_dict)
    suite = _load_suite_file(Path(args.suite))
    result = _run_suite_once(args, suite, _worker_report_dir(args_dict, worker_id, iteration),
                             {"worker_id": worker_id, "iteration": iteration}, args_dict["_screenshots"])
    bad = [c for c in result.cases if c.status != "pass"]
    first = next((s.error or f"expected {s.expected!r}, got {s.actual!r}" for c in bad for s in c.steps if s.status != "pass"), None)
    return {"status": _status_of(bad), "cases": len(result.cases), "failed_cases": len(bad), "error": first}


def session_iteration(args_dict: dict, worker_id: int, iteration: int) -> dict:
    args = argparse.Namespace(**args_dict)
    plan = load_session_plan(args.plan)
    result = _run_session_once(args, plan, _worker_report_dir(args_dict, worker_id, iteration),
                               {"worker_id": worker_id, "iteration": iteration}, args_dict["_screenshots"])
    bad = [c for s in result.suites for c in s.cases if c.status != "pass"]
    first = next((st.error or f"expected {st.expected!r}, got {st.actual!r}" for s in result.suites for c in s.cases
                  for st in c.steps if st.status != "pass"), None)
    return {"status": _status_of(bad), "cases": result.total_cases, "failed_cases": len(bad), "error": first}


def _run_load(args: argparse.Namespace, load: LoadSettings, runner) -> int:
    if load.workers > CONFIRM_ABOVE_WORKERS and not args.confirm_load:
        print(f"Refusing to start {load.workers} workers without --confirm-load "
              f"(more than {CONFIRM_ABOVE_WORKERS} can overload a shared server). Re-run with --confirm-load if that is intended.")
        return 2
    args_dict = vars(args) | {"_screenshots": _screenshot_mode(args, load)}
    summary = run_load(runner, args_dict, load, Path(args.report_dir))
    print(format_summary(summary))
    print(f"\nPer-worker reports and load-summary.json in {Path(args.report_dir).resolve()}")
    return 0 if summary["failed"] == 0 and summary["iterations_run"] > 0 else 1


def setup_logging(args: argparse.Namespace) -> None:
    """Console + per-run run.log. --log-level is also exported so load-test worker processes use the same level."""
    import os
    from framework import logs

    if getattr(args, "log_level", None):
        os.environ["TESTBOT_LOG_LEVEL"] = args.log_level
    logs.configure()


def run_suite_command(args: argparse.Namespace) -> int:
    setup_logging(args)
    suite = _load_suite_file(Path(args.suite))
    load = resolve_load(args, suite)
    if load.is_load:
        return _run_load(args, load, suite_iteration)

    result = _run_suite_once(args, suite, Path(args.report_dir), {"worker_id": 1, "iteration": 1}, _screenshot_mode(args, load))
    print(f"\n{suite.suite_id}: {result.passed}/{len(result.cases)} cases passed")
    for case in result.cases:
        marker = "PASS" if case.status == "pass" else case.status.upper()
        print(f"  [{marker}] {case.case_id} - {case.title}")
    print(f"\nReports written to {Path(args.report_dir).resolve()}")

    return 0 if result.failed == 0 else 1


def run_session_command(args: argparse.Namespace) -> int:
    setup_logging(args)
    plan = load_session_plan(args.plan)
    load = resolve_load(args, plan)
    if load.is_load:
        return _run_load(args, load, session_iteration)

    result = _run_session_once(args, plan, Path(args.report_dir), {"worker_id": 1, "iteration": 1}, _screenshot_mode(args, load))
    print(f"\n{plan.session_id}: {result.passed_cases}/{result.total_cases} cases passed across {len(result.suites)} file(s)")
    for suite in result.suites:
        for case in suite.cases:
            marker = "PASS" if case.status == "pass" else case.status.upper()
            print(f"  [{marker}] ({suite.suite_id}) {case.case_id} - {case.title}")
    if result.stopped_early:
        print(f"\nSession stopped early after a failure in file: {result.stopped_after_file}")
    print(f"\nReports written to {Path(args.report_dir).resolve()}")

    return 0 if all(s.failed == 0 for s in result.suites) else 1
