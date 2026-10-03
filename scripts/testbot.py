from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
from pathlib import Path

# This entrypoint always expects Playwright's browsers bundled locally inside the
# playwright package tree (installed via `PLAYWRIGHT_BROWSERS_PATH=0 playwright install
# chromium`), not the global user cache -- required for a self-contained package build
# where nothing downloads at runtime. Must be set before sync_playwright() is invoked.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from framework.cli import add_common_run_args, run_session_command, run_suite_command  # noqa: E402


def main() -> int:
    multiprocessing.freeze_support()  # load-test worker processes re-launch this exe; must run first
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    from framework.envfile import load_env_files

    load_env_files()  # optional .env next to the program / in the current folder (real environment variables win)
    parser = argparse.ArgumentParser(prog="testbot", description="Web UI test framework -- single-file build")
    from framework.version import banner

    parser.add_argument("--version", action="version", version=banner("testbot"))
    subparsers = parser.add_subparsers(dest="command")  # no subcommand (e.g. double-click) opens the recorder
    subparsers.add_parser("record", help="Open the recorder app: browse a site and auto-generate a test case")

    suite_parser = subparsers.add_parser("suite", help="Run one test suite (Excel or JSON)")
    suite_parser.add_argument("--suite", required=True, help="Path to a .json or .xlsx suite file")
    suite_parser.add_argument("--env", help="Optional environment block from config/environments.yaml (fallback for base_url/connections; defaults to the suite's own `environment`, and suite-level values win)")
    add_common_run_args(suite_parser)

    convert_parser = subparsers.add_parser("convert-apriso", help="Convert an Apriso AutomaticTest JSON scenario into a testbot suite")
    convert_parser.add_argument("--in", dest="src", required=True, help="Apriso scenario JSON (TestCase/Screen/Elements)")
    convert_parser.add_argument("--out", help="Output suite path (default: <input>.testbot.json next to the input)")
    convert_parser.add_argument("--map", help="Control map (default: config/apriso_control_map.yaml)")

    session_parser = subparsers.add_parser("session", help="Run an ordered, multi-file test session")
    session_parser.add_argument("--plan", required=True, help="Path to a session plan (.yaml/.yml/.json)")
    add_common_run_args(session_parser)

    args = parser.parse_args()
    if args.command in (None, "record"):
        from framework.recorder.app import main as record_main

        return record_main()
    if args.command == "convert-apriso":
        from framework.cli import app_root
        from framework.converters.apriso_bpa import convert_file

        src = Path(args.src)
        dst = Path(args.out) if args.out else src.with_name(src.stem + ".testbot.json")
        warnings = convert_file(src, dst, Path(args.map) if args.map else app_root() / "config" / "apriso_control_map.yaml")
        print(f"Wrote {dst}")
        for w in warnings:
            print(f"  WARNING: {w}")
        return 0
    if args.command == "suite":
        return run_suite_command(args)
    return run_session_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
