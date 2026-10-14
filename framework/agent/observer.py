"""What the agent sees: a compact, numbered text description of the page (every frame, pop-ups first), grids as rows of text,
visible error messages, and network/console problems since the last look.

Elements are tagged in the page with data-tb-agent="e12" so the agent can refer to them by id; ids are only valid for the
latest observation. A robust locator description (for exporting the run as a script) is computed lazily for elements that are acted on."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

from playwright.sync_api import Frame, Page

from framework.agent.config import Profile

MAX_ELEMENTS_PER_FRAME = 120
MAX_TEXT = 1500

_HELPERS_JS = r"""
const norm = s => (s || '').replace(/\s+/g, ' ').trim();
const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
const IMPLICIT = { button: 'button', a: 'link', select: 'combobox', textarea: 'textbox', summary: 'button', h1: 'heading', h2: 'heading', h3: 'heading', img: 'img', li: 'listitem', tr: 'row', td: 'cell', th: 'columnheader' };
function roleOf(el) {
  const explicit = el.getAttribute('role'); if (explicit) return explicit.split(' ')[0];
  const tag = el.tagName.toLowerCase();
  if (tag === 'a') return el.hasAttribute('href') ? 'link' : null;
  if (tag === 'input') {
    const t = (el.type || 'text').toLowerCase();
    if (['button', 'submit', 'reset', 'image'].includes(t)) return 'button';
    if (t === 'checkbox') return 'checkbox'; if (t === 'radio') return 'radio'; if (t === 'search') return 'searchbox';
    return 'textbox';
  }
  return IMPLICIT[tag] || null;
}
function labelText(el) {
  if (el.labels && el.labels.length) {            // the label's own words, without the text of controls nested inside it (a <select>'s options)
    const l = el.labels[0].cloneNode(true); l.querySelectorAll('select,input,textarea,button').forEach(x => x.remove());
    const t = norm(l.textContent); if (t) return t;
  }
  return norm(el.getAttribute('aria-label'));
}
function nameOf(el) {
  const al = el.getAttribute('aria-label'); if (al) return norm(al);
  const lb = el.getAttribute('aria-labelledby');
  if (lb) { const t = lb.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean).map(n => n.textContent).join(' '); if (norm(t)) return norm(t); }
  if (el.tagName === 'INPUT' && ['button', 'submit', 'reset'].includes((el.type || '').toLowerCase())) return norm(el.value);
  if (el.tagName === 'IMG') return norm(el.alt);
  const lt = labelText(el); if (lt) return lt;
  const cls = Array.from(el.classList || []).find(c => /^fc_/.test(c));
  const holder = el.closest ? el.closest('[class*="fc_"]') : null;
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName) && holder) {           // Apriso form controls: name lives in the fc_<Name> container
    const c = Array.from(holder.classList).find(x => /^fc_/.test(x)); if (c) return c.slice(3);
  }
  return norm(el.innerText || el.textContent) || norm(el.title) || norm(el.placeholder) || norm(el.getAttribute('data-key'));
}
"""

OBSERVE_JS = "(args) => {" + _HELPERS_JS + r"""
  const start = args.start, popupSel = args.popupSel, maxEl = args.maxEl, maxText = args.maxText;
  document.querySelectorAll('[data-tb-agent]').forEach(e => e.removeAttribute('data-tb-agent'));
  const SEL = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=menuitem],[role=tab],[role=checkbox],[role=radio],[role=switch],[role=option],[role=combobox],[role=textbox],[onclick],td[data-value],[contenteditable=true]';
  const base = Array.from(document.querySelectorAll(SEL));
  const baseSet = new Set(base), extraSet = new Set(), extra = [];
  // modern UIs make plain <div>/<span>/custom elements clickable with script handlers: list the outermost elements that LOOK clickable
  let scanned = 0;
  for (const el of (document.body ? document.body.querySelectorAll('*') : [])) {
    if (++scanned > 4000) break;
    if (baseSet.has(el) || ['SCRIPT', 'STYLE', 'HTML', 'BODY'].includes(el.tagName) || !vis(el)) continue;
    if (getComputedStyle(el).cursor !== 'pointer') continue;
    const par = el.parentElement; if (par && getComputedStyle(par).cursor === 'pointer') continue;      // the outermost one only
    if (el.closest(SEL) || el.querySelector(SEL) || !norm(el.innerText)) continue;                      // already represented by a real control
    extra.push(el); extraSet.add(el);
  }
  let els = base.concat(extra).filter(vis).filter(e => !(e.tagName === 'INPUT' && (e.type || '').toLowerCase() === 'hidden'));
  const inPopup = el => { if (popupSel && el.closest(popupSel)) return true; return !!el.closest('dialog[open],[role=dialog],[aria-modal=true],.modal.show'); };
  const popupPresent = els.some(inPopup);
  els = els.map((e, i) => [e, i]).sort((a, b) => (inPopup(b[0]) ? 1 : 0) - (inPopup(a[0]) ? 1 : 0) || a[1] - b[1]).map(x => x[0]);
  const out = [];
  for (const el of els.slice(0, maxEl)) {
    const id = 'e' + (start + out.length + 1); el.setAttribute('data-tb-agent', id);
    const tag = el.tagName.toLowerCase(), type = (el.type || '').toLowerCase();
    const info = { id, tag, role: extraSet.has(el) ? 'clickable' : (roleOf(el) || tag), name: nameOf(el).slice(0, 80), popup: inPopup(el) };
    if (['input', 'textarea', 'select'].includes(tag)) {
      info.type = type || tag; info.value = type === 'password' ? (el.value ? '***' : '') : String(el.value || '').slice(0, 80);
      if (el.required) info.required = true; if (el.placeholder) info.placeholder = el.placeholder.slice(0, 40);
      if (el.maxLength > 0 && el.maxLength < 100000) info.maxlength = el.maxLength; if (el.pattern) info.pattern = el.pattern.slice(0, 40);
      if (el.min) info.min = el.min; if (el.max) info.max = el.max; if (el.readOnly) info.readonly = true;
    }
    if (type === 'checkbox' || type === 'radio') info.checked = el.checked;
    if (tag === 'select') { info.options = Array.from(el.options).slice(0, 25).map(o => norm(o.text) || o.value); const so = el.options[el.selectedIndex]; info.value = so ? norm(so.text) : ''; }
    else if (el.list) info.options = Array.from(el.list.options).slice(0, 25).map(o => o.value);
    if (el.disabled) info.disabled = true;
    if (tag === 'a') info.href = (el.getAttribute('href') || '').slice(0, 60);
    out.push(info);
  }
  const rowsOf = t => t.classList.contains('DynamicGrid') ? Array.from(t.querySelectorAll("[id*='content'] tr:not(.Dummy)"))
      : (t.querySelectorAll('tbody tr').length ? Array.from(t.querySelectorAll('tbody tr')) : Array.from(t.querySelectorAll('tr')));
  const tables = []; let tn = 0;
  document.querySelectorAll('table, .DynamicGrid').forEach(t => {
    if (!vis(t) || (t.tagName === 'TABLE' && t.closest('.DynamicGrid'))) return;
    const rows = rowsOf(t); if (!rows.length) return;
    const id = 't' + (args.tstart + (++tn)); t.setAttribute('data-tb-agent', id);
    const heads = Array.from(t.querySelectorAll('thead th, .HeadT td, th')).map(h => norm(h.innerText)).filter(Boolean).slice(0, 12);
    tables.push({ id, heads, count: rows.length, rows: rows.slice(0, 10).map(r => Array.from(r.children).map(c => norm(c.innerText).slice(0, 40)).join(' | ')) });
  });
  const messages = Array.from(document.querySelectorAll('[role=alert],[aria-live=assertive],.ErrorMessage,.apr-message-error,.error-message,.alert-danger,.toast,.notification'))
      .filter(vis).map(e => norm(e.innerText).slice(0, 200)).filter(Boolean).slice(0, 5);
  const text = norm(document.body ? document.body.innerText : '').slice(0, maxText);
  return { elements: out, tables, messages, text, popupPresent, total: els.length };
}"""

# a stable locator description for one element, used when a run is exported as a script (same priorities as the recorder)
DESCRIBE_JS = "(el) => {" + _HELPERS_JS + r"""
  const q = sel => { try { return Array.from(document.querySelectorAll(sel)); } catch (_) { return []; } };
  const stable = id => id && !/\\d{4,}|^:r|^ember|^react|^radix/i.test(id);
  const cssPath = e => { const parts = []; while (e && e.nodeType === 1 && parts.length < 6) {
      if (stable(e.id) && q('#' + CSS.escape(e.id)).length === 1) { parts.unshift('#' + CSS.escape(e.id)); break; }
      let p = e.tagName.toLowerCase(); const par = e.parentElement;
      if (par) { const same = Array.from(par.children).filter(c => c.tagName === e.tagName); if (same.length > 1) p += ':nth-of-type(' + (same.indexOf(e) + 1) + ')'; }
      parts.unshift(p); if (e.tagName === 'BODY') break; e = par; } return parts.join(' > '); };
  const out = []; const tag = el.tagName.toLowerCase();
  const tid = el.getAttribute('data-testid'); if (tid) out.push({ strategy: 'testid', value: tid });
  const field = ['input', 'select', 'textarea'].includes(tag) && !['button', 'submit', 'reset', 'image', 'checkbox', 'radio'].includes((el.type || '').toLowerCase());
  if (field) { const lab = labelText(el); if (lab && lab.length <= 80) out.push({ strategy: 'label', value: lab }); if (el.placeholder) out.push({ strategy: 'placeholder', value: el.placeholder }); }
  const role = roleOf(el), name = nameOf(el);
  if (role && name && name.length <= 80) out.push({ strategy: 'role', value: role, name });
  for (const attr of ['name', 'data-key', 'data-test', 'data-qa']) { const v = el.getAttribute(attr); if (v) out.push({ strategy: 'css', value: tag + '[' + attr + '="' + CSS.escape(v) + '"]' }); }
  const holder = el.closest('[class*="fc_"]'); if (holder && ['input', 'select', 'textarea'].includes(tag)) { const c = Array.from(holder.classList).find(x => /^fc_/.test(x)); if (c) out.push({ strategy: 'css', value: '.' + c + ' ' + tag }); }
  if (stable(el.id)) out.push({ strategy: 'css', value: '#' + CSS.escape(el.id) });
  if (name && name.length <= 60) out.push({ strategy: 'text', value: name });
  out.push({ strategy: 'css', value: cssPath(el) });
  return out;
}"""

_FRAME_CSS_JS = """(el) => {
  if (el.id && !/\\d{4,}/.test(el.id)) return '#' + CSS.escape(el.id);
  const c = Array.from(el.classList || [])[0]; if (c) return 'iframe.' + CSS.escape(c);
  const same = Array.from(document.querySelectorAll('iframe')); return 'iframe >> nth=' + same.indexOf(el);
}"""

DESTRUCTIVE = r"(?i)\b(delete|remove|destroy|drop|purge|truncate|erase|pay|purchase|buy|place order|send|publish|approve|reject|deactivate|terminate)\b"


@dataclass
class ElementRef:
    id: str
    frame: Frame
    frame_css: Optional[str]
    info: dict[str, Any]


@dataclass
class Observation:
    url: str
    title: str
    text: str                                  # the description given to the model
    elements: dict[str, ElementRef] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)
    popup_present: bool = False
    health: dict[str, list[str]] = field(default_factory=dict)


class PageWatch:
    """Network and console activity, so the agent waits for the page to be quiet and notices errors the UI does not show."""

    def __init__(self, page: Page, profile: Profile):
        self.page, self.profile = page, profile
        self.pending = 0
        self.host = ""
        self.new_http: list[str] = []
        self.new_console: list[str] = []
        self.all_http: list[str] = []
        self.all_console: list[str] = []
        page.on("request", lambda r: self._inc())
        page.on("requestfinished", lambda r: self._dec())
        page.on("requestfailed", lambda r: self._dec())
        page.on("response", self._on_response)
        page.on("console", lambda m: self._console(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self._console("uncaught: " + str(e)))

    def _inc(self): self.pending += 1
    def _dec(self): self.pending = max(0, self.pending - 1)

    def _on_response(self, resp) -> None:
        try:
            if resp.status >= 500 and (not self.host or urlsplit(resp.url).netloc == self.host):
                msg = f"HTTP {resp.status} {resp.request.method} {resp.url[:100]}"
                self.new_http.append(msg); self.all_http.append(msg)
        except Exception:  # noqa: BLE001
            pass

    def _console(self, text: str) -> None:
        self.new_console.append(text[:160]); self.all_console.append(text[:160])

    def drain(self) -> dict[str, list[str]]:
        out = {"http": self.new_http, "console": self.new_console}
        self.new_http, self.new_console = [], []
        return out

    def busy(self) -> bool:
        if self.pending > 0:
            return True
        if self.profile.spinner:
            for frame in self.page.frames:
                try:
                    loc = frame.locator(self.profile.spinner)
                    if loc.count() and loc.first.is_visible():
                        return True
                except Exception:  # noqa: BLE001 - a frame that is navigating
                    continue
        return False

    def settle(self, max_wait: float = 8.0, quiet: float = 0.4) -> None:
        end, quiet_since = time.monotonic() + max_wait, None
        while time.monotonic() < end:
            if not self.busy():
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic() - quiet_since >= quiet:
                    return
            else:
                quiet_since = None
            try:
                self.page.wait_for_timeout(100)
            except Exception:  # noqa: BLE001 - the page closed
                return


class Observer:
    def __init__(self, page: Page, profile: Profile, watch: PageWatch, secret_values: Optional[set[str]] = None):
        self.page, self.profile, self.watch = page, profile, watch
        self.secrets = secret_values or set()
        self.last: Optional[Observation] = None

    def _frames(self) -> list[tuple[Frame, Optional[str], str]]:
        out: list[tuple[Frame, Optional[str], str]] = [(self.page.main_frame, None, "main")]
        for frame in self.page.frames:
            if frame == self.page.main_frame or frame.url in ("", "about:blank") or frame.parent_frame != self.page.main_frame:
                continue
            try:
                el = frame.frame_element()
                box = el.bounding_box()
                if not box or box["width"] < 5 or box["height"] < 5:
                    continue
                css = el.evaluate(_FRAME_CSS_JS)
            except Exception:  # noqa: BLE001
                continue
            label = "screen" if (self.profile.frame and (self.profile.frame.lstrip(".#") in css)) else "frame"
            out.append((frame, css, label))
        return out

    def _redact(self, text: str) -> str:
        for s in self.secrets:
            if s and len(s) >= 3:
                text = text.replace(s, "***")
        return text

    def snapshot(self, settle: bool = True) -> Observation:
        if settle:
            self.watch.settle()
        elements: dict[str, ElementRef] = {}
        lines: list[str] = []
        table_lines: list[str] = []
        messages: list[str] = []
        texts: list[str] = []
        popup = False
        counter = tcounter = 0
        for frame, css, label in self._frames():
            try:
                data = frame.evaluate(OBSERVE_JS, {"start": counter, "tstart": tcounter, "popupSel": self.profile.popup, "maxEl": MAX_ELEMENTS_PER_FRAME, "maxText": MAX_TEXT})
            except Exception:  # noqa: BLE001 - frame navigating / detached
                continue
            popup = popup or data["popupPresent"]
            where = "" if label == "main" else f" @{label}"
            for info in data["elements"]:
                elements[info["id"]] = ElementRef(info["id"], frame, css, info)
                lines.append(self._fmt_element(info, where))
            counter += len(data["elements"])
            if data["total"] > len(data["elements"]):
                lines.append(f"  ... {data['total'] - len(data['elements'])} more controls not listed{where}")
            for t in data["tables"]:
                tcounter += 1
                elements[t["id"]] = ElementRef(t["id"], frame, css, {"id": t["id"], "role": "table", "name": "table"})
                table_lines.append(f"[{t['id']}]{where} columns: {' | '.join(t['heads']) or '(none)'} -- {t['count']} rows; first rows:")
                table_lines += [f"     {i}: {r}" for i, r in enumerate(t["rows"], start=1)]
            messages += [f"{m}{where}" for m in data["messages"]]
            if data["text"]:
                texts.append(f"{label}: {data['text']}")
        health = self.watch.drain()
        url, title = self.page.url, ""
        try:
            title = self.page.title()
        except Exception:  # noqa: BLE001
            pass
        parts = [f"URL: {url}", f"TITLE: {title}"]
        if popup:
            parts.append("POP-UP OPEN: controls marked [popup] are in front; the others are behind it and cannot be used until it is closed.")
        if messages:
            parts.append("MESSAGES ON SCREEN: " + " || ".join(messages))
        parts.append("CONTROLS:\n" + ("\n".join(lines) if lines else "  (none visible)"))
        if table_lines:
            parts.append("TABLES / GRIDS:\n" + "\n".join(table_lines))
        if texts:
            parts.append("VISIBLE TEXT (excerpt):\n" + "\n".join(f"  {t}" for t in texts))
        if health["http"] or health["console"]:
            parts.append("PROBLEMS SINCE LAST LOOK: " + " || ".join(health["http"] + health["console"]))
        obs = Observation(url=url, title=title, text=self._redact("\n".join(parts)), elements=elements, messages=messages, popup_present=popup, health=health)
        self.last = obs
        return obs

    @staticmethod
    def _fmt_element(i: dict[str, Any], where: str) -> str:
        bits = [f'[{i["id"]}] {i["role"]} "{i["name"]}"']
        if i.get("popup"): bits.append("[popup]")
        if "type" in i and i["type"] not in (i["role"], "textbox", "text"): bits.append(f"type={i['type']}")
        if "value" in i and i["role"] not in ("checkbox", "radio"): bits.append(f'value="{i["value"]}"')
        if "checked" in i: bits.append(f"checked={str(i['checked']).lower()}")
        if i.get("required"): bits.append("required")
        if i.get("disabled"): bits.append("disabled")
        if i.get("readonly"): bits.append("readonly")
        for k in ("placeholder", "maxlength", "pattern", "min", "max"):
            if i.get(k) not in (None, ""): bits.append(f"{k}={i[k]}")
        if i.get("options"): bits.append("options=[" + ", ".join(i["options"]) + "]")
        if i.get("href"): bits.append(f"href={i['href']}")
        return "  " + " ".join(bits) + where

    def describe(self, ref: ElementRef) -> Optional[dict[str, Any]]:
        """A target for the element that REALLY resolves to exactly this element in Playwright (for script export).
        Candidates are tried in the recorder's priority order; the first that matches one element, and the tagged one, wins;
        if several match, the first candidate gets an n-th index."""
        from framework.locators.semantic import build_semantic_locator
        from framework.models import Target

        try:
            handle = ref.frame.locator(f'[data-tb-agent="{ref.id}"]').first.element_handle(timeout=2000)
            candidates = handle.evaluate(DESCRIBE_JS) if handle else []
        except Exception:  # noqa: BLE001
            return None
        fallback: Optional[dict[str, Any]] = None
        for cand in candidates:
            target = dict(cand)
            if ref.frame_css:
                target["frame"] = ref.frame_css
            try:
                loc = build_semantic_locator(self.page, Target(**target))
                n = loc.count()
                if n == 1 and loc.first.get_attribute("data-tb-agent") == ref.id:
                    return target
                if n > 1 and fallback is None:
                    for k in range(n):
                        if loc.nth(k).get_attribute("data-tb-agent") == ref.id:
                            fallback = {**target, "nth": k + 1}
                            break
            except Exception:  # noqa: BLE001 - an invalid selector candidate
                continue
        return fallback
