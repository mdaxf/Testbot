"""Optional `.env` file support: KEY=VALUE lines are loaded into the environment at startup.

Looked for (first found wins for a given variable, and real environment variables always win over any file):
  1. the folder you run the program from (current directory)
  2. the folder of the program itself (testbot.exe / the project folder)
  3. the workspace folder (manager)
Only NAMES are ever printed, never values. Keep the file private (it may hold API keys) and out of source control."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.lstrip("﻿").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        name, value = m.group(1), m.group(2)
        if value[:1] in ("'", '"') and value.endswith(value[:1]) and len(value) >= 2:
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()   # an unquoted value may end with " # comment"
        out[name] = value
    return out


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def load_env_files(extra_dirs: Iterable[Optional[Path]] = (), quiet: bool = False) -> list[str]:
    """Load .env files into os.environ without overriding anything already set. Returns the variable NAMES that were loaded."""
    loaded: list[str] = []
    seen: set[Path] = set()
    for folder in [Path.cwd(), app_dir(), *[Path(d) for d in extra_dirs if d]]:
        path = (folder / ".env").resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            values = parse(path.read_text(encoding="utf-8-sig", errors="replace"))
        except OSError:
            continue
        names = []
        for name, value in values.items():
            if name not in os.environ and value != "":
                os.environ[name] = value
                names.append(name)
        if names:
            loaded += names
            if not quiet:
                print(f"[testbot] loaded {len(names)} setting(s) from {path}: {', '.join(names)}", file=sys.stderr)
    return loaded
