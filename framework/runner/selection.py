"""Choosing which test cases of a suite run, and in which order.

    testbot suite --suite orders.json --case TC-5 --case TC-2       (or --case TC-5,TC-2)
    testbot suite --suite orders.json --tag smoke                   (every case tagged "smoke", in file order)
    files:
      - path: orders.json
        cases: [TC-5, TC-2]                                         (in a session plan; `tags: [smoke]` works too)

Rules
  * no selection (no ids, no tags)  -> every case of the file, in file order, exactly as before (no prerequisites are added)
  * ids                             -> only those cases, **in the order they are listed**
  * tags                            -> every case that has any of the tags, in file order; with ids as well: those ids that have a tag
  * the same id listed twice        -> it runs twice; the repeat is named TC-5~2 (then ~3 ...) so results and screenshots never collide
  * an id (or tag) that is not in the file -> SelectionError (checked before any browser starts), naming what does exist
  * a case with `depends_on: [TC-LOGIN]` -> its prerequisites (and theirs) are put right before it and run in the SAME browser, so the
    login state is there; if the case just before it already is that prerequisite, nothing is added. Only when a selection is active.
"""
from __future__ import annotations

from typing import Iterable, Optional

from framework.models import TestCase, TestSuite

REPEAT_SEPARATOR = "~"


class SelectionError(ValueError):
    pass


class Selection(list):
    """The cases to run, in run order (a list of TestCase), plus:
       .chain[i]  True = case i continues the browser and variables of case i-1 (a prerequisite that ran just before it)
       .added     ids of prerequisites that were added automatically"""

    def __init__(self, cases: Iterable[TestCase] = (), chain: Optional[list[bool]] = None, added: Optional[list[str]] = None):
        super().__init__(cases)
        self.chain = chain if chain is not None else [False] * len(self)
        self.added = added or []


def parse_case_args(values: Optional[Iterable[str]]) -> list[str]:
    """The raw --case / --tag values (repeatable). A value may be a comma list ('TC-1,TC-3'); it is split in select_cases, and only when the
    whole value is not itself a test case id -- so an id that contains a comma still works."""
    return [str(v).strip() for v in values or [] if str(v).strip()]


def _expand(wanted: list[str], known: set[str]) -> list[str]:
    out: list[str] = []
    lower = {k.lower() for k in known}
    for w in wanted:
        if w in known or w.lower() in lower or "," not in w:
            out.append(w)
        else:
            out += [p.strip() for p in w.split(",") if p.strip()]
    return out


def _is_default(case: TestCase) -> bool:
    """Only the default revision of a case is run (a missing status counts as default)."""
    return (getattr(case, "status", None) or "default") == "default"


def _tags_of(case: TestCase) -> set[str]:
    return {str(t).strip().lower() for t in (getattr(case, "tags", None) or []) if str(t).strip()}


def select_cases(suite: TestSuite, wanted: Optional[Iterable[str]] = None, tags: Optional[Iterable[str]] = None, *, label: Optional[str] = None) -> Selection:
    label = label or suite.suite_id
    wanted = [str(w).strip() for w in (wanted or []) if str(w).strip()]
    want_tags = {t.lower() for t in _expand([str(t).strip() for t in (tags or []) if str(t).strip()], set())}
    if not wanted and not want_tags:
        return Selection([c for c in suite.cases if _is_default(c)])
    by_id = {c.id: c for c in suite.cases}
    lower: dict[str, list[TestCase]] = {}
    for c in suite.cases:
        lower.setdefault(c.id.lower(), []).append(c)

    def find(cid: str) -> Optional[TestCase]:
        return by_id.get(cid) or (lower[cid.lower()][0] if len(lower.get(cid.lower(), [])) == 1 else None)

    # 1. the ids, in order
    if wanted:
        ids, missing = [], []
        for w in _expand(wanted, set(by_id)):
            case = find(w)
            (ids.append(case.id) if case else missing.append(w))
        if missing:
            raise SelectionError(f"{label}: test case(s) not found: {', '.join(missing)}. "
                                 f"The file has: {', '.join(c.id for c in suite.cases) or '(no test cases)'}")
        if want_tags:
            ids = [i for i in ids if _tags_of(by_id[i]) & want_tags]
    else:
        ids = [c.id for c in suite.cases if _tags_of(c) & want_tags]
    if want_tags and not ids:
        known = sorted({t for c in suite.cases for t in (getattr(c, "tags", None) or [])})
        raise SelectionError(f"{label}: no test case matches the tag(s) {', '.join(sorted(want_tags))}"
                             + (f" (tags in the file: {', '.join(known)})" if known else " (no test case in the file has tags)"))

    # 2. prerequisites, right before the case that needs them, in the same browser
    def prerequisites(cid: str, trail: tuple[str, ...] = ()) -> list[str]:
        out: list[str] = []
        for dep in getattr(by_id[cid], "depends_on", None) or []:
            target = find(dep)
            if target is None:
                raise SelectionError(f"{label}: {cid} depends on '{dep}', which is not in the file")
            if target.id in trail or target.id == cid:
                raise SelectionError(f"{label}: circular depends_on: {' -> '.join((*trail, cid, target.id))}")
            for p in prerequisites(target.id, (*trail, cid)) + [target.id]:
                if p not in out:
                    out.append(p)
        return out

    order: list[str] = []
    chain: list[bool] = []
    added: list[str] = []
    for cid in ids:
        pre = prerequisites(cid)
        if pre and order[len(order) - len(pre):] == pre:       # the prerequisites just ran: carry on from them
            new, flags = [cid], [True]
        elif pre:
            new, flags = pre + [cid], [False] + [True] * len(pre)
            added += [p for p in pre if p not in ids and p not in added]
        else:
            new, flags = [cid], [False]
        order += new
        chain += flags

    # 3. name the repeats
    runs: dict[str, int] = {}
    picked: list[TestCase] = []
    for cid in order:
        runs[cid] = runs.get(cid, 0) + 1
        case = by_id[cid]
        picked.append(case if runs[cid] == 1 else case.model_copy(deep=True, update={"id": f"{cid}{REPEAT_SEPARATOR}{runs[cid]}"}))
    return Selection(picked, chain, added)


def describe(suite: TestSuite, selected: list[TestCase], wanted: Optional[Iterable[str]], tags: Optional[Iterable[str]] = None) -> str:
    if not list(wanted or []) and not list(tags or []):
        return f"all {len(suite.cases)} case(s)"
    extra = getattr(selected, "added", None)
    return (f"{len(selected)} of {len(suite.cases)} case(s): {', '.join(c.id for c in selected)}"
            + (f" (prerequisites added: {', '.join(extra)})" if extra else ""))
