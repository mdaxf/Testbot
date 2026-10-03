"""Standalone entry point for the recorder app (windowed build: no console)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Bundled Chromium lives inside the playwright package tree, same as testbot.exe (see testbot.spec).
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from framework.recorder.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
