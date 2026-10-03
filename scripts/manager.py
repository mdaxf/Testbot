"""testbot manager: the browser UI for test cases, imports, schedules and results, plus the background scheduler.

    testbot-manager.exe                         open the UI (starts a local server and opens your browser)
    testbot-manager.exe ui [--port N] [--no-browser]
    testbot-manager.exe scheduler run           run the scheduler in the foreground (start tests when they are due)
    testbot-manager.exe scheduler run-once      start whatever is due right now, then exit (for an external timer)
    testbot-manager.exe scheduler install       start the scheduler automatically at Windows logon
    testbot-manager.exe scheduler uninstall
    testbot-manager.exe scheduler status
    testbot-manager.exe run-schedule <id>       run one schedule now and exit

Every command takes --workspace <folder or testbot-workspace.json>. The UI, the scheduler and testbot.exe share nothing
but the files in that workspace, so each can be used without the others."""
from __future__ import annotations

import argparse
import multiprocessing
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from framework.manager import schedules, scheduler, server  # noqa: E402
from framework.manager.workspace import Workspace, find_workspace_file  # noqa: E402


def main(argv=None) -> int:
    multiprocessing.freeze_support()
    try:
        sys.stdout.reconfigure(line_buffering=True, errors="replace")  # output redirected to a log file must appear as it happens
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(prog="testbot-manager", description="testbot manager: UI + scheduler")
    parser.add_argument("--workspace", help="Workspace folder or testbot-workspace.json (default: current folder, else next to the program)")
    from framework.version import banner

    parser.add_argument("--log-level", choices=["debug", "info", "warning", "error"], help="How much the manager (and the tests it starts) logs; default info. Overrides the Settings page and TESTBOT_LOG_LEVEL")
    parser.add_argument("--version", action="version", version=banner("testbot-manager"))
    sub = parser.add_subparsers(dest="command")

    def add_workspace(p):  # also accepted AFTER the subcommand (SUPPRESS: never overwrite one given before it)
        p.add_argument("--workspace", default=argparse.SUPPRESS, help="Workspace folder or testbot-workspace.json")
        return p

    ui = add_workspace(sub.add_parser("ui", help="Open the management UI (default)"))
    ui.add_argument("--port", type=int)
    ui.add_argument("--host")
    ui.add_argument("--no-browser", action="store_true")

    sch = add_workspace(sub.add_parser("scheduler", help="The background scheduler"))
    sch.add_argument("action", choices=["run", "run-once", "install", "uninstall", "status"])
    sch.add_argument("--at", choices=["logon", "startup"], default="logon", help="for install (startup needs administrator rights)")

    rs = add_workspace(sub.add_parser("run-schedule", help="Run one schedule now"))
    rs.add_argument("id")

    args = parser.parse_args(argv)
    ws = Workspace(find_workspace_file(getattr(args, "workspace", None)))
    from framework.envfile import load_env_files

    load_env_files([ws.root])  # optional .env (API keys ...) in the program, current or workspace folder; tests the manager starts inherit it

    import os

    from framework import logs

    level = args.log_level or (ws.data.get("logging") or {}).get("level") or None
    if level:
        os.environ["TESTBOT_LOG_LEVEL"] = level           # tests started from here inherit it (the Settings value is added per run as well)
    logs.configure(level, file=str(ws.root / "logs" / "manager.log"))

    if args.command in (None, "ui"):
        server.serve(ws, getattr(args, "host", None), getattr(args, "port", None), open_browser=not getattr(args, "no_browser", False))
        return 0
    if args.command == "scheduler":
        if args.action == "run":
            try:
                scheduler.run_loop(ws)
            except KeyboardInterrupt:
                print("scheduler stopped")
        elif args.action == "run-once":
            print(f"started {scheduler.run_due_once(ws)} schedule(s)")
        elif args.action == "install":
            print(scheduler.install(ws, args.at))
        elif args.action == "uninstall":
            print(scheduler.uninstall())
        else:
            hb = scheduler.heartbeat(ws)
            print("running (pid %s, last check %s)" % (hb.get("pid"), hb.get("last_beat")) if hb["running"] else "not running")
            print("automatic start:", "installed" if scheduler.installed() else "not installed")
        return 0
    if args.command == "run-schedule":
        record = schedules.run_schedule(ws, schedules.load_schedule(ws, args.id), trigger="manual")
        print(f"{args.id}: {record['status']}", *[f"\n  {i['n']}. {i['path']}: {i['status']}" for i in record.get("items", [])])
        return 0 if record["status"] == "pass" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
