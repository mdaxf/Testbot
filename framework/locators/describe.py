"""Describe a located element as a durable testbot target (used for self-healing: when the AI fallback found an element that the scripted target no longer matched,
the recommendation proposes this target instead). Preference: test id, id, name, placeholder, aria-label, then a short CSS path."""
from __future__ import annotations

from typing import Any, Optional

_JS = """el => {
  const q = (s) => { try { return document.querySelectorAll(s).length; } catch (e) { return 0; } };
  const esc = (v) => String(v).replace(/"/g, '\\\\"');
  for (const a of ['data-testid', 'data-test-id', 'data-test', 'data-qa']) {
    const v = el.getAttribute(a); if (v && q('[' + a + '="' + esc(v) + '"]') === 1) return { strategy: 'testid', value: v, attr: a };
  }
  if (el.id && q('[id="' + esc(el.id) + '"]') === 1) return { strategy: 'css', value: /^[A-Za-z_][\\w-]*$/.test(el.id) ? '#' + el.id : '[id="' + esc(el.id) + '"]' };
  const name = el.getAttribute('name'); if (name && q(el.tagName.toLowerCase() + '[name="' + esc(name) + '"]') === 1) return { strategy: 'css', value: el.tagName.toLowerCase() + '[name="' + esc(name) + '"]' };
  const ph = el.getAttribute('placeholder'); if (ph && q('[placeholder="' + esc(ph) + '"]') === 1) return { strategy: 'placeholder', value: ph };
  const al = el.getAttribute('aria-label'); if (al && q('[aria-label="' + esc(al) + '"]') === 1) return { strategy: 'css', value: '[aria-label="' + esc(al) + '"]' };
  const parts = []; let n = el;
  while (n && n.nodeType === 1 && n !== document.body && parts.length < 5) {
    let seg = n.tagName.toLowerCase();
    if (n.id && q('[id="' + esc(n.id) + '"]') === 1) { parts.unshift('#' + n.id); break; }
    const sib = n.parentElement ? Array.from(n.parentElement.children).filter((c) => c.tagName === n.tagName) : [];
    if (sib.length > 1) seg += ':nth-of-type(' + (sib.indexOf(n) + 1) + ')';
    parts.unshift(seg); n = n.parentElement;
  }
  return { strategy: 'css', value: parts.join(' > ') };
}"""


def element_target(handle: Any) -> Optional[dict[str, Any]]:
    """{'strategy': ..., 'value': ...} for an ElementHandle, or None when it cannot be described."""
    try:
        t = handle.evaluate(_JS)
    except Exception:  # noqa: BLE001 - a detached element etc.: no recommendation, never an error
        return None
    if not isinstance(t, dict) or not t.get("value"):
        return None
    if t.get("attr") and t["attr"] != "data-testid":      # testbot's `testid` strategy reads data-testid; other attributes become a css target
        return {"strategy": "css", "value": f'[{t["attr"]}="{t["value"]}"]'}
    return {"strategy": t["strategy"], "value": t["value"]}
