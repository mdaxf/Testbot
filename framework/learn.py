"""What the agent learns, as a RECOMMENDATION on the test case.

After a passing agentic run with the `learn` option on, the steps the agent actually used -- with the elements it identified for them and the checks it
proved -- are written into the case as an extra node, `ai_recommendation`. The runner never executes the node, and it is not part of a revision, so nothing
about the test changes until a person reviews it in the manager (replace, merge with the original steps, or skip -- partly or fully).

    "ai_recommendation": {
        "status": "pending",             pending | applied | skipped
        "kind": "agent",                 agent (steps the agent used) | heal (healed targets of a scripted case; each step has `replaces_step`)
        "created": "...", "run": "SUITE_20261005T101500Z", "model": "openai:gpt-...", "verdict": "pass",
        "base_revision": 3,              the revision of the case it was made against
        "steps": [ {action, description, target, input, expected, ...}, ... ],
        "variables": { ... },            variables the suggested steps use
        "notes": [ ... ],
        "history": [ {created, status, run, model}, ... ]       earlier recommendations (the last 10)
    }
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

HISTORY = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def build(steps: list[dict[str, Any]], variables: Optional[dict[str, Any]], *, run: str, model: str, verdict: str, base_revision: Optional[int],
          notes: Optional[list[str]] = None, kind: str = "agent") -> dict[str, Any]:
    """kind "agent": the steps the agent used. kind "heal": steps of the script whose target no longer matched, each with `replaces_step` (the original step_no)."""
    rec: dict[str, Any] = {"status": "pending", "kind": kind, "created": _now(), "run": run, "model": model, "verdict": verdict, "steps": steps}
    if base_revision is not None:
        rec["base_revision"] = base_revision
    if variables:
        rec["variables"] = variables
    if notes:
        rec["notes"] = [str(n)[:300] for n in notes][:20]
    return rec


def _find(data: dict[str, Any], case_id: str) -> Optional[dict[str, Any]]:
    return next((c for c in data.get("cases", []) if isinstance(c, dict) and str(c.get("id")) == case_id), None)


def attach(path: Path, case_id: str, rec: dict[str, Any]) -> bool:
    """Put `rec` on the case in the suite file (replacing an older recommendation, which moves to `history`). False when it cannot be done."""
    path = Path(path)
    if path.suffix.lower() != ".json" or not path.is_file():
        return False
    data = _read(path)
    case = _find(data, case_id)
    if case is None:
        return False
    old = case.get("ai_recommendation")
    history = list((old or {}).get("history") or [])
    if old:
        history.append({k: old[k] for k in ("created", "status", "run", "model") if k in old})
    rec = {**rec, "history": history[-HISTORY:]}
    if not rec["history"]:
        rec.pop("history")
    case["ai_recommendation"] = rec
    _write(path, data)
    return True


def set_status(path: Path, case_id: str, action: str) -> dict[str, Any]:
    """action: applied | skipped | dismiss (remove the node). Returns the node (or {})."""
    path = Path(path)
    data = _read(path)
    case = _find(data, case_id)
    if case is None:
        raise ValueError(f"test case '{case_id}' is not in the file")
    rec = case.get("ai_recommendation")
    if not rec:
        raise ValueError("this test case has no recommendation")
    if action == "dismiss":
        case.pop("ai_recommendation")
        _write(path, data)
        return {}
    if action not in ("applied", "skipped"):
        raise ValueError("action must be applied, skipped or dismiss")
    rec["status"] = action
    rec["reviewed"] = _now()
    _write(path, data)
    return rec
