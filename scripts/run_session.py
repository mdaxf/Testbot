from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from framework.cli import add_common_run_args, run_session_command  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an ordered, multi-file test session")
    parser.add_argument("--plan", required=True, help="Path to a session plan (.yaml/.yml/.json)")
    add_common_run_args(parser)
    from framework.envfile import load_env_files

    load_env_files()
    args = parser.parse_args()
    return run_session_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
