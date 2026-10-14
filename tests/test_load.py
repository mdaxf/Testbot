from __future__ import annotations

import time
from pathlib import Path

from framework.runner.load import LoadSettings, run_load


def _hang(_args, _worker, _iteration):
    time.sleep(30)
    return {"status": "pass"}


def test_duration_stops_a_hung_iteration(tmp_path: Path):
    started = time.monotonic()
    summary = run_load(
        _hang, {}, LoadSettings(workers=1, iterations=None, duration_s=0.4), tmp_path, log=lambda *_: None, stop_grace_s=0.5,
    )
    assert time.monotonic() - started < 20
    assert summary["iterations_run"] == 0
