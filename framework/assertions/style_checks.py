"""Checks on how elements LOOK and are ordered: computed CSS values (e.g. background colour), CSS classes,
and "the top N rows of a table/list are these, in this order, with these colours".

Colours are compared as the browser sees them: any CSS colour the author writes (#ffcccc, rgb(255,204,204),
red, hsl(...)) is converted by the browser itself before comparing, so it does not matter how either side is written."""
from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import ElementHandle

_ALIASES = {"background": "background-color", "bg": "background-color", "text_color": "color"}

# Normalises `value` for CSS property `prop` the way the browser would compute it (null = not a valid value).
_NORMALIZE_JS = """
(prop, value) => {
  const probe = document.createElement('div');
  probe.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none';
  probe.style.setProperty(prop, value);
  if (probe.style.getPropertyValue(prop) === '') return null;
  document.body.appendChild(probe);
  const out = getComputedStyle(probe).getPropertyValue(prop);
  probe.remove();
  return out;
}
"""


def _prop(name: str) -> str:
    name = name.strip()
    return _ALIASES.get(name, name)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_pairs(element: ElementHandle, pairs: list[tuple[str, str]]) -> list[str]:
    """Browser-normalised form of each (property, value); raises if a value isn't valid CSS for that property."""
    out = element.evaluate(f"(el, pairs) => pairs.map(([p, v]) => ({_NORMALIZE_JS})(p, v))", [list(p) for p in pairs])
    for (prop, value), norm in zip(pairs, out):
        if norm is None:
            raise ValueError(f"'{value}' is not a valid value for CSS property '{prop}'")
    return out


def css_equals(element: ElementHandle, prop: str, wanted: str) -> tuple[bool, str, str]:
    prop = _prop(prop or "background-color")
    actual = element.evaluate("(el, p) => getComputedStyle(el).getPropertyValue(p)", prop)
    (wanted_norm,) = normalize_pairs(element, [(prop, str(wanted))])
    return _squash(actual) == _squash(wanted_norm), _squash(actual), _squash(wanted_norm)


def has_classes(element: ElementHandle, wanted: Any) -> tuple[bool, list[str], list[str]]:
    names = wanted if isinstance(wanted, list) else str(wanted).split()
    actual = element.evaluate("el => Array.from(el.classList)")
    return all(n in actual for n in names), actual, names


_ROWS_JS = """
(container, o) => {
  const squash = s => (s || '').replace(/\\s+/g, ' ').trim();
  let rows;
  if (o.rows) rows = Array.from(container.querySelectorAll(o.rows));
  else if (container.classList.contains('DynamicGrid')) {   // Apriso grid: data rows live under an element whose id contains 'content'
    rows = Array.from(container.querySelectorAll("[id*='content'] tr:not(.Dummy)"));   // first row is an empty placeholder
  } else if (container.tagName === 'TABLE') {
    rows = Array.from(container.querySelectorAll('tbody tr'));
    if (!rows.length) rows = Array.from(container.querySelectorAll('tr'));
  } else if (container.tagName === 'TBODY' || container.tagName === 'THEAD') rows = Array.from(container.querySelectorAll('tr'));
  else rows = Array.from(container.children);
  return rows.slice(0, o.n).map(row => {
    const pick = sel => (!sel || sel === 'row' || sel === ':scope') ? row : row.querySelector(sel);
    const item = pick(o.item);
    const style = o.styleItem ? pick(o.styleItem) : item;
    const cs = style ? getComputedStyle(style) : null;
    const css = {};
    for (const p of o.props) css[p] = cs ? cs.getPropertyValue(p) : null;
    return {found: !!item && !!style, text: squash(item ? (item.innerText || item.textContent) : ''),   // visible text: cells come out space-separated
            classes: style ? Array.from(style.classList) : [], css};
  });
}
"""


def _row_specs(value: Any) -> list[dict[str, Any]]:
    import json

    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or not value:
        raise ValueError("list_matches needs 'value': a non-empty list of row specs, e.g. "
                         '[{"text": "Order-A", "background": "#ffcccc"}, {"text": "Order-B"}]')
    return [v if isinstance(v, dict) else {"text": str(v)} for v in value]


def _spec_css(spec: dict[str, Any]) -> dict[str, str]:
    css = {_prop(k): str(v) for k, v in (spec.get("css") or {}).items()}
    for key in ("background", "bg", "color", "text_color"):
        if key in spec:
            css[_prop(key)] = str(spec[key])
    return css


def list_matches(container: ElementHandle, value: Any, *, rows: str | None, item: str | None,
                 style_item: str | None) -> tuple[bool, list[str], list[str]]:
    """Top N rows (N = number of specs) must match the specs in order. Each spec may have
    text / text_contains / background / color / css {prop: value} / class / not_class.
    Returns (passed, actual lines, expected lines); a differing row is flagged in the actual line."""
    specs = _row_specs(value)
    spec_css = [_spec_css(s) for s in specs]
    props = sorted({p for c in spec_css for p in c})
    normed: dict[tuple[str, str], str] = {}
    pairs = sorted({(p, v) for c in spec_css for p, v in c.items()})
    if pairs:
        for pair, norm in zip(pairs, normalize_pairs(container, pairs)):
            normed[pair] = _squash(norm)

    found = container.evaluate(_ROWS_JS, {"rows": rows, "item": item, "styleItem": style_item, "n": len(specs), "props": props})

    passed = True
    actual_lines: list[str] = []
    expected_lines: list[str] = []
    for i, spec in enumerate(specs, start=1):
        want_bits: list[str] = []
        if "text" in spec:
            want_bits.append(f"text={spec['text']!r}")
        if "text_contains" in spec:
            want_bits.append(f"text contains {spec['text_contains']!r}")
        for p, v in spec_css[i - 1].items():
            want_bits.append(f"{p}={v}")
        for key in ("class", "not_class"):
            if key in spec:
                want_bits.append(f"{key}={spec[key]}")
        expected_lines.append(f"{i}. " + ", ".join(want_bits))

        if i > len(found) or not found[i - 1]["found"]:
            passed = False
            actual_lines.append(f"{i}. (missing -- only {len(found)} row(s), or the item/style selector matched nothing)")
            continue
        row = found[i - 1]
        ok = True
        got_bits: list[str] = []
        if "text" in spec:
            got_bits.append(f"text={row['text']!r}")
            ok &= row["text"] == _squash(str(spec["text"]))
        if "text_contains" in spec:
            got_bits.append(f"text={row['text']!r}")
            ok &= _squash(str(spec["text_contains"])) in row["text"]
        for p, v in spec_css[i - 1].items():
            got = _squash(row["css"].get(p) or "")
            got_bits.append(f"{p}={got}")
            ok &= got == normed[(p, v)]
        for key, want_present in (("class", True), ("not_class", False)):
            if key in spec:
                names = spec[key] if isinstance(spec[key], list) else str(spec[key]).split()
                got_bits.append(f"classes={row['classes']}")
                ok &= all((n in row["classes"]) == want_present for n in names)
        passed &= ok
        actual_lines.append(f"{i}. " + ", ".join(dict.fromkeys(got_bits)) + ("" if ok else "   <-- differs"))
    return passed, actual_lines, expected_lines
