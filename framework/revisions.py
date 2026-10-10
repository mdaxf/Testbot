"""Revisions of test suites and test cases, with history.

A suite file (orders.json) always holds the DEFAULT version -- the one the runner executes -- so the runner, sessions, groups, schedules and git work
unchanged. Everything else lives beside it, in  <test_cases>/.revisions/<file>/ :

    index.json                 the history (below)
    settings/<sN>.json         the suite settings (base_url, variables, connections ... everything except the cases), one file per revision
    cases/<case id>/<n>.json   one test case, one file per revision

  * a CASE has its own lineage of revisions 1, 2, 3 ...; exactly one is the default (status "default"), older ones are "superseded", proposals are "draft"
  * the SETTINGS have a lineage s1, s2 ...
  * a SUITE revision (r1, r2 ...) is only a list: {settings: s2, cases: {TC-1: 3, TC-2: 1}, order: [...]}. Changing one case adds a revision of that
    case and a new suite revision pointing to it; the other cases are untouched. You can make an older revision of one case the default while the
    others stay on their latest.
  * saving the DEFAULT creates a new revision (it becomes the default, the old one "superseded"); a revision that is NOT the default is edited in place
  * the file carries `revision` and `status` on the suite and on each case. A file without them counts as the default; the next save adds them.
  * a change made outside testbot (an editor, a git pull) is noticed when the suite is opened and recorded as a new revision.
`ai_recommendation` (the agent's suggested steps) is a note on the case in the file; it is not part of a revision and never changes it.
"""
from __future__ import annotations

import copy
import difflib
import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

META_KEYS = ("revision", "status", "ai_recommendation")
KEEP = 50
DIR_NAME = ".revisions"
_FIELDS = ("action", "description", "target", "input", "expected", "capture", "query", "connection", "timeout_ms", "delay_after_ms", "config", "mode", "agent", "variables")


class RevisionError(Exception):
    pass


# ------------------------------------------------------------------ files

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_rel(root: Path, rel: str) -> Path:
    """The suite file `rel` inside `root` (the test-cases folder); a path that escapes it is refused."""
    base = Path(root).resolve()
    target = (base / str(rel)).resolve()
    if base not in target.parents or DIR_NAME in target.relative_to(base).parts:
        raise RevisionError("the suite path is outside the test-cases folder")
    return target


def _dir(root: Path, rel: str) -> Path:
    _check_rel(root, rel)
    return Path(root) / DIR_NAME / rel


def _safe(name: Any) -> str:
    q = urllib.parse.quote(str(name), safe="")
    return q.replace(".", "%2E") if q in (".", "..") else q          # never a '.' / '..' folder


def _rev_no(rev: Any) -> int:
    """A case revision number (1, 2, 3 ...); anything else is refused, so it can never become a path."""
    try:
        n = int(str(rev).strip())
    except (TypeError, ValueError):
        raise RevisionError(f"'{str(rev)[:40]}' is not a revision number") from None
    if n < 1:
        raise RevisionError(f"'{n}' is not a revision number")
    return n


def _settings_id(sid: Any) -> str:
    if not re.match(r"^s[0-9]{1,9}$", str(sid)):
        raise RevisionError(f"'{str(sid)[:40]}' is not a settings revision (s1, s2 ...)")
    return str(sid)


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _case_file(root: Path, rel: str, cid: str, rev: Any) -> Path:
    return _dir(root, rel) / "cases" / _safe(cid) / f"{_rev_no(rev)}.json"


def _settings_file(root: Path, rel: str, sid: str) -> Path:
    return _dir(root, rel) / "settings" / f"{_settings_id(sid)}.json"


def _core_case(case: dict[str, Any]) -> dict[str, Any]:
    c = {k: copy.deepcopy(v) for k, v in case.items() if k not in META_KEYS}
    for i, st in enumerate(c.get("steps") or [], start=1):
        if isinstance(st, dict):
            st["step_no"] = i
    return c


def _core_settings(suite: dict[str, Any]) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in suite.items() if k not in META_KEYS and k != "cases"}


def _norm(x: Any) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


# ------------------------------------------------------------------ the index

def _load(root: Path, rel: str) -> Optional[dict[str, Any]]:
    p = _dir(root, rel) / "index.json"
    return _read(p) if p.is_file() else None


def _save(root: Path, rel: str, idx: dict[str, Any]) -> None:
    _write(_dir(root, rel) / "index.json", idx)


def _new_case_rev(root: Path, rel: str, idx: dict[str, Any], cid: str, core: dict[str, Any], *, status: str, source: str, note: str = "") -> int:
    lin = idx["cases"].setdefault(cid, {"default": None, "removed": False, "next": 1, "revisions": {}})
    rev = lin["next"]
    lin["next"] = rev + 1
    lin["revisions"][str(rev)] = {"created": _now(), "source": source, "note": note, "status": status}
    _write(_case_file(root, rel, cid, rev), core)
    if status == "default":
        old = lin.get("default")
        if old is not None and str(old) in lin["revisions"] and str(old) != str(rev):
            lin["revisions"][str(old)]["status"] = "superseded"
        lin["default"] = rev
    return rev


def _new_settings_rev(root: Path, rel: str, idx: dict[str, Any], core: dict[str, Any], *, status: str, source: str, note: str = "") -> str:
    st = idx["settings"]
    n = st["next"]
    st["next"] = n + 1
    sid = f"s{n}"
    st["revisions"][sid] = {"created": _now(), "source": source, "note": note, "status": status}
    _write(_settings_file(root, rel, sid), core)
    if status == "default":
        old = st.get("default")
        if old and old in st["revisions"] and old != sid:
            st["revisions"][old]["status"] = "superseded"
        st["default"] = sid
    return sid


def _new_manifest(idx: dict[str, Any], mapping: dict[str, int], order: list[str], source: str, note: str = "") -> str:
    n = idx["next_manifest"]
    idx["next_manifest"] = n + 1
    rid = f"r{n}"
    prev = idx.get("default_manifest")
    if prev and prev in idx["manifests"]:
        idx["manifests"][prev]["status"] = "superseded"
    idx["manifests"][rid] = {"created": _now(), "source": source, "note": note, "status": "default", "settings": idx["settings"]["default"],
                             "cases": dict(mapping), "order": list(order)}
    idx["default_manifest"] = rid
    return rid


def _read_suite_file(root: Path, rel: str) -> dict[str, Any]:
    p = _check_rel(root, rel)
    data = _read(p)
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise RevisionError(f"'{rel}' is not a testbot suite")
    return data


def _init(root: Path, rel: str, suite: dict[str, Any], source: str = "initial") -> dict[str, Any]:
    idx: dict[str, Any] = {"version": 1, "file": rel, "settings": {"default": None, "next": 1, "revisions": {}}, "cases": {}, "manifests": {},
                           "next_manifest": 1, "default_manifest": None}
    _new_settings_rev(root, rel, idx, _core_settings(suite), status="default", source=source)
    mapping, order = {}, []
    for case in suite.get("cases", []):
        cid = str(case.get("id"))
        mapping[cid] = _new_case_rev(root, rel, idx, cid, _core_case(case), status="default", source=source)
        order.append(cid)
    _new_manifest(idx, mapping, order, source)
    _save(root, rel, idx)
    return idx


def ensure(root: Path, rel: str) -> dict[str, Any]:
    idx = _load(root, rel)
    if idx is None:
        idx = _init(root, rel, _read_suite_file(root, rel))
    return idx


# ------------------------------------------------------------------ recording changes

def _apply(root: Path, rel: str, idx: dict[str, Any], suite: dict[str, Any], source: str, note: str) -> list[str]:
    """Record the content of `suite` as the new DEFAULT. Returns what changed (empty = nothing)."""
    changed: list[str] = []
    sc = _core_settings(suite)
    cur = _read(_settings_file(root, rel, idx["settings"]["default"]))
    if _norm(sc) != _norm(cur):
        _new_settings_rev(root, rel, idx, sc, status="default", source=source, note=note)
        changed.append("settings")
    order, mapping = [], {}
    for case in suite.get("cases", []):
        cid = str(case.get("id"))
        order.append(cid)
        core = _core_case(case)
        lin = idx["cases"].get(cid)
        if lin is None or lin.get("removed") or lin.get("default") is None:
            if lin is not None:
                lin["removed"] = False
            _new_case_rev(root, rel, idx, cid, core, status="default", source=source, note=note)
            changed.append(f"{cid} (added)")
        else:
            if _norm(core) != _norm(_read(_case_file(root, rel, cid, lin["default"]))):
                _new_case_rev(root, rel, idx, cid, core, status="default", source=source, note=note)
                changed.append(cid)
        mapping[cid] = idx["cases"][cid]["default"]
    for cid, lin in idx["cases"].items():
        if cid not in order and not lin.get("removed") and lin.get("default") is not None:
            lin["removed"] = True
            changed.append(f"{cid} (removed)")
    dm = idx["manifests"].get(idx["default_manifest"]) or {}
    if changed or dm.get("order") != order or dm.get("cases") != mapping or dm.get("settings") != idx["settings"]["default"]:
        _new_manifest(idx, mapping, order, source, note)
        if not changed:
            changed.append("order")
    return changed


def _materialize(root: Path, rel: str, idx: dict[str, Any], incoming: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The suite as it should be on disk: the default manifest's content plus the revision / status fields. Notes on a case that are not part of
    its revision (ai_recommendation) are carried over from the file as it is now."""
    m = idx["manifests"][idx["default_manifest"]]
    existing: dict[str, Any] = {}
    p = Path(root) / rel
    if p.is_file():
        try:
            existing = {str(c.get("id")): c for c in _read(p).get("cases", []) if isinstance(c, dict)}
        except (ValueError, OSError):
            existing = {}
    if incoming is not None:      # a save: the notes (ai_recommendation) are those of the content being saved, including a changed status or a removal
        saved = {str(c.get("id")): c for c in incoming.get("cases", []) if isinstance(c, dict)}
        for cid, c in saved.items():
            existing[cid] = {**existing.get(cid, {}), "ai_recommendation": c.get("ai_recommendation")}
    suite = _read(_settings_file(root, rel, m["settings"]))
    suite["revision"], suite["status"] = idx["default_manifest"], "default"
    suite["cases"] = []
    for cid in m["order"]:
        rev = m["cases"][cid]
        c = _read(_case_file(root, rel, cid, rev))
        c["revision"], c["status"] = rev, "default"
        if existing.get(cid, {}).get("ai_recommendation"):
            c["ai_recommendation"] = existing[cid]["ai_recommendation"]
        suite["cases"].append(c)
    return suite


def _write_suite(root: Path, rel: str, idx: dict[str, Any], incoming: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    suite = _materialize(root, rel, idx, incoming)
    _write(Path(root) / rel, suite)
    return suite


def _prune(root: Path, rel: str, idx: dict[str, Any], keep: int = KEEP) -> None:
    ids = sorted(idx["manifests"], key=lambda r: int(r[1:]))
    drop = [r for r in ids if r != idx["default_manifest"]][: max(0, len(ids) - keep)]
    for r in drop:
        del idx["manifests"][r]
    used_cases = {(cid, rev) for m in idx["manifests"].values() for cid, rev in m["cases"].items()}
    used_settings = {m["settings"] for m in idx["manifests"].values()}
    for cid, lin in idx["cases"].items():
        for rev in list(lin["revisions"]):
            rec = lin["revisions"][rev]
            if rec["status"] == "superseded" and (cid, int(rev)) not in used_cases:
                del lin["revisions"][rev]
                _case_file(root, rel, cid, rev).unlink(missing_ok=True)
    for sid in list(idx["settings"]["revisions"]):
        if idx["settings"]["revisions"][sid]["status"] == "superseded" and sid not in used_settings:
            del idx["settings"]["revisions"][sid]
            _settings_file(root, rel, sid).unlink(missing_ok=True)


# ------------------------------------------------------------------ public operations

def sync(root: Path, rel: str) -> list[str]:
    """Called when a suite is opened: a change made outside testbot is recorded as a new default revision (the file is not rewritten)."""
    idx = ensure(root, rel)
    if idx["default_manifest"] and (Path(root) / rel).is_file():
        changed = _apply(root, rel, idx, _read_suite_file(root, rel), "changed outside testbot", "")
        if changed:
            _prune(root, rel, idx)
            _save(root, rel, idx)
        return changed
    return []


def save_default(root: Path, rel: str, suite: dict[str, Any], source: str = "manual", note: str = "") -> dict[str, Any]:
    """Save `suite` as the default version (a new revision for everything that changed) and write it to the file."""
    idx = _load(root, rel)
    created = False
    if idx is None:
        if (Path(root) / rel).is_file():
            idx = _init(root, rel, _read_suite_file(root, rel))          # the file as it is now becomes revision 1; the save below is revision 2
        else:
            idx, created = _init(root, rel, suite, source), True         # a new file: what is saved now is revision 1
    # a change made outside testbot since the last save is kept as its own revision first, so it is not silently overwritten
    if (Path(root) / rel).is_file() and not created:
        try:
            outside = _apply(root, rel, idx, _read_suite_file(root, rel), "changed outside testbot", "")
        except RevisionError:
            outside = []
    else:
        outside = []
    changed = ["(first revision)"] if created else _apply(root, rel, idx, suite, source, note)
    _prune(root, rel, idx)
    _save(root, rel, idx)
    written = _write_suite(root, rel, idx, suite)
    return {"revision": idx["default_manifest"], "changed": changed, "unchanged": not changed, "outside_changes": outside,
            "cases": {c["id"]: c["revision"] for c in written["cases"]}}


def add_draft(root: Path, rel: str, case_id: str, content: dict[str, Any], source: str, note: str = "") -> int:
    """A proposal (chat, agent): a new revision of a case that is NOT the default. Returns its number."""
    idx = ensure(root, rel)
    rev = _new_case_rev(root, rel, idx, case_id, _core_case(content), status="draft", source=source, note=note)
    _save(root, rel, idx)
    return rev


def make_default_case(root: Path, rel: str, case_id: str, rev: int, source: str = "activated") -> dict[str, Any]:
    idx = ensure(root, rel)
    lin = idx["cases"].get(case_id)
    if not lin or str(rev) not in lin["revisions"]:
        raise RevisionError(f"{case_id} has no revision {rev}")
    if lin.get("default") == rev and not lin.get("removed"):
        return {"revision": idx["default_manifest"], "unchanged": True}
    old = lin.get("default")
    if old is not None and str(old) in lin["revisions"] and old != rev:
        lin["revisions"][str(old)]["status"] = "superseded"
    lin["revisions"][str(rev)]["status"] = "default"
    lin["default"] = rev
    m = idx["manifests"][idx["default_manifest"]]
    order = list(m["order"]) + ([case_id] if case_id not in m["order"] else [])
    lin["removed"] = False
    mapping = {cid: idx["cases"][cid]["default"] for cid in order}
    _new_manifest(idx, mapping, order, source, f"{case_id} revision {rev}")
    _prune(root, rel, idx)
    _save(root, rel, idx)
    _write_suite(root, rel, idx)
    return {"revision": idx["default_manifest"], "unchanged": False}


def make_default_manifest(root: Path, rel: str, rid: str, source: str = "activated") -> dict[str, Any]:
    idx = ensure(root, rel)
    m = idx["manifests"].get(rid)
    if not m:
        raise RevisionError(f"there is no suite revision {rid}")
    if rid == idx["default_manifest"]:
        return {"revision": rid, "unchanged": True}
    for cid, lin in idx["cases"].items():
        if cid in m["cases"]:
            rev = m["cases"][cid]
            old = lin.get("default")
            if old is not None and str(old) in lin["revisions"] and old != rev:
                lin["revisions"][str(old)]["status"] = "superseded"
            lin["revisions"][str(rev)]["status"] = "default"
            lin["default"], lin["removed"] = rev, False
        else:
            lin["removed"] = True
    sid = m["settings"]
    old = idx["settings"].get("default")
    if old and old != sid:
        idx["settings"]["revisions"][old]["status"] = "superseded"
    idx["settings"]["revisions"][sid]["status"] = "default"
    idx["settings"]["default"] = sid
    idx["manifests"][idx["default_manifest"]]["status"] = "superseded"
    m["status"] = "default"
    idx["default_manifest"] = rid
    _save(root, rel, idx)
    _write_suite(root, rel, idx)
    return {"revision": rid, "unchanged": False}


def edit_case_revision(root: Path, rel: str, case_id: str, rev: int, content: dict[str, Any], note: str = "") -> None:
    """Change a revision that is NOT the default, in place (no new revision)."""
    idx = ensure(root, rel)
    lin = idx["cases"].get(case_id)
    if not lin or str(rev) not in lin["revisions"]:
        raise RevisionError(f"{case_id} has no revision {rev}")
    if lin["revisions"][str(rev)]["status"] == "default":
        raise RevisionError("this is the default revision: save it normally (that creates a new revision) or edit another one")
    _write(_case_file(root, rel, case_id, rev), _core_case(content))
    lin["revisions"][str(rev)]["edited"] = _now()
    if note:
        lin["revisions"][str(rev)]["note"] = note
    _save(root, rel, idx)


def edit_settings_revision(root: Path, rel: str, sid: str, content: dict[str, Any], note: str = "") -> None:
    idx = ensure(root, rel)
    rec = idx["settings"]["revisions"].get(sid)
    if not rec:
        raise RevisionError(f"there is no settings revision {sid}")
    if rec["status"] == "default":
        raise RevisionError("this is the default revision: save it normally (that creates a new revision) or edit another one")
    _write(_settings_file(root, rel, sid), _core_settings(content))
    rec["edited"] = _now()
    if note:
        rec["note"] = note
    _save(root, rel, idx)


def delete_case_revision(root: Path, rel: str, case_id: str, rev: int) -> None:
    idx = ensure(root, rel)
    lin = idx["cases"].get(case_id)
    if not lin or str(rev) not in lin["revisions"]:
        raise RevisionError(f"{case_id} has no revision {rev}")
    if lin["revisions"][str(rev)]["status"] == "default":
        raise RevisionError("the default revision cannot be deleted: make another one the default first")
    users = [r for r, m in idx["manifests"].items() if m["cases"].get(case_id) == rev]
    if users:
        raise RevisionError(f"revision {rev} of {case_id} is part of suite revision {', '.join(users)}: delete those first")
    del lin["revisions"][str(rev)]
    _case_file(root, rel, case_id, rev).unlink(missing_ok=True)
    _save(root, rel, idx)


def delete_manifest(root: Path, rel: str, rid: str) -> None:
    idx = ensure(root, rel)
    if rid == idx["default_manifest"]:
        raise RevisionError("the default suite revision cannot be deleted")
    if rid not in idx["manifests"]:
        raise RevisionError(f"there is no suite revision {rid}")
    del idx["manifests"][rid]
    _prune(root, rel, idx)
    _save(root, rel, idx)


# ------------------------------------------------------------------ reading

def case_content(root: Path, rel: str, case_id: str, rev: Any) -> dict[str, Any]:
    p = _case_file(root, rel, case_id, rev)
    if not p.is_file():
        raise RevisionError(f"{case_id} has no revision {rev}")
    return _read(p)


def settings_content(root: Path, rel: str, sid: str) -> dict[str, Any]:
    p = _settings_file(root, rel, sid)
    if not p.is_file():
        raise RevisionError(f"there is no settings revision {sid}")
    return _read(p)


def manifest_suite(root: Path, rel: str, rid: str) -> dict[str, Any]:
    """The whole suite as it was in suite revision `rid`."""
    idx = ensure(root, rel)
    m = idx["manifests"].get(rid)
    if not m:
        raise RevisionError(f"there is no suite revision {rid}")
    suite = settings_content(root, rel, m["settings"])
    suite["cases"] = [case_content(root, rel, cid, m["cases"][cid]) for cid in m["order"]]
    return suite


def summary(root: Path, rel: str) -> dict[str, Any]:
    idx = ensure(root, rel)
    ids = sorted(idx["manifests"], key=lambda r: int(r[1:]))
    manifests = []
    for n, rid in enumerate(ids):
        m = idx["manifests"][rid]
        prev = idx["manifests"][ids[n - 1]] if n else None
        changed = []
        if prev is None:
            changed = ["(first revision)"]
        else:
            if m["settings"] != prev["settings"]:
                changed.append("settings")
            for cid in m["order"]:
                if cid not in prev["cases"]:
                    changed.append(f"{cid} added")
                elif prev["cases"][cid] != m["cases"][cid]:
                    changed.append(f"{cid} → rev {m['cases'][cid]}")
            changed += [f"{cid} removed" for cid in prev["order"] if cid not in m["cases"]]
            if not changed and prev["order"] != m["order"]:
                changed.append("order")
        manifests.append({"id": rid, **{k: m[k] for k in ("created", "source", "note", "status", "settings")}, "cases": m["cases"], "changed": changed})
    cases = {}
    for cid, lin in idx["cases"].items():
        cases[cid] = {"default": lin.get("default"), "removed": bool(lin.get("removed")),
                      "revisions": [{"rev": int(r), **rec} for r, rec in sorted(lin["revisions"].items(), key=lambda kv: int(kv[0]))]}
    settings = [{"id": sid, **rec} for sid, rec in sorted(idx["settings"]["revisions"].items(), key=lambda kv: int(kv[0][1:]))]
    drafts = sum(1 for lin in idx["cases"].values() for rec in lin["revisions"].values() if rec["status"] == "draft")
    return {"default": idx["default_manifest"], "default_settings": idx["settings"]["default"], "manifests": list(reversed(manifests)), "cases": cases,
            "settings": list(reversed(settings)), "drafts": drafts}


def editor_info(root: Path, rel: str) -> dict[str, Any]:
    """What the editor shows: the default revision numbers and how many drafts wait."""
    s = summary(root, rel)
    return {"default": s["default"], "drafts": s["drafts"], "cases": {cid: {"default": c["default"], "removed": c["removed"],
            "drafts": [r["rev"] for r in c["revisions"] if r["status"] == "draft"]} for cid, c in s["cases"].items()}}


def quick_counts(root: Path, rel: str) -> Optional[dict[str, Any]]:
    """For the test list: revision id and number of drafts, without creating anything."""
    idx = _load(root, rel)
    if not idx:
        return None
    return {"revision": idx["default_manifest"], "drafts": sum(1 for lin in idx["cases"].values() for rec in lin["revisions"].values() if rec["status"] == "draft")}


# ------------------------------------------------------------------ comparing

def _sig(st: dict[str, Any]) -> str:
    return _norm({k: st.get(k) for k in ("action", "description", "target", "input")})


def _short(v: Any, n: int = 90) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, sort_keys=True)
    return s if len(s) <= n else s[: n - 1] + "…"


def diff_cases(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    """What changed from case version `a` to `b`: case-level fields, then the steps (added / removed / changed, matched by content)."""
    out: list[dict[str, Any]] = []
    for k in sorted((set(a) | set(b)) - {"steps", "step_no"} - set(META_KEYS)):
        if _norm(a.get(k)) != _norm(b.get(k)):
            out.append({"op": "field", "field": k, "from": _short(a.get(k)), "to": _short(b.get(k))})
    sa, sb = _core_case(a).get("steps") or [], _core_case(b).get("steps") or []
    sm = difflib.SequenceMatcher(a=[_sig(s) for s in sa], b=[_sig(s) for s in sb], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for x, y in zip(sa[i1:i2], sb[j1:j2]):
                diffs = {f: [_short(x.get(f)), _short(y.get(f))] for f in _FIELDS if _norm(x.get(f)) != _norm(y.get(f))}
                if diffs:
                    out.append({"op": "changed", "step": y.get("step_no"), "was": x.get("step_no"), "description": y.get("description", ""), "fields": diffs})
        else:
            pairs = min(i2 - i1, j2 - j1)
            for k in range(pairs):                     # replaced in place: show it as a change of that step
                x, y = sa[i1 + k], sb[j1 + k]
                diffs = {f: [_short(x.get(f)), _short(y.get(f))] for f in _FIELDS if _norm(x.get(f)) != _norm(y.get(f))}
                out.append({"op": "changed", "step": y.get("step_no"), "was": x.get("step_no"), "description": y.get("description", ""), "fields": diffs})
            for x in sa[i1 + pairs:i2]:
                out.append({"op": "removed", "step": x.get("step_no"), "description": x.get("description", ""), "action": x.get("action")})
            for y in sb[j1 + pairs:j2]:
                out.append({"op": "added", "step": y.get("step_no"), "description": y.get("description", ""), "action": y.get("action")})
    return out


def diff_manifests(root: Path, rel: str, a: str, b: str) -> dict[str, Any]:
    idx = ensure(root, rel)
    ma, mb = idx["manifests"].get(a), idx["manifests"].get(b)
    if not ma or not mb:
        raise RevisionError("unknown suite revision")
    cases = []
    for cid in list(dict.fromkeys(list(ma["order"]) + list(mb["order"]))):
        ra, rb = ma["cases"].get(cid), mb["cases"].get(cid)
        if ra is None:
            cases.append({"case": cid, "change": "added", "rev_b": rb})
        elif rb is None:
            cases.append({"case": cid, "change": "removed", "rev_a": ra})
        elif ra != rb:
            cases.append({"case": cid, "change": "changed", "rev_a": ra, "rev_b": rb, "steps": diff_cases(case_content(root, rel, cid, ra), case_content(root, rel, cid, rb))})
    sa, sb = settings_content(root, rel, ma["settings"]), settings_content(root, rel, mb["settings"])
    return {"a": a, "b": b, "settings": [{"field": k, "from": _short(sa.get(k)), "to": _short(sb.get(k))} for k in sorted(set(sa) | set(sb)) if _norm(sa.get(k)) != _norm(sb.get(k))],
            "cases": cases, "order_changed": ma["order"] != mb["order"]}
