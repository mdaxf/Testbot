"use strict";
/* Test case list + editor (suite settings, cases, steps). */

/* ------------------------------------------------------------------ list */
routes.tests = async (main) => {
  const [{ tests }, st] = await Promise.all([GET("/api/tests"), GET("/api/state")]);
  let filter = "";
  const tbl = table(["File", "Suite", "Format", "Cases", "Steps", "Modified", ""], []), body = tbl.querySelector("tbody");
  const draw = () => {
    clear(body);
    const rows = tests.filter((t) => !filter || (t.path + " " + (t.suite_name || "") + " " + (t.suite_id || "")).toLowerCase().includes(filter));
    if (!rows.length) body.appendChild(h("tr", {}, h("td", { colspan: 7, class: "empty" }, tests.length ? "No test case matches." : "No test cases in this folder yet. Create one or import a file.")));
    rows.forEach((t) => {
      const ok = ["testbot", "testbot-excel"].includes(t.format);
      body.appendChild(h("tr", { class: "clickable", onclick: () => ok ? go("edit", { path: t.path }) : go("import", { path: t.path }) },
        h("td", {}, h("b", {}, t.name), t.folder ? h("div", { class: "small muted" }, t.folder) : null),
        h("td", {}, t.suite_id || "", h("div", { class: "small muted" }, t.suite_name || "")),
        h("td", {}, ok ? badge(t.format === "testbot" ? "JSON" : "Excel", "ok") : (t.format === "apriso" ? badge("Apriso – import", "warn") : badge("Needs import", "warn"))),
        h("td", {}, t.cases), h("td", {}, t.steps), h("td", { class: "small" }, fmtTime(t.modified)),
        h("td", { onclick: (e) => e.stopPropagation(), style: { whiteSpace: "nowrap" } },
          ok ? h("button", { class: "icon", title: "Run now", onclick: () => runDialog(t.path, "suite", "") }, "▶") : null,
          ok ? h("button", { class: "icon", title: "Duplicate", onclick: () => duplicate(t) }, "⧉") : null,
          h("button", { class: "icon danger", title: "Delete", onclick: () => remove(t) }, "🗑"))));
    });
  };
  const reload = () => go("tests", { r: Date.now() });
  const duplicate = async (t) => {
    const to = await promptBox("Duplicate", "New file name", t.path.replace(/\.(json|xlsx|xlsm)$/i, "") + "-copy.json"); if (!to) return;
    try { await POST("/api/test/duplicate", { path: t.path, new_path: to }); toast("Duplicated", "ok"); reload(); } catch (e) { fail(e); }
  };
  const remove = async (t) => {
    if (!(await confirmBox("Delete test case", `Delete ${t.path}? It is moved to a recoverable backup folder.`, "Delete", true))) return;
    try { await DEL("/api/test?" + q({ path: t.path })); toast("Deleted", "ok"); reload(); } catch (e) { fail(e); }
  };
  main.append(
    h("h1", {}, "Test cases"),
    h("div", { class: "toolbar" },
      h("button", { class: "primary", onclick: () => newTestDialog() }, "+ New test case"),
      h("button", { onclick: () => go("import") }, "Import…"),
      h("button", { onclick: reload }, "Refresh"),
      h("input", { placeholder: "Search…", oninput: (e) => { filter = e.target.value.toLowerCase(); draw(); } }),
      h("span", { class: "grow" }),
      h("span", { class: "muted small" }, "Folder: ", h("span", { class: "mono" }, st.dirs.test_cases.path), " ", h("a", { href: "#/settings" }, "change"))),
    h("div", { class: "card", style: { padding: 0 } }, tbl));
  draw();
};

function newTestDialog() {
  const f = { path: "new-test", suite_id: "SUITE-1", name: "New test suite", base_url: "" };
  modal({ title: "New test case", body: h("div", { class: "grid g2" },
    field("File name (may include a sub-folder)", textInput(f, "path"), "e.g. login/basic – saved as .json"),
    field("Suite ID", textInput(f, "suite_id")), field("Suite name", textInput(f, "name")),
    field("Base URL (optional)", textInput(f, "base_url", { placeholder: "http://server/app" }))),
    buttons: [{ label: "Cancel" }, { label: "Create", primary: true, onclick: async () => {
      try { const r = await POST("/api/test", f); go("edit", { path: r.path }); } catch (e) { fail(e); return false; } } }] });
}

/* ------------------------------------------------------------------ run dialog (used by list, editor, schedules) */
function runDialog(path, type, env, options) {
  const log = h("pre", { class: "log" }, "Starting…"), status = h("div", { class: "muted" }, "Starting…"), links = h("div", {});
  let stop = false;
  const m = modal({ title: "Run: " + path, wide: true, body: h("div", {}, status, log, links), onclose: () => { stop = true; },
    buttons: [{ label: "Close" }] });
  (async () => {
    try {
      const { run } = await POST("/api/run", { path, type, env, options: options || {} });
      while (!stop) {
        const r = await GET("/api/run/status?" + q({ id: run }));
        log.textContent = r.log || "(waiting for output…)"; log.scrollTop = log.scrollHeight;
        const items = (r.record.items || []).map((i) => `${i.type} ${i.path}: ${i.status}${i.exit_code !== undefined && i.exit_code !== null ? " (exit " + i.exit_code + ")" : ""}`).join(" · ");
        status.textContent = r.running ? "Running… " + items : "Finished: " + r.record.status + " " + items;
        if (!r.running) {
          if (r.record.dir) links.append(h("a", { href: "#/results" }, "Open the Results page"), " – results folder: ", h("span", { class: "mono" }, r.record.dir));
          refreshPills(); break;
        }
        await new Promise((res) => setTimeout(res, 1200));
      }
    } catch (e) { status.textContent = "Could not run: " + e.message; status.className = "fail"; }
  })();
  return m;
}

/* ------------------------------------------------------------------ editor */
let E = null;   // the open suite

routes.edit = async (main, { path }) => {
  if (!path) return go("tests");
  const data = await GET("/api/test?" + q({ path }));
  E = { path: data.path, saveAs: data.save_as, mtime: data.mtime, source: data.source, suite: data.suite, tab: "steps", ci: 0, si: null, problems: [], open: {} };
  S.dirty = false;
  const envs = (await GET("/api/environments")).environments; E.envs = Object.keys(envs);
  E.box = main;
  drawEditor();
  runValidate();
};

const mark = () => { if (!S.dirty) { S.dirty = true; updateHeader(); } scheduleValidate(); };
const scheduleValidate = debounce(() => runValidate(), 700);
async function runValidate() {
  if (!E) return;
  try { E.problems = (await POST("/api/validate", { suite: cleanSuite(E.suite) })).problems; } catch (e) { return; }
  if (E.tab === "steps") decorateProblems();
  const c = $("#problem-count"); if (c) { const n = E.problems.filter((p) => p.level === "error").length, w = E.problems.length - n;
    clear(c).append(n ? badge(n + " error" + (n > 1 ? "s" : ""), "bad") : badge("valid", "ok"), " ", w ? badge(w + " warning" + (w > 1 ? "s" : ""), "warn") : ""); }
}

function updateHeader() {
  const el = $("#ed-dirty"); if (el) el.textContent = S.dirty ? "● unsaved changes" : "";
}

/* remove empty strings/objects so saved files stay tidy */
function cleanSuite(suite) {
  const s = deep(suite);
  const prune = (o) => {
    for (const k of Object.keys(o)) {
      const v = o[k];
      if (v === "" || v === null || v === undefined) delete o[k];
      else if (typeof v === "object" && !Array.isArray(v)) { prune(v); if (!Object.keys(v).length && !["variables", "config", "match", "css"].includes(k)) delete o[k]; }
    }
  };
  (s.cases || []).forEach((c) => {
    (c.steps || []).forEach((st) => {
      if (st.target && !st.target.value && !st.target.strategy) delete st.target;
      if (st.target && st.target.value === "" ) delete st.target;
      if (st.expected && (!st.expected.type || st.expected.type === "none")) delete st.expected;
      if (st.capture && !st.capture.var) delete st.capture;
      const inputKept = st.input;
      prune(st);
      if (inputKept !== undefined && inputKept !== "" && st.input === undefined) st.input = inputKept;
    });
    prune({ ...c, steps: undefined });
    ["area_path", "preconditions", "device", "objective", "data_hints", "start_url", "mode"].forEach((k) => { if (c[k] === "" || c[k] === undefined) delete c[k]; });
    ["expect", "constraints"].forEach((k) => { if (Array.isArray(c[k]) && !c[k].length) delete c[k]; });
    if (c.viewport && !(c.viewport.width && c.viewport.height)) delete c.viewport;
    if (c.variables && !Object.keys(c.variables).length) delete c.variables;
  });
  ["base_url", "environment", "device", "mode"].forEach((k) => { if (!s[k]) delete s[k]; });
  ["variables", "connections"].forEach((k) => { if (s[k] && !Object.keys(s[k]).length) delete s[k]; });
  if (s.viewport && !(s.viewport.width && s.viewport.height)) delete s.viewport;
  ["workers", "iterations", "duration_s", "ramp_up_s", "default_step_delay_ms"].forEach((k) => { if (!s[k]) delete s[k]; });
  return s;
}

async function saveSuite({ as } = {}) {
  const target = as || E.saveAs || E.path;
  try {
    const r = await PUT("/api/test", { path: target, suite: cleanSuite(E.suite), base_mtime: target === E.path ? E.mtime : undefined, overwrite: as ? false : true });
    E.mtime = r.mtime; E.path = r.path; E.saveAs = r.path; E.source = "json"; S.dirty = false; updateHeader();
    const t = $("#ed-title"); if (t) t.textContent = r.path;
    toast("Saved", "ok"); return true;
  } catch (e) {
    if (e.status === 409 && !as) {
      modal({ title: "The file changed on disk", body: h("p", {}, e.message), buttons: [
        { label: "Cancel" },
        { label: "Save as another file…", onclick: async () => { const n = await promptBox("Save as", "File name", E.path.replace(/\.json$/i, "") + "-mine.json"); if (n) await saveSuite({ as: n }); } },
        { label: "Overwrite", danger: true, onclick: async () => { E.mtime = undefined; const r = await PUT("/api/test", { path: target, suite: cleanSuite(E.suite), overwrite: true }); E.mtime = r.mtime; S.dirty = false; updateHeader(); toast("Saved", "ok"); } }] });
      return false;
    }
    fail(e); return false;
  }
}

function drawEditor() {
  const main = clear(E.box);
  const tabs = [["steps", "Cases & steps"], ["suite", "Suite settings"], ["vars", "Variables & SQL"], ["json", "JSON"], ["ai", "AI"]];
  main.append(
    h("div", { class: "toolbar" },
      h("a", { href: "#/tests" }, "← Test cases"), h("h1", { id: "ed-title", style: { margin: 0 } }, E.path),
      E.source === "excel" ? badge("from Excel – saves as " + E.saveAs, "warn") : null, h("span", { id: "ed-dirty", class: "small", style: { color: "var(--warn)" } }),
      h("span", { class: "grow" }), h("span", { id: "problem-count" }),
      h("button", { onclick: async () => { await runValidate(); toast(E.problems.length ? "See the highlighted steps" : "No problems found", E.problems.length ? "" : "ok"); } }, "Validate"),
      h("button", { onclick: () => saveSuite({}).then((ok) => ok && runDialog(E.path, "suite", E.suite.environment || "")) }, "Save & run ▶"),
      h("button", { class: "primary", id: "btn-save", onclick: () => saveSuite({}) }, "Save"),
      h("button", { onclick: async () => { const n = await promptBox("Save as", "New file name", E.path.replace(/\.json$/i, "") + "-copy.json"); if (n) saveSuite({ as: n }); } }, "Save as…")),
    h("div", { class: "tabs" }, tabs.map(([k, l]) => h("button", { class: E.tab === k ? "active" : "", onclick: () => { E.tab = k; drawEditor(); runValidate(); } }, l))),
    h("div", { id: "ed-body" }));
  const body = $("#ed-body");
  ({ steps: stepsTab, suite: suiteTab, vars: (b) => varsTab(E, b, mark), json: jsonTab, ai: (b) => aiEditorTab(E, b, mark, drawEditor) })[E.tab](body);
  updateHeader();
}
document.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "s" && E && location.hash.startsWith("#/edit")) { e.preventDefault(); saveSuite({}); } });

/* ---- suite settings tab ---- */
function suiteTab(body) {
  const s = E.suite;
  s.viewport = s.viewport || {};
  body.append(h("div", { class: "card" },
    h("div", { class: "grid g3" },
      field("Suite ID", textInput(s, "suite_id", { onchange: mark })), field("Suite name", textInput(s, "suite_name", { onchange: mark })),
      field("Base URL", textInput(s, "base_url", { onchange: mark, placeholder: "http://server/app" }), "Becomes {base_url} in every step."),
      field("Environment (optional fallback)", selectInput(E.envs, s.environment || "", (v) => { s.environment = v; mark(); }, { blank: "(none)" }), "A block of config/environments.yaml; suite values win."),
      field("On case failure", selectInput(S.catalog.on_case_fail, s.on_case_fail || "continue", (v) => { s.on_case_fail = v; mark(); })),
      field("Pause after every step (ms)", numInput(s, "default_step_delay_ms", { onchange: mark })),
      field("Mode", selectInput(S.catalog.modes, s.mode || "", (v) => { s.mode = v; mark(); }, { blank: "(default: script)" }), "script = run the steps · agentic = the AI agent runs natural-language cases · auto = script when there is one, agent otherwise. TESTBOT_MODE / --mode override."),
      field("Device", textInput(s, "device", { onchange: mark, list: "devices" }), "A Playwright device name, e.g. iPhone 13."),
      field("Window width", numInput(s.viewport, "width", { onchange: mark })), field("Window height", numInput(s.viewport, "height", { onchange: mark }))),
    h("datalist", { id: "devices" }, S.catalog.devices.map((d) => h("option", { value: d })))),
    h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Load test (optional)"),
      h("p", { class: "muted small" }, "Leave empty for a normal run. With workers > 1 every worker runs the whole suite on its own (see the manual, section 17)."),
      h("div", { class: "grid g4" }, field("Workers", numInput(s, "workers", { onchange: mark })), field("Iterations per worker", numInput(s, "iterations", { onchange: mark })),
        field("Duration (seconds)", numInput(s, "duration_s", { onchange: mark })), field("Ramp-up (seconds)", numInput(s, "ramp_up_s", { onchange: mark })))));
}

/* ---- JSON tab ---- */
function jsonTab(body) {
  const ta = h("textarea", { rows: 28, spellcheck: false }, JSON.stringify(cleanSuite(E.suite), null, 2));
  const msg = h("span", { class: "muted small" }, "Edit the file text directly, then Apply.");
  body.append(h("div", { class: "toolbar" }, h("button", { class: "primary", onclick: () => {
    try { const v = JSON.parse(ta.value); if (!v || !Array.isArray(v.cases)) throw new Error("a suite needs a 'cases' list"); E.suite = v; E.ci = 0; E.si = null; mark(); msg.textContent = "Applied."; msg.className = "pass small"; }
    catch (e) { msg.textContent = "Not applied: " + e.message; msg.className = "fail small"; } } }, "Apply"),
    h("button", { onclick: () => downloadText(E.path.split("/").pop(), ta.value) }, "Download"), msg), ta);
}

/* ---- cases & steps tab ---- */
function newStep(action = "click") {
  const st = { step_no: 0, description: "New step", action };
  applyActionDefaults(st, null);
  return st;
}
function applyActionDefaults(st, prev) {
  const a = st.action;
  if (a === "navigate" && (!st.target || !st.target.value) && !st.input) st.target = { strategy: "url", value: "{base_url}/" };
  if (a === "wait_until" && !st.input) st.input = "hidden";
  if (a === "check" && !st.input) st.input = "true";
  if (a === "wait" && !st.input) st.input = "1000";
  if (S.catalog.actions[a].target === "required" && !st.target) st.target = { strategy: a === "click" ? "role" : "label", value: "" };
  if (S.catalog.actions[a].target === "none" && prev && S.catalog.actions[prev]?.target !== "none" && st.target && !st.target.value) delete st.target;
  if (S.catalog.actions[a].config && !st.config) st.config = deep(S.catalog.config_templates[a] || {});
}
const renumber = (c) => (c.steps || []).forEach((s, i) => (s.step_no = i + 1));
const curCase = () => E.suite.cases[E.ci];

function stepsTab(body) {
  const cases = E.suite.cases;
  if (!cases.length) cases.push({ id: "TC-001", title: "New test case", steps: [] });
  if (E.ci >= cases.length) E.ci = 0;
  const side = h("div", { class: "side" }), main = h("div", { class: "body" });
  const drawSide = () => {
    clear(side).append(h("div", { class: "toolbar" }, h("b", {}, "Test cases"), h("span", { class: "grow" }),
      h("button", { class: "icon", title: "Add test case", onclick: () => { const n = cases.length + 1; cases.push({ id: "TC-" + String(n).padStart(3, "0"), title: "New test case", steps: [] }); E.ci = cases.length - 1; E.si = null; mark(); drawEditor(); } }, "+")));
    cases.forEach((c, i) => side.append(h("div", { class: "list-item" + (i === E.ci ? " active" : ""), onclick: () => { E.ci = i; E.si = null; drawEditor(); } },
      h("b", {}, c.id || "(no id)"), h("div", { class: "small muted" }, c.title || ""), h("div", { class: "small muted" }, (c.steps || []).length + " steps"))));
    const c = curCase();
    side.append(h("div", { class: "toolbar", style: { marginTop: "8px" } },
      h("button", { class: "icon", title: "Duplicate", onclick: () => { const d = deep(c); d.id = c.id + "-copy"; cases.splice(E.ci + 1, 0, d); E.ci++; mark(); drawEditor(); } }, "⧉"),
      h("button", { class: "icon", title: "Move up", disabled: E.ci === 0, onclick: () => { [cases[E.ci - 1], cases[E.ci]] = [cases[E.ci], cases[E.ci - 1]]; E.ci--; mark(); drawEditor(); } }, "↑"),
      h("button", { class: "icon", title: "Move down", disabled: E.ci === cases.length - 1, onclick: () => { [cases[E.ci + 1], cases[E.ci]] = [cases[E.ci], cases[E.ci + 1]]; E.ci++; mark(); drawEditor(); } }, "↓"),
      h("button", { class: "icon danger", title: "Delete", onclick: async () => { if (await confirmBox("Delete test case", `Delete ${c.id} with its ${(c.steps || []).length} steps?`, "Delete", true)) { cases.splice(E.ci, 1); E.ci = 0; E.si = null; mark(); drawEditor(); } } }, "🗑")));
  };
  const c = curCase(); c.steps = c.steps || []; c.variables = c.variables || {}; c.viewport = c.viewport || {};
  const stepsBox = h("div", {});
  drawSide();
  main.append(
    h("div", { class: "card" }, h("div", { class: "grid g4" },
      field("Case ID", textInput(c, "id", { onchange: () => { mark(); drawSide(); } })), field("Title", textInput(c, "title", { onchange: () => { mark(); drawSide(); } })),
      field("Area path", textInput(c, "area_path", { onchange: mark })), field("Priority", numInput(c, "priority", { onchange: mark })),
      field("Preconditions", textInput(c, "preconditions", { onchange: mark })), field("Device (this case)", textInput(c, "device", { onchange: mark, list: "devices" })),
      field("Window width", numInput(c.viewport, "width", { onchange: mark })), field("Window height", numInput(c.viewport, "height", { onchange: mark })))),
    h("details", { class: "card", open: !!(c.objective || c.mode) }, h("summary", {}, h("b", {}, "Natural-language test (agentic mode)"), " ",
      h("span", { class: "muted small" }, "state the goal in words; the AI agent decides the data and what to check. Steps below are then optional.")),
      h("div", { class: "grid g2", style: { marginTop: "8px" } },
        field("Objective", areaInput(c, "objective", { rows: 3, onchange: mark, placeholder: "e.g. Add a production line with a unique number and confirm it appears in the list" })),
        field("Data hints (optional)", areaInput(c, "data_hints", { rows: 3, onchange: mark, placeholder: "e.g. line numbers start with TEST_ and must be unique" })),
        field("Expected outcomes (one per line)", linesInput(c, "expect", { rows: 3, onchange: mark, placeholder: "The new line is listed\nNo error message is shown" }), "Each one must be proven by a passing check for the test to pass."),
        field("Constraints (one per line)", linesInput(c, "constraints", { rows: 3, onchange: mark, placeholder: "Do not delete any existing line" })),
        field("Start address (optional)", textInput(c, "start_url", { onchange: mark, placeholder: "{base_url}/portal" })),
        field("Mode for this case", selectInput(S.catalog.modes, c.mode || "", (v) => { c.mode = v; mark(); }, { blank: "(suite / environment default)" }), "TESTBOT_MODE and --mode override this."))),
    h("div", { class: "toolbar" }, h("h2", { style: { margin: 0 } }, "Steps"), h("span", { class: "grow" }),
      h("button", { class: "primary", onclick: () => { c.steps.push(newStep()); renumber(c); E.si = c.steps.length - 1; mark(); drawSteps(); } }, "+ Add step")),
    stepsBox, h("div", { id: "problem-list", class: "problems" }));
  body.append(h("div", { class: "split" }, side, main));

  function drawSteps() {
    clear(stepsBox);
    const tbody = h("tbody", {});
    c.steps.forEach((st, i) => { tbody.appendChild(stepRow(c, st, i, drawSteps)); if (E.si === i) tbody.appendChild(h("tr", {}, h("td", { colspan: 8 }, detailForm(st, () => drawSteps(), mark)))); });
    if (!c.steps.length) tbody.appendChild(h("tr", {}, h("td", { colspan: 8, class: "empty" }, "No steps yet. Press “+ Add step”, or use the AI tab / the recorder.")));
    stepsBox.append(h("table", { class: "steps" }, h("thead", {}, h("tr", {}, ["#", "Action", "Description", "Target", "Input", "Check", ""].map((x, k) => h("th", { colspan: k === 6 ? 2 : 1 }, x)))), tbody));
    decorateProblems();
  }
  drawSteps();
}

function describeTarget(t) {
  if (!t || !t.value) return "";
  return `${t.strategy}: ${t.value}${t.name ? " [" + t.name + "]" : ""}${t.nth ? " #" + t.nth : ""}${t.row ? " r" + t.row + "c" + (t.col || "") : ""}`;
}
function describeCheck(e) { return e && e.type && e.type !== "none" ? e.type + (typeof e.value === "string" && e.value ? ": " + e.value : "") : ""; }

function stepRow(c, st, i, redraw) {
  const meta = S.catalog.actions[st.action] || {};
  const doMove = (from, to) => { if (to < 0 || to >= c.steps.length || from === to) return; const [x] = c.steps.splice(from, 1); c.steps.splice(to, 0, x); E.si = to; renumber(c); mark(); redraw(); };
  const tr = h("tr", { class: (E.si === i ? "selected" : ""), "data-i": i, draggable: false,
    ondragover: (e) => { if (E.drag !== undefined) { e.preventDefault(); tr.classList.add("dropline"); } }, ondragleave: () => tr.classList.remove("dropline"),
    ondrop: (e) => { e.preventDefault(); tr.classList.remove("dropline"); if (E.drag !== undefined) { const from = E.drag; E.drag = undefined; doMove(from, from < i ? i : i); } } },
    h("td", { class: "no" }, h("span", { class: "handle", title: "Drag to reorder", draggable: true,
      ondragstart: (e) => { E.drag = i; e.dataTransfer.setData("text/plain", String(i)); tr.classList.add("dragging"); }, ondragend: () => { E.drag = undefined; tr.classList.remove("dragging"); } }, "⋮⋮ "), i + 1),
    h("td", { class: "act" }, selectInput(Object.keys(S.catalog.actions), st.action, (v) => { const prev = st.action; st.action = v; applyActionDefaults(st, prev); mark(); redraw(); })),
    h("td", {}, textInput(st, "description", { onchange: mark })),
    h("td", { class: "small", style: { maxWidth: "240px", cursor: "pointer", wordBreak: "break-all" }, title: "Click to edit", onclick: () => { E.si = E.si === i ? null : i; redraw(); } },
      describeTarget(st.target) || h("span", { class: "muted" }, meta.target === "none" ? "–" : "(set target)")),
    h("td", {}, meta.input === null ? h("span", { class: "muted" }, "–") : textInput(st, "input", { onchange: mark, placeholder: meta.input || "" })),
    h("td", { class: "small", style: { maxWidth: "200px", cursor: "pointer" }, onclick: () => { E.si = E.si === i ? null : i; redraw(); } }, describeCheck(st.expected) || h("span", { class: "muted" }, "–")),
    h("td", { class: "btns" },
      h("button", { class: "icon", title: "Edit details", onclick: () => { E.si = E.si === i ? null : i; redraw(); } }, E.si === i ? "▾" : "▸"),
      h("button", { class: "icon", title: "Insert a step above", onclick: () => { c.steps.splice(i, 0, newStep()); renumber(c); E.si = i; mark(); redraw(); } }, "⤒+"),
      h("button", { class: "icon", title: "Insert a step below", onclick: () => { c.steps.splice(i + 1, 0, newStep()); renumber(c); E.si = i + 1; mark(); redraw(); } }, "+⤓"),
      h("button", { class: "icon", title: "Duplicate", onclick: () => { c.steps.splice(i + 1, 0, deep(st)); renumber(c); E.si = i + 1; mark(); redraw(); } }, "⧉"),
      h("button", { class: "icon", title: "Move up", disabled: i === 0, onclick: () => doMove(i, i - 1) }, "↑"),
      h("button", { class: "icon", title: "Move down", disabled: i === c.steps.length - 1, onclick: () => doMove(i, i + 1) }, "↓"),
      h("button", { class: "icon danger", title: "Delete", onclick: () => { c.steps.splice(i, 1); renumber(c); if (E.si === i) E.si = null; mark(); redraw(); } }, "✕")));
  return tr;
}

function decorateProblems() {
  const c = curCase(); if (!c) return;
  const rows = $$("table.steps tbody tr[data-i]");
  rows.forEach((r) => r.classList.remove("err", "wrn"));
  const list = $("#problem-list"); if (list) clear(list);
  E.problems.filter((p) => p.case === E.ci || p.case === undefined).forEach((p) => {
    if (p.step !== undefined) { const r = rows[p.step]; if (r) r.classList.add(p.level === "error" ? "err" : "wrn"); }
    if (list) list.append(h("div", { class: p.level, onclick: () => { if (p.step !== undefined) { E.si = p.step; drawEditor(); } } },
      (p.step !== undefined ? `Step ${p.step + 1}` : "Suite") + (p.field ? ` (${p.field})` : "") + ": " + p.message));
  });
}

/* ---- the detail form of one step ---- */
function targetEditor(owner, key, { optional = false, defaultStrategy = "css", onchange }) {
  const box = h("div", {});
  const draw = () => {
    clear(box);
    const t = owner[key];
    if (!t) { box.append(h("button", { onclick: () => { owner[key] = { strategy: defaultStrategy, value: "" }; onchange(); draw(); } }, "+ Set a target element")); return; }
    const strategies = Object.keys(S.catalog.strategies);
    box.append(h("div", { class: "grid g4" },
      field("Find by", selectInput(strategies, t.strategy, (v) => { t.strategy = v; onchange(); draw(); }), S.catalog.strategies[t.strategy] || ""),
      field(t.strategy === "table_cell" ? "Table (CSS)" : "Value", textInput(t, "value", { onchange })),
      t.strategy === "role" ? field("Accessible name", textInput(t, "name", { onchange })) : h("span"),
      h("span")));
    const adv = h("details", {}, h("summary", { class: "small muted" }, "More: n-th match, row/column, scope, iframe, pop-up"),
      h("div", { class: "grid g4", style: { marginTop: "6px" } },
        field("n-th match (1 = first)", numInput(t, "nth", { onchange })),
        t.strategy === "table_cell" ? field("Row", numInput(t, "row", { onchange })) : h("span"), t.strategy === "table_cell" ? field("Column", numInput(t, "col", { onchange })) : h("span"), h("span"),
        field("Search inside (CSS)", textInput(t, "scope", { onchange })), field("iframe (CSS)", textInput(t, "frame", { onchange })),
        field("Only inside pop-up (CSS)", textInput(t, "scope_if_present", { onchange, placeholder: ".apr-popup" })), h("span")));
    if (t.nth || t.scope || t.frame || t.scope_if_present || t.row || t.col) adv.open = true;
    box.append(adv);
    if (optional) box.append(h("button", { class: "ghost small", onclick: () => { delete owner[key]; onchange(); draw(); } }, "Remove target"));
  };
  draw();
  return box;
}

function inputEditor(st, onchange) {
  const meta = S.catalog.actions[st.action] || {};
  if (meta.input === null || meta.input === undefined) return null;
  if (st.action === "wait_until") {
    const cur = String(st.input || "hidden"); const m = cur.match(/^(hidden|visible|has_value|value:|text:|text_contains:)(.*)$/) || ["", "hidden", ""];
    let cond = m[1], arg = m[2];
    const argIn = h("input", { value: arg, placeholder: "expected text / value", oninput: () => { arg = argIn.value; sync(); } });
    const sync = () => { st.input = cond + (cond.endsWith(":") ? arg : ""); onchange(); };
    const sel = selectInput(S.catalog.wait_conditions, cond, (v) => { cond = v; argIn.style.display = v.endsWith(":") ? "" : "none"; sync(); });
    argIn.style.display = cond.endsWith(":") ? "" : "none";
    return field("Wait until the element is…", h("div", { style: { display: "flex", gap: "6px" } }, sel, argIn), "Timeout is set under Advanced.");
  }
  if (st.action === "check") return field("Checkbox state", selectInput(["true", "false"], String(st.input || "true"), (v) => { st.input = v; onchange(); }));
  if (st.action === "press") return field("Key", h("div", {}, textInput(st, "input", { onchange, list: "keys" }), h("datalist", { id: "keys" }, S.catalog.keys.map((k) => h("option", { value: k })))));
  return field("Input", textInput(st, "input", { onchange }), meta.input);
}

function configEditor(st, onchange) {
  const err = h("span", { class: "fail small" });
  const ta = h("textarea", { rows: 7, spellcheck: false, oninput: () => {
    try { st.config = ta.value.trim() ? JSON.parse(ta.value) : undefined; err.textContent = ""; ta.style.borderColor = ""; onchange(); }
    catch (e) { err.textContent = "Not valid JSON yet: " + e.message; ta.style.borderColor = "var(--bad)"; } } }, st.config ? JSON.stringify(st.config, null, 2) : "");
  const tpl = S.catalog.config_templates[st.action];
  return field("Config (JSON)", h("div", {}, ta, h("div", { class: "toolbar" }, tpl ? h("button", { onclick: () => { st.config = deep(tpl); ta.value = JSON.stringify(st.config, null, 2); onchange(); } }, "Insert template") : null, err)));
}

function expectedEditor(st, onchange, redraw) {
  const meta = S.catalog.actions[st.action] || {};
  const e = st.expected || (st.expected = { type: "none" });
  const info = S.catalog.expected[e.type] || {};
  const box = h("div", {}, h("div", { class: "grid g3" },
    field("Check type", selectInput(Object.keys(S.catalog.expected), e.type || "none", (v) => { e.type = v; if (v === "list_matches" && typeof e.value !== "object") e.value = [{ text: "" }]; onchange(); redraw(); }), info.help || "")));
  if (e.type && e.type !== "none") {
    if (info.list) {
      const err = h("span", { class: "fail small" });
      const ta = h("textarea", { rows: 6, spellcheck: false, oninput: () => { try { e.value = JSON.parse(ta.value); err.textContent = ""; onchange(); } catch (x) { err.textContent = "Not valid JSON yet"; } } }, JSON.stringify(e.value ?? [], null, 2));
      box.append(field("Rows (top N, in order) – JSON", h("div", {}, ta, err), 'Each row: {"text": "...", "background": "#ffcccc", "class": "hot"}.  Keys: text, text_contains, background, color, css, class, not_class.'));
      box.append(h("div", { class: "grid g3" }, field("Row selector (optional)", textInput(e, "rows", { onchange })), field("Text element inside row", textInput(e, "item", { onchange, placeholder: "td:nth-child(1)" })),
        field("Colour/class element", textInput(e, "style_item", { onchange, placeholder: "row" }))));
    } else if (info.value) box.append(h("div", { class: "grid g3" }, field("Expected value", textInput(e, "value", { onchange }))));
    if (info.property) box.append(h("div", { class: "grid g3" }, field("CSS property", textInput(e, "property", { onchange, placeholder: "background-color" }))));
    if (info.column) box.append(h("div", { class: "grid g3" }, field("Column (optional)", textInput(e, "column", { onchange }))));
    if (info.target) box.append(h("div", {}, h("div", { class: "small muted" }, "Element to check"), targetEditor(e, "target", { defaultStrategy: "css", onchange })));
  }
  return box;
}

function captureEditor(st, onchange) {
  const cap = st.capture || {};
  if (!st.capture && st.action !== "set_var") return h("button", { onclick: () => { st.capture = { var: "", from: "element_text" }; onchange(true); } }, "+ Capture a value into a variable");
  const c = st.capture || (st.capture = { var: "", from: "element_text" });
  const info = S.catalog.capture_from[c.from] || {};
  const box = h("div", {}, h("div", { class: "grid g4" },
    field("Variable name", textInput(c, "var", { onchange })),
    field("Take it from", selectInput(Object.keys(S.catalog.capture_from), c.from, (v) => { c.from = v; onchange(true); }), info.help || ""),
    info.column ? field("Column", textInput(c, "column", { onchange })) : h("span")));
  if (info.target) box.append(targetEditor(c, "target", { defaultStrategy: "css", onchange }));
  if (st.action !== "set_var") box.append(h("button", { class: "ghost small", onclick: () => { delete st.capture; onchange(true); } }, "Remove capture"));
  return box;
}

function detailForm(st, redraw, changed) {
  const meta = S.catalog.actions[st.action] || {};
  const box = h("div", { class: "detail" });
  const onchange = (structural) => { changed(); if (structural) redraw(); };
  box.append(h("div", { class: "small muted" }, meta.help || ""));
  if (meta.target !== "none") box.append(h("fieldset", {}, h("legend", {}, "Target – which element" + (meta.target === "optional" ? " (optional)" : "")),
    targetEditor(st, "target", { optional: meta.target === "optional", defaultStrategy: st.action === "navigate" ? "url" : "role", onchange: () => onchange(false) })));
  const inp = inputEditor(st, () => onchange(false)); if (inp) box.append(h("fieldset", {}, h("legend", {}, "Input"), inp));
  if (meta.query || st.query) {
    box.append(h("fieldset", {}, h("legend", {}, "SQL"),
      h("div", { class: "grid g2" }, field("Query", h("textarea", { rows: 4, spellcheck: false, oninput: (e) => { st.query = e.target.value; onchange(false); } }, st.query || "")),
        field("Connection name", textInput(st, "connection", { placeholder: "default", onchange: () => onchange(false) }))),
      h("button", { onclick: () => sqlTryDialog(st.query || "", st.connection || "default") }, "Try this query (read-only)")));
  }
  if (meta.config || st.config) box.append(h("fieldset", {}, h("legend", {}, "Config"), configEditor(st, () => onchange(false))));
  box.append(h("fieldset", {}, h("legend", {}, "Check the result (expected)"), expectedEditor(st, () => onchange(false), () => { changed(); redraw(); })));
  box.append(h("fieldset", {}, h("legend", {}, "Capture"), captureEditor(st, (s) => onchange(s))));
  box.append(h("fieldset", {}, h("legend", {}, "Advanced"), h("div", { class: "grid g4" },
    field("Timeout (ms)", numInput(st, "timeout_ms", { onchange: () => onchange(false), placeholder: "10000" })), field("Pause after step (ms)", numInput(st, "delay_after_ms", { onchange: () => onchange(false) })))));
  return box;
}
