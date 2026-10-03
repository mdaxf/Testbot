"""Turning the agent's claim into a verdict testbot will stand behind.

The agent says pass / fail / inconclusive. testbot only accepts 'pass' when the evidence supports it:
  * no deterministic assertion is failing (latest result per distinct check),
  * every stated outcome (the case's `expect` list) is covered by a PASSING assertion,
  * at least one passing assertion exists at all (an agent that checked nothing cannot pass),
  * no server error happened during the run and no error message is left on screen (unless the test is about an error).
Otherwise the verdict is downgraded, and the reason says why."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from framework.agent.actions import Assertion

NEGATIVE_OBJECTIVE = re.compile(r"(?i)\b(error|invalid|duplicate|reject|refus\w*|fail\w*|not allowed|cannot|must not|should not|already exists|denied|unauthori[sz]ed)\b")


@dataclass
class Verdict:
    status: str                       # pass | fail | inconclusive | error
    reason: str
    ai_judgement: bool = False        # a 'fail' that no failed assertion backs up: the model's opinion only
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    downgraded_from: Optional[str] = None


def decide(requested: str, summary: str, outcomes: list[dict[str, Any]], assertions: list[Assertion], expect: list[str],
           objective: str, http_errors: list[str], screen_messages: list[str]) -> Verdict:
    latest: dict[str, Assertion] = {}
    for a in assertions:
        latest[a.key] = a                      # a later run of the same check replaces the earlier result
    failed = [a for a in latest.values() if not a.passed]
    passed = [a for a in latest.values() if a.passed]
    missing = [i for i in range(1, len(expect) + 1) if not any(i in a.covers for a in passed)]
    requested = requested if requested in ("pass", "fail", "inconclusive") else "inconclusive"

    if requested == "fail":
        detail = "; ".join(f"{a.id} {a.kind}: expected {a.expected!r}, got {a.actual!r}" for a in failed[:3])
        return Verdict("fail", (summary or "the agent reported a failure") + (f" [{detail}]" if detail else ""), ai_judgement=not failed, outcomes=outcomes)
    if requested == "inconclusive":
        return Verdict("inconclusive", summary or "the agent could not reach a verdict", outcomes=outcomes)

    # the agent claims a pass: check the evidence
    if failed:
        detail = "; ".join(f"{a.id} {a.kind}: expected {a.expected!r}, got {a.actual!r}" for a in failed[:3])
        return Verdict("fail", f"the agent claimed a pass but a check failed: {detail}", outcomes=outcomes, downgraded_from="pass")
    if expect and missing:
        names = "; ".join(f"#{i} '{expect[i - 1][:80]}'" for i in missing)
        return Verdict("inconclusive", f"expected outcome(s) not verified by any passing check: {names}", outcomes=outcomes, downgraded_from="pass")
    if not passed:
        return Verdict("inconclusive", "no check was executed, so a pass cannot be confirmed", outcomes=outcomes, downgraded_from="pass")
    if http_errors:
        return Verdict("fail", f"server error(s) during the run: {http_errors[0]}", outcomes=outcomes, downgraded_from="pass")
    if screen_messages and not NEGATIVE_OBJECTIVE.search(objective + " " + " ".join(expect)):
        return Verdict("fail", f"an error message is still shown on screen: {screen_messages[0][:150]}", outcomes=outcomes, downgraded_from="pass")
    return Verdict("pass", summary or "all checks passed", outcomes=outcomes)
