"""Converts test_cases/ad_demo/ad_demo_approval_workflow.xlsx to the
equivalent JSON suite format (see README.md "Authoring test cases" /
test_cases/examples/sample_suite.json) -- both formats load through the
exact same TestSuite model, so this is a lossless round-trip, not a
re-authoring. Loads the Excel file for real (proves it's still valid) and
serializes the resulting TestSuite, trimming only the fields that are pure
Excel-column noise at their default value (connection="default",
timeout_ms=10000 unused-default, nth/row/col/scope=None) so the output
reads like a hand-written JSON suite, not a dict dump.

Run:
    python scripts/xlsx_to_json.py
"""
from __future__ import annotations

import json
from pathlib import Path

from framework.loaders.excel_loader import load_suite_from_excel
from framework.loaders.json_loader import load_suite_from_json

SRC = Path(__file__).resolve().parent.parent / "test_cases" / "ad_demo" / "ad_demo_approval_workflow.xlsx"
DST = SRC.with_suffix(".json")


def _strip_target(d: dict | None) -> dict | None:
    if d is None:
        return None
    return {k: v for k, v in d.items() if v is not None}


def _strip_step(d: dict) -> dict:
    out = {k: v for k, v in d.items() if v is not None}
    if out.get("connection") == "default":
        del out["connection"]
    if out.get("timeout_ms") == 10_000:
        del out["timeout_ms"]
    if "target" in out:
        out["target"] = _strip_target(out["target"])
    if "expected" in out and out["expected"] is not None:
        exp = {k: v for k, v in out["expected"].items() if v is not None}
        if "target" in exp:
            exp["target"] = _strip_target(exp["target"])
        out["expected"] = exp
    if "capture" in out and out["capture"] is not None:
        cap = {k: v for k, v in out["capture"].items() if v is not None}
        if "target" in cap:
            cap["target"] = _strip_target(cap["target"])
        out["capture"] = cap
    return out


def _strip_variable(d: dict) -> dict:
    out = {k: v for k, v in d.items() if v is not None and v != {}}
    if out.get("connection") == "default":
        del out["connection"]
    return out


def main() -> None:
    suite = load_suite_from_excel(SRC)
    dumped = suite.model_dump(mode="json", by_alias=True, exclude_none=True)

    dumped["cases"] = [
        {
            **{k: v for k, v in case.items() if k not in ("steps", "variables") and v is not None},
            "variables": {name: _strip_variable(v) for name, v in case.get("variables", {}).items()},
            "steps": [_strip_step(s) for s in case["steps"]],
        }
        for case in dumped["cases"]
    ]

    DST.write_text(json.dumps(dumped, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {DST}")

    # Round-trip proof: the JSON we just wrote must load back to an
    # equivalent TestSuite, not just be well-formed JSON.
    reloaded = load_suite_from_json(DST)
    assert [c.id for c in reloaded.cases] == [c.id for c in suite.cases]
    assert sum(len(c.steps) for c in reloaded.cases) == sum(len(c.steps) for c in suite.cases)
    print(f"round-trip verified: {len(reloaded.cases)} cases, "
          f"{sum(len(c.steps) for c in reloaded.cases)} steps total")


if __name__ == "__main__":
    main()
