"use strict";
/* Core helpers: DOM builder (never innerHTML -- file content can contain anything), API client, router, dialogs. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  const add = (kid) => {
    if (Array.isArray(kid)) kid.forEach(add);
    else if (kid === null || kid === undefined || kid === false) return;
    else el.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  };
  kids.forEach(add);
  return el;
}
// Element.append() with arrays / null would print "[object HTMLDivElement]" or "null": flatten and skip empty values everywhere.
const _nativeAppend = Element.prototype.append;
Element.prototype.append = function (...kids) {
  return _nativeAppend.apply(this, kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
};
const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
const fmtTime = (t) => { if (!t) return ""; const d = typeof t === "number" ? new Date(t * 1000) : new Date(t); return isNaN(d) ? String(t) : d.toLocaleString(); };
const deep = (o) => JSON.parse(JSON.stringify(o));

/* ---- API ---- */
class ApiError extends Error { constructor(msg, status, data) { super(msg); this.status = status; this.data = data; } }
async function api(method, path, body) {
  const opts = { method, headers: { "X-Testbot": "1" } };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  let data = null; try { data = await res.json(); } catch (e) { /* not json */ }
  if (!res.ok) throw new ApiError((data && data.error) || res.statusText || "request failed", res.status, data);
  return data;
}
const GET = (p) => api("GET", p), POST = (p, b) => api("POST", p, b || {}), PUT = (p, b) => api("PUT", p, b), DEL = (p) => api("DELETE", p);
const q = (o) => Object.entries(o).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");

/* ---- toasts & dialogs ---- */
function toast(msg, kind = "") {
  const t = h("div", { class: "toast " + kind }, msg);
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), kind === "bad" ? 9000 : 3800);
}
const fail = (e) => toast(e && e.message ? e.message : String(e), "bad");

function modal({ title, body, buttons = [], wide = false, onclose }) {
  const root = $("#modal-root");
  const close = () => { overlay.remove(); if (onclose) onclose(); };
  const foot = h("footer", {}, buttons.map((b) => h("button", {
    class: b.primary ? "primary" : (b.danger ? "danger" : ""),
    onclick: async () => { const keep = b.onclick ? await b.onclick(close) : undefined; if (keep !== false && !b.stay) close(); },
  }, b.label)));
  const overlay = h("div", { class: "overlay" }, h("div", { class: "modal" + (wide ? " wide" : "") },
    h("header", {}, h("span", { style: { flex: 1 } }, title), h("button", { class: "ghost icon", onclick: close }, "✕")),
    h("div", { class: "mbody" }, body), buttons.length ? foot : null));
  root.appendChild(overlay);
  return { close, overlay };
}
function confirmBox(title, message, okLabel = "OK", danger = false) {
  return new Promise((resolve) => {
    modal({ title, body: h("p", {}, message), onclose: () => resolve(false),
      buttons: [{ label: "Cancel", onclick: () => resolve(false) }, { label: okLabel, primary: !danger, danger, onclick: () => resolve(true) }] });
  });
}
function promptBox(title, label, value = "", hint = "") {
  return new Promise((resolve) => {
    const inp = h("input", { value, style: { width: "100%" } });
    const m = modal({ title, body: h("div", { class: "field" }, h("label", {}, label), inp, hint ? h("div", { class: "hint" }, hint) : null),
      onclose: () => resolve(null),
      buttons: [{ label: "Cancel", onclick: () => resolve(null) }, { label: "OK", primary: true, onclick: () => resolve(inp.value.trim() || null) }] });
    inp.focus(); inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { resolve(inp.value.trim() || null); m.close(); } });
  });
}

/* ---- form helpers ---- */
function field(label, control, hint) { return h("div", { class: "field" }, h("label", {}, label), control, hint ? h("div", { class: "hint" }, hint) : null); }
function textInput(obj, key, { onchange, placeholder, type = "text", list } = {}) {
  const el = h("input", { type, value: obj[key] ?? "", placeholder, list, oninput: () => { obj[key] = el.value; if (onchange) onchange(el.value); } });
  return el;
}
function areaInput(obj, key, { rows = 3, onchange, placeholder } = {}) {
  const el = h("textarea", { rows, placeholder, style: { fontFamily: "inherit" }, oninput: () => { obj[key] = el.value; if (onchange) onchange(); } }, obj[key] ?? "");
  return el;
}
function linesInput(obj, key, { rows = 3, onchange, placeholder } = {}) {   // one item per line <-> array
  const el = h("textarea", { rows, placeholder, style: { fontFamily: "inherit" }, oninput: () => { obj[key] = el.value.split("\n").map((x) => x.trim()).filter(Boolean); if (onchange) onchange(); } }, (obj[key] || []).join("\n"));
  return el;
}
function numInput(obj, key, { onchange, placeholder } = {}) {
  const el = h("input", { type: "number", value: obj[key] ?? "", placeholder, oninput: () => {
    if (el.value === "") delete obj[key]; else obj[key] = Number(el.value); if (onchange) onchange(); } });
  return el;
}
function selectInput(options, value, onchange, { blank } = {}) {
  const el = h("select", { onchange: () => onchange(el.value) },
    blank !== undefined ? h("option", { value: "" }, blank) : null,
    options.map((o) => { const [v, l] = Array.isArray(o) ? o : [o, o]; return h("option", { value: v, selected: v === value }, l); }));
  if (value !== undefined && value !== null) el.value = value;
  return el;
}
function checkInput(obj, key, label, onchange) {
  const el = h("input", { type: "checkbox", checked: !!obj[key], onchange: () => { obj[key] = el.checked; if (onchange) onchange(); } });
  return h("label", { style: { display: "inline-flex", gap: "6px", alignItems: "center" } }, el, label);
}
function table(headers, rows, opts = {}) {
  return h("table", { class: opts.class || "" }, h("thead", {}, h("tr", {}, headers.map((x) => h("th", {}, x)))), h("tbody", {}, rows));
}
const badge = (text, kind = "") => h("span", { class: "badge " + kind }, text);
const statusBadge = (s) => badge(s, s === "pass" ? "ok" : (s === "fail" ? "bad" : (["error", "empty", "inconclusive"].includes(s) ? "warn" : "")));
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function downloadText(name, text) { const a = h("a", { href: URL.createObjectURL(new Blob([text])), download: name }); document.body.appendChild(a); a.click(); a.remove(); }
function readFileB64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader(); r.onerror = reject;
    r.onload = () => resolve(String(r.result).split(",")[1] || ""); r.readAsDataURL(file);
  });
}

/* ---- app state, router ---- */
const S = { catalog: null, app: null, dirty: false };
const routes = {};
const NAV = [["tests", "Test cases"], ["import", "Import"], ["sessions", "Sessions"], ["schedules", "Schedules"], ["results", "Results"],
             ["environments", "Environments & SQL"], ["ai", "AI assistant"], ["settings", "Settings"]];

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [name, qs] = raw.split("?");
  const params = {}; (qs || "").split("&").filter(Boolean).forEach((p) => { const [k, v] = p.split("="); params[decodeURIComponent(k)] = decodeURIComponent(v || ""); });
  return [name || "tests", params];
}
async function navigate() {
  const [name, params] = parseHash();
  const view = routes[name] || routes.tests;
  $$("#navlinks a").forEach((a) => a.classList.toggle("active", a.dataset.route === (name === "edit" ? "tests" : name)));
  const main = clear($("#main"));
  try { await view(main, params); } catch (e) { main.appendChild(h("div", { class: "banner bad" }, "Error: " + e.message)); console.error(e); }
}
function go(name, params) { location.hash = "#/" + name + (params ? "?" + q(params) : ""); }

let lastHash = location.hash;
window.addEventListener("hashchange", async () => {
  if (S.revert) { S.revert = false; return; }               // we put the old address back ourselves: nothing to do
  if (S.dirty) {
    const leave = await confirmBox("Unsaved changes", "You have unsaved changes in the editor. Leave without saving?", "Leave", true);
    if (!leave) { S.revert = true; location.hash = lastHash; return; }
    S.dirty = false;
  }
  lastHash = location.hash;
  navigate();
});
window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });

async function refreshPills() {
  const st = S.app = await GET("/api/state");
  const pill = (ok, text) => h("div", {}, h("span", { class: "dot " + (ok ? "ok" : "bad") }), text);
  clear($("#pills")).append(
    pill(!!st.runner, st.runner ? "Test runner found" : "Runner not found"),
    pill(st.scheduler.running, st.scheduler.running ? "Scheduler running" : "Scheduler not running"),
    pill(st.ai.configured, st.ai.configured ? "AI ready (" + st.ai.provider + ")" : "AI not configured"));
}

function showAbout() {
  const a = S.app || {};
  modal({ title: "About testbot", body: h("div", {},
    h("img", { src: a.logo, style: { height: "44px", background: a.logo_bg, padding: "6px 8px", borderRadius: "4px", display: "block", marginBottom: "8px" } }), h("div", { style: { fontSize: "20px", fontWeight: 600 } }, "testbot manager"), h("div", { class: "muted" }, "Version " + (a.version || "")),
    h("p", {}, h("b", {}, a.copyright || "")),
    h("div", { class: "kv" }, h("div", {}, "Workspace"), h("div", { class: "mono small" }, a.workspace_file || ""), h("div", {}, "Test runner"), h("div", { class: "mono small" }, a.runner ? a.runner.join(" ") : "not found"),
      h("div", {}, "Scheduler"), h("div", {}, a.scheduler && a.scheduler.running ? "running" : "not running")),
    h("p", { class: "muted small", style: { marginTop: "12px" } }, "The IACF mark is also stamped on the screenshots testbot saves as test evidence (switch off with TESTBOT_WATERMARK=off).")), buttons: [{ label: "Close" }] });
}

async function start() {
  clear($("#navlinks")).append(...NAV.map(([r, label]) => h("a", { href: "#/" + r, "data-route": r }, label)));
  try {
    S.catalog = await GET("/api/catalog");
    await refreshPills();
    clear($("#brandfoot")).append(h("div", { onclick: showAbout, title: "About" }, h("img", { src: S.app.logo, style: { height: "22px", background: S.app.logo_bg, padding: "3px 5px", borderRadius: "3px", verticalAlign: "middle", marginRight: "6px" } }), " v" + S.app.version, h("br"), S.app.copyright));
  } catch (e) { $("#main").textContent = "Cannot reach the manager: " + e.message; return; }
  setInterval(() => refreshPills().catch(() => {}), 20000);
  navigate();
}
