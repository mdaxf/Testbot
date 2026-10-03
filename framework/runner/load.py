"""Load-test mode: run the same suite/session in N independent worker processes.

Each worker is its own OS process with its own browser, its own variables and its own SQL connections
(so a crash or a stuck page in one worker never affects another). Every worker runs the WHOLE test
independently; workers can repeat it for `iterations` times or until `duration_s` has elapsed, and are
started spread out over `ramp_up_s` seconds."""
from __future__ import annotations

import json
import multiprocessing
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

CONFIRM_ABOVE_WORKERS = 10  # more than this needs an explicit --confirm-load


@dataclass
class LoadSettings:
    workers: int = 1
    iterations: Optional[int] = 1   # None = keep going until duration_s
    duration_s: Optional[float] = None
    ramp_up_s: float = 0.0

    @property
    def is_load(self) -> bool:
        return self.workers > 1 or self.iterations != 1 or self.duration_s is not None


def resolve_load(args: Any, source: Any) -> LoadSettings:
    """CLI flags win over the suite/session-plan settings; unset means: 1 worker, 1 iteration."""
    def pick(cli: Any, file_value: Any) -> Any:
        return cli if cli is not None else file_value

    workers = pick(getattr(args, "workers", None), getattr(source, "workers", None)) or 1
    duration = pick(getattr(args, "duration", None), getattr(source, "duration_s", None))
    iterations = pick(getattr(args, "iterations", None), getattr(source, "iterations", None))
    if iterations is None:
        iterations = None if duration else 1
    ramp = pick(getattr(args, "ramp_up", None), getattr(source, "ramp_up_s", None)) or 0.0
    if workers < 1 or (iterations is not None and iterations < 1) or (duration is not None and duration <= 0) or ramp < 0:
        raise ValueError("workers and iterations must be >= 1, duration > 0, ramp-up >= 0")
    return LoadSettings(workers=int(workers), iterations=iterations, duration_s=duration, ramp_up_s=float(ramp))


def _worker_main(runner: Callable[..., dict], args_dict: dict[str, Any], load: LoadSettings,
                 worker_id: int, delay_s: float, out: "multiprocessing.Queue") -> None:
    """One worker process: wait for its ramp-up slot, then repeat the run until the limits are reached."""
    from framework import logs

    logs.configure()                      # a worker is a new process: logging comes from the environment (TESTBOT_LOG_LEVEL ...)
    logs.set_tag(f"w{worker_id:02d}")
    try:
        time.sleep(delay_s)
        started = time.monotonic()
        iteration = 0
        while True:
            iteration += 1
            t0 = time.monotonic()
            try:
                summary = runner(args_dict, worker_id, iteration)
            except Exception as exc:  # noqa: BLE001 - a broken iteration is a result, not a crash
                summary = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            out.put({"type": "iteration", "worker": worker_id, "iteration": iteration,
                     "seconds": round(time.monotonic() - t0, 2), **summary})
            if load.iterations is not None and iteration >= load.iterations:
                break
            if load.duration_s is not None and time.monotonic() - started >= load.duration_s:
                break
    finally:
        out.put({"type": "done", "worker": worker_id})


def run_load(runner: Callable[..., dict], args_dict: dict[str, Any], load: LoadSettings, report_dir: Path,
             log: Callable[[str], None] = print) -> dict[str, Any]:
    """Start the workers, stream a line per finished iteration, and return/write the summary.
    `runner(args_dict, worker_id, iteration) -> {"status": "pass"|"fail"|"error", ...}` must be a module-level
    function (it is sent to the worker processes)."""
    mp = multiprocessing.get_context("spawn")
    queue = mp.Queue()
    n = load.workers
    started = time.monotonic()
    procs = []
    for i in range(1, n + 1):
        delay = load.ramp_up_s * (i - 1) / (n - 1) if n > 1 else 0.0
        p = mp.Process(target=_worker_main, args=(runner, args_dict, load, i, delay, queue), name=f"worker-{i:02d}")
        p.start()
        procs.append(p)
    log(f"Load run: {n} worker(s), iterations={load.iterations if load.iterations is not None else 'until duration'}, "
        f"duration={load.duration_s or '-'}s, ramp-up={load.ramp_up_s}s")

    results: list[dict[str, Any]] = []
    done = 0
    while done < n:
        try:
            msg = queue.get(timeout=1.0)
        except Exception:  # queue.Empty -- also notice workers that died without saying goodbye
            if all(not p.is_alive() for p in procs) and queue.empty():
                break
            continue
        if msg["type"] == "done":
            done += 1
            continue
        results.append(msg)
        log(f"  [worker {msg['worker']:02d} iter {msg['iteration']:03d}] {msg['status'].upper():5s} {msg['seconds']:6.1f}s"
            + (f"  {msg['error'][:120]}" if msg.get("error") else ""))
    for p in procs:
        p.join(timeout=30)

    elapsed = time.monotonic() - started
    summary = summarize(results, load, elapsed, [p.exitcode for p in procs])
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "load-summary.json").write_text(json.dumps({"settings": asdict(load), **summary, "iterations": results}, indent=2), encoding="utf-8")
    return summary


def summarize(results: list[dict[str, Any]], load: LoadSettings, elapsed: float, exit_codes: list) -> dict[str, Any]:
    by_worker: dict[int, dict[str, Any]] = {}
    for r in results:
        w = by_worker.setdefault(r["worker"], {"worker": r["worker"], "iterations": 0, "pass": 0, "fail": 0, "error": 0, "seconds": 0.0})
        w["iterations"] += 1
        w[r["status"]] = w.get(r["status"], 0) + 1
        w["seconds"] += r["seconds"]
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "pass")
    times = sorted(r["seconds"] for r in results)
    return {
        "elapsed_s": round(elapsed, 1), "iterations_run": total, "passed": passed, "failed": total - passed,
        "iterations_per_min": round(total / elapsed * 60, 1) if elapsed else 0,
        "iteration_seconds": {"min": times[0], "avg": round(sum(times) / total, 2), "max": times[-1]} if total else {},
        "workers": [by_worker[k] | {"seconds": round(by_worker[k]["seconds"], 1)} for k in sorted(by_worker)],
        "worker_exit_codes": exit_codes,
    }


def format_summary(summary: dict[str, Any]) -> str:
    lines = ["", f"Load run finished in {summary['elapsed_s']}s: {summary['iterations_run']} iteration(s), "
                 f"{summary['passed']} passed, {summary['failed']} failed/errored, {summary['iterations_per_min']} iterations/min"]
    if summary.get("iteration_seconds"):
        t = summary["iteration_seconds"]
        lines.append(f"Iteration time: min {t['min']}s / avg {t['avg']}s / max {t['max']}s")
    lines.append("Worker  Iterations  Pass  Fail  Error")
    for w in summary["workers"]:
        lines.append(f"  {w['worker']:02d}    {w['iterations']:>8}  {w['pass']:>4}  {w['fail']:>4}  {w['error']:>5}")
    bad = [c for c in summary["worker_exit_codes"] if c not in (0, None)]
    if bad:
        lines.append(f"WARNING: {len(bad)} worker process(es) exited abnormally (codes {bad}) -- results may be incomplete")
    return "\n".join(lines)
