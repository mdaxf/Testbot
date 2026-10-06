"""Everything waiting for a person to look at it, across all suites: the agent's / self-healing recommendations (pending) and draft revisions."""
from __future__ import annotations

import json
from typing import Any

from framework import revisions
from framework.manager import testcases
from framework.manager.workspace import Workspace


def pending(ws: Workspace) -> dict[str, Any]:
    root = ws.dir("test_cases")
    recs: list[dict[str, Any]] = []
    drafts: list[dict[str, Any]] = []
    for t in testcases.list_tests(ws):
        if t.get("format") != "testbot" or not t["path"].lower().endswith(".json"):
            continue
        try:
            data = json.loads((root / t["path"]).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for c in data.get("cases", []):
            r = c.get("ai_recommendation") if isinstance(c, dict) else None
            if isinstance(r, dict) and r.get("status") == "pending":
                recs.append({"path": t["path"], "case": c.get("id"), "title": c.get("title", ""), "kind": r.get("kind", "agent"), "created": r.get("created"),
                             "model": r.get("model"), "run": r.get("run"), "steps": len(r.get("steps") or [])})
        try:
            idx = revisions._load(root, t["path"])
        except (OSError, ValueError):
            idx = None
        for cid, lin in ((idx or {}).get("cases") or {}).items():
            for rev, rec in lin["revisions"].items():
                if rec.get("status") == "draft":
                    drafts.append({"path": t["path"], "case": cid, "rev": int(rev), "created": rec.get("created"), "source": rec.get("source"), "note": rec.get("note", "")})
    recs.sort(key=lambda r: r.get("created") or "", reverse=True)
    drafts.sort(key=lambda r: r.get("created") or "", reverse=True)
    return {"recommendations": recs, "drafts": drafts}
