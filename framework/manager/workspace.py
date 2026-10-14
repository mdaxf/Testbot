"""Workspace settings: where the test cases, results, schedules and config live.

One small JSON file (testbot-workspace.json). The UI, the scheduler and testbot.exe share nothing but files, so
each can run without the others."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

FILE_NAME = "testbot-workspace.json"

DEFAULTS: dict[str, Any] = {
    "test_cases_dir": "test_cases",
    "results_dir": "reports",
    "schedules_dir": "schedules",
    "config_dir": "config",
    "groups_dir": "groups",         # test groups (sessions / suites / cases tracked together, e.g. a UAT cycle)
    "runner_command": "",           # empty = auto-detect testbot.exe next to the workspace / on PATH
    "ai": {"provider": "anthropic", "model": "", "ssl_ca_bundle_file": "", "ssl_use_os_truststore": True, "proxy": ""},   # the API key always comes from an environment variable
    "agent": {"mode": "", "provider": "", "model": "", "vision": "auto", "max_steps": 40, "allow_destructive": False, "learn": False, "profile": ""},
    "email": {},                    # default SMTP settings (server, port, security, username, password_env, from, to, on, attach_report); an environment's email block overrides them
    "logging": {"level": ""},       # debug | info | warning | error ; empty = TESTBOT_LOG_LEVEL or info. Applies to the manager and to the tests it starts
    "server": {"host": "127.0.0.1", "port": 8780},
}

# tree name -> setting key
KINDS = {"test_cases": "test_cases_dir", "results": "results_dir", "schedules": "schedules_dir", "config": "config_dir", "groups": "groups_dir"}


class WorkspaceError(Exception):
    pass


# One rule for every id that becomes a file or folder name (schedules, groups, run ids, revision ids): letters, digits,
# '_', '-' and '.', not starting with '.', at most 60 characters -- so an id can never contain a path separator or '..'.
ID_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,59}$")


def check_id(value: Any, what: str = "id") -> str:
    """`value` as a safe id, or WorkspaceError (a ValueError-free 400 in the API)."""
    sid = str(value if value is not None else "").strip()
    if not ID_RE.match(sid):
        raise WorkspaceError(f"{what}: letters, digits, '-', '_' or '.' (max 60 characters, not starting with '.')")
    return sid


def resolve_within(base: Path, *parts: Any) -> Path:
    """`base / parts...` resolved; refused (WorkspaceError) if the result is not strictly inside `base`."""
    base = Path(base).resolve()
    target = base.joinpath(*(str(p) for p in parts)).resolve()
    if base not in target.parents:
        raise WorkspaceError("path is outside the workspace folder")
    return target


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def find_workspace_file(explicit: Optional[str] = None) -> Path:
    """--workspace argument > TESTBOT_WORKSPACE env > ./testbot-workspace.json > <app folder>/testbot-workspace.json."""
    if explicit:
        p = Path(explicit)
        return p / FILE_NAME if p.is_dir() else p
    if os.environ.get("TESTBOT_WORKSPACE"):
        p = Path(os.environ["TESTBOT_WORKSPACE"])
        return p / FILE_NAME if p.is_dir() else p
    here = Path.cwd() / FILE_NAME
    if here.exists():
        return here
    return app_dir() / FILE_NAME


class Workspace:
    def __init__(self, file: Path):
        self.file = Path(file).resolve()
        self.root = self.file.parent
        self.data: dict[str, Any] = json.loads(json.dumps(DEFAULTS))
        if self.file.exists():
            try:
                stored = json.loads(self.file.read_text(encoding="utf-8-sig"))
            except ValueError as exc:
                raise WorkspaceError(f"{self.file} is not valid JSON: {exc}") from exc
            self._merge(stored)
        else:
            self.save()

    def _merge(self, stored: dict[str, Any]) -> None:
        for key, value in stored.items():
            if isinstance(value, dict) and isinstance(self.data.get(key), dict):
                self.data[key].update(value)
            else:
                self.data[key] = value

    # ---- persistence
    def save(self, new_data: Optional[dict[str, Any]] = None) -> None:
        if new_data is not None:
            self._merge({k: v for k, v in new_data.items() if k in DEFAULTS})
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    # ---- folders
    def dir(self, kind: str) -> Path:
        if kind not in KINDS:
            raise WorkspaceError(f"unknown folder kind '{kind}'")
        p = Path(self.data[KINDS[kind]])
        return (p if p.is_absolute() else self.root / p).resolve()

    def ensure_dirs(self) -> None:
        for kind in KINDS:
            self.dir(kind).mkdir(parents=True, exist_ok=True)

    def safe_path(self, kind: str, rel: str) -> Path:
        """`rel` (as sent by the browser) resolved inside the kind's folder; anything that escapes it is refused."""
        base = self.dir(kind)
        target = (base / rel).resolve()
        if target != base and base not in target.parents:
            raise WorkspaceError("path is outside the workspace folder")
        return target

    def rel(self, kind: str, path: Path) -> str:
        return path.resolve().relative_to(self.dir(kind)).as_posix()

    # ---- runner
    def runner_argv(self) -> Optional[list[str]]:
        """Command prefix that starts the test runner: ['testbot.exe'] or [python, scripts/testbot.py]."""
        configured = self.data.get("runner_command", "").strip()
        if configured:
            return [configured] if Path(configured).exists() or shutil.which(configured) else None
        for candidate in (self.root / "testbot.exe", app_dir() / "testbot.exe", app_dir().parent / "testbot.exe"):
            if candidate.exists():
                return [str(candidate)]
        found = shutil.which("testbot") or shutil.which("testbot.exe")
        if found:
            return [found]
        script = app_dir() / "scripts" / "testbot.py"
        if script.exists() and not getattr(sys, "frozen", False):
            return [sys.executable, str(script)]
        return None
