"""Page script injected into every frame of the recorded browser. It reports user actions
to Python through the `__tbRecord` binding, each with the best *unique* target it can find
(same priorities the runner resolves: testid > label/placeholder > role+name > text > css).

Uniqueness is checked in-page with substring, case-insensitive matching (Playwright's default
for get_by_role/label/text), so a candidate is only used when it can't hit a second element."""

INJECTED_JS = r"""
(() => {
  if (window.__tbInstalled) return;
  window.__tbInstalled = true;

  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const lc = s => norm(s).toLowerCase();
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const send = ev => { try { ev.url = location.href; window.__tbRecord(ev); } catch (_) {} };

  const IMPLICIT = { button: 'button', a: 'link', select: 'combobox', textarea: 'textbox', summary: 'button',
                     h1: 'heading', h2: 'heading', h3: 'heading', h4: 'heading', h5: 'heading', h6: 'heading',
                     img: 'img', li: 'listitem', tr: 'row', td: 'cell', th: 'columnheader' };
  function roleOf(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit.split(' ')[0];
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return el.hasAttribute('href') ? 'link' : null;
    if (tag === 'input') {
      const t = (el.type || 'text').toLowerCase();
      if (['button', 'submit', 'reset', 'image'].includes(t)) return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'search') return 'searchbox';
      if (['text', 'email', 'tel', 'url', ''].includes(t)) return 'textbox';
      return null;
    }
    return IMPLICIT[tag] || null;
  }

  function nameOf(el) {
    const al = el.getAttribute('aria-label');
    if (al) return norm(al);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const t = lb.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean).map(n => n.textContent).join(' ');
      if (norm(t)) return norm(t);
    }
    if (el.tagName === 'INPUT' && ['button', 'submit', 'reset'].includes((el.type || '').toLowerCase())) return norm(el.value);
    if (el.tagName === 'IMG') return norm(el.alt);
    if (el.labels && el.labels.length) return norm(el.labels[0].textContent);
    return norm(el.innerText || el.textContent) || norm(el.title);
  }

  const q = sel => { try { return Array.from(document.querySelectorAll(sel)); } catch (_) { return []; } };
  const CANDIDATE_SEL = 'a,button,input,select,textarea,summary,h1,h2,h3,h4,h5,h6,img,li,tr,td,th,[role]';

  function labelText(el) {
    if (el.labels && el.labels.length) return norm(el.labels[0].textContent);
    return norm(el.getAttribute('aria-label'));
  }
  function countLabel(text) {
    const t = lc(text);
    return q('input,select,textarea,[aria-label]').filter(e => lc(labelText(e)).includes(t)).length;
  }
  function countRoleName(role, name) {
    const n = lc(name);
    return q(CANDIDATE_SEL).filter(e => roleOf(e) === role && lc(nameOf(e)).includes(n)).length;
  }
  function countText(text) {
    const t = lc(text);
    return q('body *').filter(e => {
      if (['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(e.tagName)) return false;
      if (!lc(e.textContent).includes(t)) return false;
      return !Array.from(e.children).some(c => lc(c.textContent).includes(t));  // smallest containing element
    }).length;
  }

  const stableId = id => id && !/\d{4,}|^:r|^ember|^react|^radix|^headlessui/i.test(id);
  function cssPath(el) {
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 6) {
      if (stableId(el.id) && q('#' + CSS.escape(el.id)).length === 1) { parts.unshift('#' + CSS.escape(el.id)); break; }
      let part = el.tagName.toLowerCase();
      const parent = el.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter(c => c.tagName === el.tagName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
      }
      parts.unshift(part);
      if (el.tagName === 'BODY') break;
      el = parent;
    }
    return parts.join(' > ');
  }

  function describe(el) {
    const tid = el.getAttribute('data-testid');
    if (tid && q('[data-testid="' + CSS.escape(tid) + '"]').length === 1) return { strategy: 'testid', value: tid };

    const tag = el.tagName.toLowerCase();
    const isField = ['input', 'select', 'textarea'].includes(tag) && !['button', 'submit', 'reset', 'image', 'checkbox', 'radio'].includes((el.type || '').toLowerCase());
    if (isField) {
      const lab = labelText(el);
      if (lab && lab.length <= 80 && countLabel(lab) === 1) return { strategy: 'label', value: lab };
      const ph = el.getAttribute('placeholder');
      if (ph && q('[placeholder]').filter(e => lc(e.getAttribute('placeholder')).includes(lc(ph))).length === 1)
        return { strategy: 'placeholder', value: ph };
    }
    const role = roleOf(el);
    const name = nameOf(el);
    if (role && name && name.length <= 80 && countRoleName(role, name) === 1) return { strategy: 'role', value: role, name };
    if (name && name.length <= 60 && !isField && countText(name) === 1 && !['input', 'select', 'textarea'].includes(tag))
      return { strategy: 'text', value: name };

    for (const attr of ['name', 'data-test', 'data-qa', 'data-cy']) {
      const v = el.getAttribute(attr);
      if (v) { const sel = tag + '[' + attr + '="' + CSS.escape(v) + '"]'; if (q(sel).length === 1) return { strategy: 'css', value: sel }; }
    }
    if (stableId(el.id) && q('#' + CSS.escape(el.id)).length === 1) return { strategy: 'css', value: '#' + CSS.escape(el.id) };
    return { strategy: 'css', value: cssPath(el) };
  }

  const CLICKABLE = 'a,button,input,select,textarea,summary,label,[role=button],[role=link],[role=menuitem],[role=tab],[role=checkbox],[role=radio],[role=option],[role=switch],[onclick]';
  const TEXT_TYPES = ['text', 'email', 'password', 'search', 'tel', 'url', 'number', 'date', 'time', 'datetime-local', 'month', 'week', 'color', 'range', ''];
  const isTextInput = el => el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && TEXT_TYPES.includes((el.type || 'text').toLowerCase()));

  const lastValue = new WeakMap();
  function flushValue(el) {
    if (!isTextInput(el)) return;
    const v = el.value;
    if (lastValue.get(el) === v || (lastValue.get(el) === undefined && v === '')) return;
    lastValue.set(el, v);
    send({ type: 'type', target: describe(el), value: v, isPassword: (el.type || '').toLowerCase() === 'password' });
  }

  document.addEventListener('click', e => {
    if (!e.isTrusted) return;
    let el = e.target.closest ? e.target.closest(CLICKABLE) : null;
    if (!el) el = e.target.nodeType === 1 ? e.target : e.target.parentElement;
    if (!el) return;
    if (el.tagName === 'LABEL' && el.control) el = el.control;
    if (el.tagName === 'SELECT' || isTextInput(el)) return;   // focusing a field; the value change is recorded instead
    send({ type: 'click', target: describe(el) });
  }, true);

  document.addEventListener('change', e => {
    const el = e.target;
    if (!el || !el.tagName) return;
    if (el.tagName === 'SELECT' && e.isTrusted) {
      const opt = el.options[el.selectedIndex];
      send({ type: 'select', target: describe(el), value: opt ? (opt.value || norm(opt.text)) : '', label: opt ? norm(opt.text) : '' });
    } else if (el.type === 'file') {
      send({ type: 'upload', target: describe(el) });
    } else if (e.isTrusted) {
      flushValue(el);
    }
  }, true);

  document.addEventListener('keydown', e => {
    if (!e.isTrusted) return;
    const el = e.target;
    if (!el || !el.tagName) return;
    if (e.key === 'Enter' && el.tagName !== 'TEXTAREA') {
      if (!isTextInput(el)) return;   // Enter on a button/link fires a click, recorded separately
      flushValue(el);
      send({ type: 'press', target: describe(el), key: 'Enter' });
    } else if (e.key === 'Escape' && !['BODY', 'HTML'].includes(el.tagName)) {
      send({ type: 'press', target: describe(el), key: 'Escape' });
    }
  }, true);
})();
"""
