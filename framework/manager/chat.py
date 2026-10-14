"""Chat with the AI assistant to create or change test cases of one suite.

The conversation is kept on disk, per suite (<test_cases>/.revisions/<file>/chat.json), and can be cleared. Each assistant message may carry a PROPOSAL: complete
test cases to add or replace. A proposal is only ever a proposal: it is tidied (framework/suitefix.py), validated with the editor's rules, shown as a diff, and
applied by the user -- into the editor (a new revision when saved), or as a draft revision that leaves the default untouched.

What is sent to the AI service: the suite's settings, the case being discussed in full, a one-line outline of the other cases (and in full any case the
message names by id), and the last messages of the conversation. Nothing is sent until the user presses Send.
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from framework import revisions, suitefix
from framework.manager import ai, testcases
from framework.manager.workspace import Workspace, WorkspaceError, resolve_within

HISTORY_LIMIT = 200          # messages kept on disk
CONTEXT_MESSAGES = 10        # messages sent along with a new one

CHAT_SYSTEM = """You are the assistant inside the test editor of 'testbot', a tool that runs automated web UI tests. The user talks to you to create or change the test cases of ONE test suite.
Reply with ONE JSON object and nothing else:
{"reply": "what you did or your answer, in a few plain sentences",
 "changes": [ {"op": "replace", "case": {<complete testbot test case>}}, {"op": "add", "case": {<complete testbot test case>}} ],
 "settings": {optional: only the suite settings that change, e.g. "variables": {...}, "base_url": "..."}}

RULES
- "replace" must keep the id of an existing case and contains the WHOLE new version of it (all steps, not just the changed ones). "add" is a new case with a new id.
- If the user only asks a question or you need more information, use "changes": [] and ask or answer in "reply".
- Change only what the user asked for. Keep the other steps, titles, variables and checks as they are.
- Use ONLY the actions, target strategies and check types listed below. Number steps 1, 2, 3...  Never invent selectors that the information given does not support:
  prefer role / label / text / placeholder / testid targets; if unsure, say so in "reply".
- Reference variables as {name}. Put real passwords and keys in variables, never in steps.
- A natural-language case (agentic) has `objective`, optional `data_hints`, `expect` and no steps; keep that shape if the case already has it.

"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file(ws: Workspace, rel: str) -> Path:
    """<test_cases>/.revisions/<rel>/chat.json -- refused if `rel` escapes the suite's own folder."""
    ws.safe_path("test_cases", rel)                                   # the suite path itself must be inside the test-cases folder
    folder = resolve_within(ws.dir("test_cases") / revisions.DIR_NAME, rel)
    return folder / "chat.json"


def load(ws: Workspace, rel: str) -> dict[str, Any]:
    p = _file(ws, rel)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"messages": []}


def _save(ws: Workspace, rel: str, data: dict[str, Any]) -> None:
    p = _file(ws, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    data["messages"] = data["messages"][-HISTORY_LIMIT:]
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def clear(ws: Workspace, rel: str) -> None:
    _file(ws, rel).unlink(missing_ok=True)


def mark(ws: Workspace, rel: str, message_id: str, status: str) -> None:
    data = load(ws, rel)
    for m in data["messages"]:
        if m.get("id") == message_id and m.get("proposal"):
            m["proposal"]["status"] = status
            m["proposal"]["resolved"] = _now()
    _save(ws, rel, data)


def _outline(case: dict[str, Any]) -> str:
    n = len(case.get("steps") or [])
    return f"- {case.get('id')}: {case.get('title', '')} ({n} step{'s' if n != 1 else ''}{', natural language' if case.get('objective') else ''})"


def _context(suite: dict[str, Any], focus: Optional[str], message: str) -> str:
    settings = {k: v for k, v in suite.items() if k not in ("cases", "revision", "status")}
    named = {c.get("id") for c in suite.get("cases", []) if c.get("id") and re.search(r"(?<![\w-])" + re.escape(str(c["id"])) + r"(?![\w-])", message)}
    if focus:
        named.add(focus)
    full = [c for c in suite.get("cases", []) if c.get("id") in named]
    rest = [c for c in suite.get("cases", []) if c.get("id") not in named]
    strip = lambda c: {k: v for k, v in c.items() if k not in ("revision", "status", "ai_recommendation")}
    parts = ["SUITE SETTINGS:\n" + json.dumps(settings, indent=1, ensure_ascii=False)]
    if full:
        parts.append("CASE(S) UNDER DISCUSSION (complete):\n" + json.dumps([strip(c) for c in full], indent=1, ensure_ascii=False))
    if rest:
        parts.append("OTHER CASES IN THE SUITE (outline only; ask the user or mention the id if you need one in full):\n" + "\n".join(_outline(c) for c in rest))
    return "\n\n".join(parts)


def _transcript(messages: list[dict[str, Any]]) -> str:
    lines = []
    for m in messages[-CONTEXT_MESSAGES:]:
        who = "USER" if m["role"] == "user" else "ASSISTANT"
        extra = ""
        if m.get("proposal"):
            extra = f" [proposed {len(m['proposal'].get('changes', []))} change(s): {m['proposal'].get('status', 'pending')}]"
        lines.append(f"{who}: {m['content']}{extra}")
    return "\n".join(lines)


def _proposal(ws: Workspace, rel: str, suite: dict[str, Any], data: dict[str, Any]) -> tuple[Optional[dict[str, Any]], list[str]]:
    """Validate and describe what the model proposed. Returns (proposal or None, repair problems)."""
    changes = data.get("changes") or []
    if not isinstance(changes, list):
        raise ai.AIError("the reply's `changes` is not a list")
    existing = {c.get("id"): c for c in suite.get("cases", [])}
    out, notes = [], []
    mini = {k: copy.deepcopy(v) for k, v in suite.items() if k not in ("cases", "revision", "status")}
    mini["cases"] = []
    for ch in changes:
        case = ch.get("case") if isinstance(ch, dict) else None
        if not isinstance(case, dict) or not case.get("id"):
            raise ai.AIError("a change has no test case with an id")
        op = ch.get("op") if ch.get("op") in ("replace", "add") else ("replace" if case["id"] in existing else "add")
        if op == "replace" and case["id"] not in existing:
            op = "add"
        if op == "add" and case["id"] in existing:
            raise ai.AIError(f"'add' used for the existing case id '{case['id']}': use 'replace', or a new id")
        mini["cases"].append(case)
        out.append({"op": op, "case": case})
    if data.get("settings"):
        mini.update(copy.deepcopy(data["settings"]))
    fixed = suitefix.normalize_suite(mini, guess_check_targets=True)
    problems = [p for p in testcases.validate(testcases.renumber(mini)) if p["level"] == "error"]
    if problems:
        raise ai.AIError("validation errors: " + "; ".join(f"{(mini['cases'][p['case']]['id'] if isinstance(p.get('case'), int) and p['case'] < len(mini['cases']) else 'suite')} step {p.get('step')}: {p['message']}" for p in problems[:10]))
    for ch, case in zip(out, mini["cases"]):
        ch["case"] = case
        old = existing.get(case["id"])
        ch["diff"] = revisions.diff_cases({k: v for k, v in old.items() if k not in revisions.META_KEYS}, case) if (old and ch["op"] == "replace") else None
    notes += [f"Fixed automatically: {n}" for n in fixed]
    if not out and not data.get("settings"):
        return None, notes
    return {"changes": out, "settings": data.get("settings") or None, "notes": notes}, notes


def send(ws: Workspace, rel: str, message: str, focus: Optional[str] = None, suite: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """One turn: store the user's message, ask the model, store and return its answer (with a validated proposal, if any)."""
    message = (message or "").strip()
    if not message:
        raise ai.AIError("type a message")
    if suite is None:
        suite = testcases.read_test(ws, rel)["suite"]
    history = load(ws, rel)
    system = CHAT_SYSTEM + "REFERENCE:\n" + ai._reference()
    user = (_context(suite, focus, message) + ("\n\nCONVERSATION SO FAR:\n" + _transcript(history["messages"]) if history["messages"] else "")
            + f"\n\nUSER'S NEW MESSAGE:\n{message}")
    reply, model = ai._complete(ws, system, ai._clip(user))
    proposal, problem = None, None
    for attempt in range(2):
        try:
            data = ai._extract_json(reply)
            if not isinstance(data, dict) or "reply" not in data:
                raise ai.AIError("the reply had no `reply` text")
            proposal, _ = _proposal(ws, rel, suite, data)
            break
        except ai.AIError as exc:
            if attempt == 1:
                problem = str(exc)
                data = {"reply": "I could not produce a valid change for that: " + problem + ". Please rephrase or give me more detail.", "changes": []}
                proposal = None
                break
            reply, _ = ai._complete(ws, system, ai._clip(user + f"\n\nYour previous reply had a problem: {exc}\nReply again with the corrected JSON object only."))
    now = _now()
    um = {"id": uuid.uuid4().hex[:10], "role": "user", "content": message, "at": now, "case": focus}
    am = {"id": uuid.uuid4().hex[:10], "role": "assistant", "content": str(data.get("reply", "")).strip(), "at": _now(), "model": model}
    if proposal:
        am["proposal"] = {**proposal, "status": "pending"}
    history["messages"] += [um, am]
    _save(ws, rel, history)
    return {"user": um, "assistant": am, "error": problem}


def apply_as_drafts(ws: Workspace, rel: str, message_id: str, note: str = "") -> list[dict[str, Any]]:
    """Store the proposal of a message as DRAFT revisions (the default stays untouched). Returns [{case, rev}]."""
    data = load(ws, rel)
    msg = next((m for m in data["messages"] if m.get("id") == message_id and m.get("proposal")), None)
    if msg is None:
        raise WorkspaceError("that proposal does not exist (any more)")
    out = []
    for ch in msg["proposal"]["changes"]:
        rev = revisions.add_draft(ws.dir("test_cases"), rel, ch["case"]["id"], ch["case"], source="chat", note=note or msg["content"][:120])
        out.append({"case": ch["case"]["id"], "rev": rev})
    msg["proposal"]["status"] = "draft"
    msg["proposal"]["resolved"] = _now()
    _save(ws, rel, data)
    return out
