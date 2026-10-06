"""Tidy up things that AI tools (and hand edits) get wrong in a suite, so that it validates and runs.

  * a target with a strategy testbot does not know ('name', 'id', 'class', 'link_text' ... as Selenium / Playwright users know them)
    is rewritten with one it does:            name=username  ->  css  [name="username"]
  * an empty target  `{}`  (an `agent` step needs none)  is removed
  * (only with guess_check_targets=True, used for AI answers) an element check with no element of its own gets one:
        text checks (text_equals, text_contains)  -> the whole page  (css: body)
        visible / hidden / value_equals / ...      -> the step's own target, when the step has one
    Every guess is reported in the notes so the person reviewing the proposal sees it.

Used on the answer of the AI assistant (Generate / Optimize / Convert) before it is checked, and when the manager saves a suite.
`normalize_suite` changes the dict in place and returns one line per change, for the notes shown to the user."""
from __future__ import annotations

import re
from typing import Any

_PLAIN = re.compile(r"^[A-Za-z_][\w-]*$")


def _css_attr(attr: str, value: str) -> str:
    return f'[{attr}="{value}"]'


def _rewrite(strategy: str, value: str) -> tuple[str, str] | None:
    key = re.sub(r"[^a-z]", "", strategy.lower())
    if key == "name":
        return "css", _css_attr("name", value)
    if key == "id":
        return "css", ("#" + value) if _PLAIN.match(value) else _css_attr("id", value)
    if key in ("class", "classname"):
        return "css", ("." + value) if _PLAIN.match(value) else _css_attr("class", value)
    if key in ("tag", "tagname", "selector", "cssselector", "css"):
        return "css", value
    if key in ("linktext", "partiallinktext", "link"):
        return "text", value
    return None


def _fix_target(target: Any, where: str, notes: list[str]) -> Any:
    """Returns the (possibly rewritten) target; None = remove it."""
    if not isinstance(target, dict):
        return target
    if not target.get("strategy") and not target.get("value"):
        notes.append(f"{where}: removed an empty target")
        return None
    strategy = str(target.get("strategy") or "")
    if strategy and "value" in target:
        known = {"role", "label", "text", "placeholder", "testid", "css", "xpath", "url", "ai", "row_containing", "table_cell"}
        if strategy not in known:
            fixed = _rewrite(strategy, str(target["value"]))
            if fixed:
                notes.append(f"{where}: target '{strategy}={target['value']}' is not a testbot strategy; now {fixed[0]}: {fixed[1]}")
                target = {**target, "strategy": fixed[0], "value": fixed[1]}
    return target


_NEEDS_TARGET = {"text_equals", "text_contains", "value_equals", "visible", "hidden", "count_equals", "css_equals", "has_class", "list_matches"}
_TEXT_CHECKS = {"text_equals", "text_contains"}


def _guess_check_target(st: dict[str, Any], where: str, notes: list[str]) -> None:
    exp = st.get("expected")
    if not isinstance(exp, dict) or exp.get("type") not in _NEEDS_TARGET or exp.get("target"):
        return
    own = st.get("target")
    if exp["type"] in _TEXT_CHECKS:
        exp["target"] = {"strategy": "css", "value": "body"}
        notes.append(f"{where}: check '{exp['type']}' had no element; it now looks at the whole page (body) -- narrow it if you can")
    elif isinstance(own, dict) and own.get("strategy") and own.get("value"):
        exp["target"] = dict(own)
        notes.append(f"{where}: check '{exp['type']}' had no element; it now uses the step's own element")


def normalize_suite(suite: dict[str, Any], guess_check_targets: bool = False) -> list[str]:
    notes: list[str] = []
    for case in suite.get("cases", []) if isinstance(suite, dict) else []:
        if not isinstance(case, dict):
            continue
        for st in case.get("steps", []) or []:
            if not isinstance(st, dict):
                continue
            where = f"{case.get('id')} step {st.get('step_no')}"
            if "target" in st:
                new = _fix_target(st["target"], where, notes)
                if new is None:
                    del st["target"]
                else:
                    st["target"] = new
            for holder in ("expected", "capture"):
                block = st.get(holder)
                if isinstance(block, dict) and "target" in block:
                    new = _fix_target(block["target"], f"{where} ({holder})", notes)
                    if new is None:
                        del block["target"]
                    else:
                        block["target"] = new
            if guess_check_targets:
                _guess_check_target(st, where, notes)
    return notes
