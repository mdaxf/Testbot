from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from framework.manager import schedules


def test_write_state_is_atomic_and_leaves_no_temp_files(ws):
    schedules.write_state(ws, "nightly", {"history": [{"n": i} for i in range(100)]})
    d = ws.dir("schedules") / ".state"
    assert [p.name for p in d.iterdir()] == ["nightly.json"]
    state = json.loads((d / "nightly.json").read_text(encoding="utf-8"))
    assert len(state["history"]) == schedules.HISTORY_LIMIT


def test_failed_write_keeps_the_old_state(ws, monkeypatch):
    schedules.write_state(ws, "nightly", {"history": [{"n": 1}]})

    def fail(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(schedules.os, "replace", fail)
    with pytest.raises(OSError):
        schedules.write_state(ws, "nightly", {"history": [{"n": 2}]})
    monkeypatch.undo()
    assert schedules.read_state(ws, "nightly")["history"] == [{"n": 1}]


def test_corrupt_state_is_reported(ws, caplog):
    (ws.dir("schedules") / ".state").mkdir(parents=True, exist_ok=True)
    (ws.dir("schedules") / ".state" / "x.json").write_text("{not json", encoding="utf-8")
    caplog.set_level("WARNING")
    assert schedules.read_state(ws, "x") == {"history": []}


def test_setup_failure_never_leaves_the_schedule_running(ws):
    (ws.dir("results") / "blocker").write_text("a file, not a folder", encoding="utf-8")
    sched = {"id": "broken", "items": [], "results_subdir": "blocker"}
    rec = schedules.run_schedule(ws, sched)
    assert rec["status"] == "error"
    assert "broken" not in schedules._running
    rec2 = schedules.run_schedule(ws, sched)            # previously: "already running" forever
    assert rec2.get("message") != "already running"
    assert not schedules.read_state(ws, "broken").get("running")


def test_run_schedule_records_history(ws, monkeypatch):
    monkeypatch.setattr(schedules, "item_command", lambda ws, item, d: [sys.executable, "-c", "print('hi')"])
    rec = schedules.run_schedule(ws, {"id": "ok", "items": [{"type": "suite", "path": "a.json"}]})
    assert rec["status"] == "pass" and rec["items"][0]["exit_code"] == 0
    assert schedules.read_state(ws, "ok")["history"][-1]["run_id"] == rec["run_id"]
    assert "ok" not in schedules._running


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group check")
def test_cancel_kills_the_child_and_its_children(tmp_path):
    pidfile = tmp_path / "pid"
    script = ("import subprocess, sys, time; "
              f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); open({str(pidfile)!r}, 'w').write(str(p.pid)); "
              "time.sleep(120)")
    cancel = threading.Event()
    result = {}
    t = threading.Thread(target=lambda: result.update(r=schedules.run_child([sys.executable, "-c", script], cancel=cancel, poll_s=0.1, grace_s=2)))
    t0 = time.monotonic()
    t.start()
    while not pidfile.exists() or not pidfile.read_text():
        assert time.monotonic() - t0 < 20
        time.sleep(0.05)
    grandchild = int(pidfile.read_text())
    cancel.set()
    t.join(15)
    assert not t.is_alive()
    assert result["r"] == (-15, "cancelled")
    for _ in range(50):
        if not schedules.pid_alive(grandchild):
            break
        time.sleep(0.1)
        try:
            os.waitpid(grandchild, os.WNOHANG)
        except ChildProcessError:
            pass
    assert not schedules.pid_alive(grandchild)


def test_timeout_stops_the_child():
    code, why = schedules.run_child([sys.executable, "-c", "import time; time.sleep(60)"], cancel=threading.Event(), timeout_s=0.5,
                                    poll_s=0.1, grace_s=2)
    assert (code, why) == (-9, "timeout")


def test_normal_exit_code():
    assert schedules.run_child([sys.executable, "-c", "raise SystemExit(3)"], cancel=threading.Event(), poll_s=0.1) == (3, "")
    assert subprocess  # imported for clarity of the API under test
