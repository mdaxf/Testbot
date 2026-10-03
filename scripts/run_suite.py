from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from framework.cli import add_common_run_args, run_suite_command  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a test suite (Excel or JSON) against a web app")
    parser.add_argument("--suite", required=True, help="Path to a .json or .xlsx suite file")
    parser.add_argument("--env", help="Optional environment block from config/environments.yaml (fallback for base_url/connections; defaults to the suite's own `environment`, and suite-level values win)")
    add_common_run_args(parser)
    from framework.envfile import load_env_files

    load_env_files()
    args = parser.parse_args()
    return run_suite_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
