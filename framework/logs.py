"""Run log: one line per event, sorted by level and by area.

    2026-10-02 13:05:01 INFO    step      TC-01 step 3 PASS  click  Click Save (120 ms)
    2026-10-02 13:05:02 WARNING step      TC-01 step 4 FAIL  assert Order is shown -- expected 'Open', actual 'Closed'
    2026-10-02 13:05:02 ERROR   step      TC-01 step 5 ERROR click  Click Print -- element not found

Levels
    DEBUG    what is about to happen and with which data (secrets masked), SQL text and row counts, variable names, screenshot files, agent reasoning
    INFO     suite / case start and finish, every step with its result and time, every agent action        (default)
    WARNING  a failed check, an inconclusive case, a fallback that was needed
    ERROR    a step or case that could not run (exception), an agent that could not start

Areas (the second column): runner, step, sql, agent, manager, scheduler.

Switch it with   TESTBOT_LOG_LEVEL=debug|info|warning|error      or   --log-level debug
Per area         TESTBOT_LOG_LEVELS=agent=debug,sql=warning
Extra file       TESTBOT_LOG_FILE=C:\\logs\\testbot.log           (every run also writes run.log into its own report folder)
Console          TESTBOT_LOG_CONSOLE=off  to silence the console (the files are still written)
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = "testbot"
LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "warn": logging.WARNING, "error": logging.ERROR}
DEFAULT_LEVEL = "info"
_FORMAT = "%(asctime)s %(levelname)-7s %(area)-9s %(tag)s%(message)s"
_SECRET_WORDS = re.compile(r"pass(word|wd)?|pwd|secret|token|api[_-]?key|credential", re.I)
_SECRET_ASSIGN = re.compile(r"(?i)\b([\w.-]*(?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key)[\w.-]*)\s*([=:])\s*[^\s,;|&]+")

_console: Optional[logging.Handler] = None
_global_file: Optional[logging.Handler] = None
_run_file: Optional[logging.Handler] = None
_tag = ""


class _AreaFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.area = record.name[len(ROOT) + 1:] if record.name.startswith(ROOT + ".") else record.name
        record.tag = f"[{_tag}] " if _tag else ""
        return super().format(record)


def set_tag(tag: str) -> None:
    """A label put in front of every message of this process, e.g. the load-test worker (w03)."""
    global _tag
    _tag = tag


def get(area: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT}.{area}")


def parse_level(text: Optional[str], default: str = DEFAULT_LEVEL) -> int:
    return LEVELS.get((text or default).strip().lower(), LEVELS[default])


def _flag(value: Optional[str], default: bool = True) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in ("0", "off", "false", "no")


def looks_secret(*texts: Optional[str]) -> bool:
    return any(t and _SECRET_WORDS.search(str(t)) for t in texts)


def redact(text: Any) -> str:
    """Mask `password=...` style assignments in free text (SQL, URLs, messages)."""
    return _SECRET_ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}***", str(text))


def clip(text: Any, n: int = 300) -> str:
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _swap(old: Optional[logging.Handler], new: Optional[logging.Handler]) -> Optional[logging.Handler]:
    root = logging.getLogger(ROOT)
    if old is not None:
        root.removeHandler(old)
        try:
            old.close()
        except Exception:  # noqa: BLE001
            pass
    if new is not None:
        root.addHandler(new)
    return new


def configure(level: Optional[str] = None, *, file: Optional[str] = None, console: Optional[bool] = None, env: Optional[dict[str, str]] = None) -> None:
    """Set up the `testbot` loggers. Safe to call again (it replaces its own handlers). Arguments win over the environment."""
    global _console, _global_file
    env = os.environ if env is None else env
    root = logging.getLogger(ROOT)
    root.propagate = False
    root.setLevel(parse_level(level or env.get("TESTBOT_LOG_LEVEL")))
    for pair in (env.get("TESTBOT_LOG_LEVELS") or "").split(","):          # per-area levels: agent=debug,sql=warning
        if "=" in pair:
            area, lvl = pair.split("=", 1)
            if area.strip():
                get(area.strip()).setLevel(parse_level(lvl, default=logging.getLevelName(root.level).lower()))
    fmt = _AreaFormatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")

    show = _flag(env.get("TESTBOT_LOG_CONSOLE")) if console is None else console
    stream = sys.stderr or sys.stdout          # a windowed program (the recorder) has neither: then there is no console output
    handler: Optional[logging.Handler] = None
    if show and stream is not None:
        handler = logging.StreamHandler(stream)
        handler.setFormatter(fmt)
    _console = _swap(_console, handler)

    path = file or env.get("TESTBOT_LOG_FILE")
    fh: Optional[logging.Handler] = None
    if path:
        try:
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(str(Path(path).expanduser()), maxBytes=5_000_000, backupCount=3, encoding="utf-8")
            fh.setFormatter(fmt)
        except OSError as exc:
            root.warning("cannot write the log file %s: %s", path, exc)
    _global_file = _swap(_global_file, fh)


def attach_run_log(folder: Path) -> Optional[Path]:
    """Also write everything from now on to <folder>/run.log (one file per run; the previous one is closed)."""
    global _run_file
    if logging.getLogger(ROOT).level == logging.NOTSET:      # nobody configured logging (library use): apply the defaults
        configure()
    path = Path(folder) / "run.log"
    try:
        handler = logging.FileHandler(str(path), mode="a", encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(_AreaFormatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    _run_file = _swap(_run_file, handler)
    return path


def detach_run_log() -> None:
    global _run_file
    _run_file = _swap(_run_file, None)


def env_for_child(level: Optional[str]) -> dict[str, str]:
    return {"TESTBOT_LOG_LEVEL": level.strip().lower()} if level and level.strip().lower() in LEVELS else {}
