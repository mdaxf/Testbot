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
        h("td", {}, h("b", {}, t.name), t.history ? h("span", { class: "muted small", style: { marginLeft: "6px" }, title: "suite revision on disk" }, t.history.revision) : null, t.history && t.history.drafts ? h("span", { style: { marginLeft: "4px" } }, badge(t.history.drafts + " draft" + (t.history.drafts > 1 ? "s" : ""), "warn")) : null, t.folder ? h("div", { class: "small muted" }, t.folder) : null),
        h("td", {}, t.suite_id || "", h("div", { class: "small muted" }, t.suite_name || "")),
        h("td", {}, ok ? badge(t.format === "testbot" ? "JSON" : "Excel", "ok") : (t.format === "apriso" ? badge("Apriso – import", "warn") : badge("Needs import", "warn"))),
        h("td", {}, t.cases), h("td", {}, t.steps), h("td", { class: "small" }, fmtTime(t.modified)),
        h("td", { onclick: (e) => e.stopPropagation(), style: { whiteSpace: "nowrap" } },
          ok ? h("button", { class: "icon", title: "Run now (all test cases)", onclick: () => runDialog(t.path, "suite", "") }, "▶") : null,
          ok ? h("button", { class: "icon", title: "Run selected test cases…", onclick: () => runCasesDialog(t.path, "") }, "☑") : null,
          ok ? h("button", { class: "icon", title: "Add to a session…", onclick: () => addToSessionDialog(t.path) }, "⇉") : null,
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
function runDialog(path, type, env, options, cases) {
  const log = h("pre", { class: "log" }, "Starting…"), status = h("div", { class: "muted" }, "Starting…"), links = h("div", {});
  let stop = false;
  const m = modal({ title: "Run: " + path, wide: true, body: h("div", {}, status, log, links), onclose: () => { stop = true; },
    buttons: [{ label: "Close" }] });
  (async () => {
    try {
      const { run } = await POST("/api/run", { path, type, env, options: options || {}, cases: cases && cases.length ? cases : undefined });
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

routes.edit = async (main, { path, tab, case: caseId }) => {
  if (!path) return go("tests");
  const data = await GET("/api/test?" + q({ path }));
  E = { path: data.path, saveAs: data.save_as, mtime: data.mtime, source: data.source, suite: data.suite, tab: tab || "steps", ci: 0, si: null, problems: [], open: {}, revisions: data.revisions, rev: null };
  S.dirty = false;
  if (caseId) { const i = E.suite.cases.findIndex((c) => c.id === caseId); if (i >= 0) E.ci = i; }
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
    ["expect", "constraints", "tags", "depends_on"].forEach((k) => { if (Array.isArray(c[k]) && !c[k].length) delete c[k]; });
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
  if (E.rev) return saveRevisionInPlace();
  const target = as || E.saveAs || E.path;
  try {
    const r = await PUT("/api/test", { path: target, suite: cleanSuite(E.suite), base_mtime: target === E.path ? E.mtime : undefined, overwrite: as ? false : true, source: E.nextSource || undefined, note: E.nextNote || undefined });
    E.nextSource = E.nextNote = undefined;
    E.mtime = r.mtime; E.path = r.path; E.saveAs = r.path; E.source = "json"; S.dirty = false; updateHeader();
    const t = $("#ed-title"); if (t) t.textContent = r.path;
    const rv = r.revision || {};
    if (rv.revision) { E.revisions = { ...(E.revisions || {}), default: rv.revision }; const b = $("#ed-rev"); if (b) b.textContent = rv.revision; }
    toast(rv.revision ? (rv.unchanged ? `Saved (no change: still ${rv.revision})` : `Saved as suite revision ${rv.revision}${rv.changed && rv.changed.length && rv.changed[0] !== "(first revision)" ? ": " + rv.changed.join(", ") : ""}`) : "Saved", "ok"); return true;
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
  const tabs = [["steps", "Cases & steps"], ["suite", "Suite settings"], ["vars", "Variables & SQL"], ["history", "History"], ["json", "JSON"], ["chat", "Chat"], ["ai", "AI"]];
  main.append(
    h("div", { class: "toolbar" },
      h("a", { href: "#/tests" }, "← Test cases"), h("h1", { id: "ed-title", style: { margin: 0 } }, E.path),
      E.source === "excel" ? badge("from Excel – saves as " + E.saveAs, "warn") : null,
      E.revisions && E.revisions.default ? h("span", { class: "badge", title: "The suite revision on disk (the default, which is what runs)" }, h("span", { id: "ed-rev" }, E.revisions.default), E.revisions.drafts ? ` · ${E.revisions.drafts} draft(s)` : "") : null, h("span", { id: "ed-dirty", class: "small", style: { color: "var(--warn)" } }),
      h("span", { class: "grow" }), h("span", { id: "problem-count" }),
      h("button", { onclick: async () => { await runValidate(); toast(E.problems.length ? "See the highlighted steps" : "No problems found", E.problems.length ? "" : "ok"); } }, "Validate"),
      ...(E.rev ? [] : [
      h("button", { title: "Save, then run only the open test case", onclick: () => saveSuite({}).then((ok) => ok && E.suite.cases[E.ci] && runDialog(E.path, "suite", E.suite.environment || "", {}, [E.suite.cases[E.ci].id])) }, "Run this case ▶"),
      h("button", { title: "Save, then choose which test cases to run", onclick: () => saveSuite({}).then((ok) => ok && runCasesDialog(E.path, E.suite.environment || "", E.suite.cases[E.ci] ? [E.suite.cases[E.ci].id] : [])) }, "Run cases…"),
      h("button", { onclick: () => saveSuite({}).then((ok) => ok && runDialog(E.path, "suite", E.suite.environment || "")) }, "Save & run all ▶"),
      h("button", { title: "Add the whole file, or some of its cases, to a session", onclick: () => saveSuite({}).then((ok) => ok && addToSessionDialog(E.path, E.suite.cases[E.ci] ? [E.suite.cases[E.ci].id] : [])) }, "Add to session…"),
      ]),
      h("button", { class: "primary", id: "btn-save", onclick: () => saveSuite({}) }, "Save"),
      h("button", { onclick: async () => { const n = await promptBox("Save as", "New file name", E.path.replace(/\.json$/i, "") + "-copy.json"); if (n) saveSuite({ as: n }); } }, "Save as…")),
    revisionBanner(),
    h("div", { class: "tabs" }, tabs.map(([k, l]) => h("button", { class: E.tab === k ? "active" : "", onclick: () => { E.tab = k; drawEditor(); runValidate(); } }, l))),
    h("div", { id: "ed-body" }));
  const body = $("#ed-body");
  ({ steps: stepsTab, suite: suiteTab, vars: (b) => varsTab(E, b, mark), history: historyTab, json: jsonTab, chat: chatTab, ai: (b) => aiEditorTab(E, b, mark, drawEditor) })[E.tab](body);
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
      field("Tags", csvInput(c, "tags", { onchange: mark, placeholder: "smoke, regression" }), "Run by tag: --tag smoke"),
      field("Depends on (case ids)", csvInput(c, "depends_on", { onchange: mark, placeholder: "TC-LOGIN" }), "When you run chosen cases, these run just before this one, in the same browser."),
      field("Preconditions", textInput(c, "preconditions", { onchange: mark })), field("Device (this case)", textInput(c, "device", { onchange: mark, list: "devices" })),
      field("Window width", numInput(c.viewport, "width", { onchange: mark })), field("Window height", numInput(c.viewport, "height", { onchange: mark })))),
    recommendationCard(c),
    h("details", { class: "card", open: !!(c.objective || c.mode) }, h("summary", {}, h("b", {}, "Natural-language test (agentic mode)"), " ",
      h("span", { class: "muted small" }, "state the goal in words; the AI agent decides the data and what to check. Steps below are then optional.")),
      h("div", { class: "grid g2", style: { marginTop: "8px" } },
        field("Objective", areaInput(c, "objective", { rows: 3, onchange: mark, placeholder: "e.g. Add a production line with a unique number and confirm it appears in the list" })),
        field("Data hints (optional)", areaInput(c, "data_hints", { rows: 3, onchange: mark, placeholder: "e.g. line numbers start with TEST_ and must be unique" })),
        field("Expected outcomes (one per line)", linesInput(c, "expect", { rows: 3, onchange: mark, placeholder: "The new line is listed\nNo error message is shown" }), "Each one must be proven by a passing check for the test to pass."),
        field("Constraints (one per line)", linesInput(c, "constraints", { rows: 3, onchange: mark, placeholder: "Do not delete any existing line" })),
        field("Start address (optional)", textInput(c, "start_url", { onchange: mark, placeholder: "{base_url}/portal" })),
        field("Mode for this case", selectInput(S.catalog.modes, c.mode || "", (v) => { c.mode = v; mark(); }, { blank: "(suite / environment default)" }), "TESTBOT_MODE and --mode override this."),
        h("div", { style: { paddingTop: "18px" } }, checkInput({ get learn() { return !!(c.agent && c.agent.learn); }, set learn(v) { c.agent = c.agent || {}; if (v) c.agent.learn = true; else delete c.agent.learn; if (!Object.keys(c.agent).length) delete c.agent; } }, "learn", "let the agent suggest improvements after a passing run (a recommendation to review — nothing changes by itself)", mark)))),
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


/* ------------------------------------------------------------------ history: revisions of the suite and of its test cases */
const REV_STATUS = { default: ["default (runs)", "ok"], draft: ["draft", "warn"], superseded: ["superseded", ""] };
const revBadge = (s) => badge(...(REV_STATUS[s] || [s, ""]));

function diffLines(changes) {
  if (!changes || !changes.length) return [h("div", { class: "muted small" }, "No difference.")];
  return changes.map((c) => {
    if (c.op === "field") return h("div", { class: "small" }, h("b", {}, c.field), ": ", h("span", { class: "fail" }, c.from || "∅"), "  →  ", h("span", { class: "pass" }, c.to || "∅"));
    if (c.op === "added") return h("div", { class: "small pass" }, `+ step ${c.step}: ${c.description || c.action || ""}`);
    if (c.op === "removed") return h("div", { class: "small fail" }, `− step ${c.step}: ${c.description || c.action || ""}`);
    return h("div", { class: "small" }, h("b", {}, `~ step ${c.step}`), ` ${c.description || ""}`, Object.entries(c.fields || {}).map(([f, [a, b]]) => h("div", { style: { marginLeft: "16px" } }, f + ": ", h("span", { class: "fail" }, a || "∅"), "  →  ", h("span", { class: "pass" }, b || "∅"))));
  });
}
function showDiff(title, nodes) { modal({ title, wide: true, body: h("div", { style: { maxHeight: "60vh", overflow: "auto" } }, nodes), buttons: [{ label: "Close" }] }); }

async function historyTab(body) {
  clear(body).append(h("span", { class: "muted" }, "Loading the history…"));
  let H;
  try { H = await GET("/api/revisions?" + q({ path: E.path })); } catch (e) { clear(body).append(h("div", { class: "banner bad" }, e.message)); return; }
  const reload = () => historyTab(body);
  const ask = async (title, msg, ok) => confirmBox(title, msg, ok);
  const makeDefault = async (payload, what) => {
    if (S.dirty && !(await ask("Unsaved changes", "Making another revision the default replaces the file on disk. Your unsaved changes in the editor will be lost.", "Continue"))) return;
    try { await POST("/api/revision/default", { path: E.path, ...payload }); toast(`${what} is now the default`, "ok"); S.dirty = false; go("edit", { path: E.path, r: Date.now() }); } catch (e) { fail(e); }
  };
  const caseId = (E.suite.cases[E.ci] || {}).id;
  const cs = H.cases[caseId];
  clear(body).append(
    h("p", { class: "muted small" }, "The file on disk is always the default version — the one that runs. Saving the default creates a new revision; a revision that is not the default is edited in place (no new revision). Suite revisions list which revision of each test case belongs together; changing one test case does not touch the others."),
    h("h3", {}, "Suite revisions"),
    h("div", { class: "card", style: { padding: 0 } }, table(["Revision", "Status", "When", "Source", "What changed", ""], H.manifests.map((m) => h("tr", {},
      h("td", {}, h("b", {}, m.id)), h("td", {}, revBadge(m.status)), h("td", { class: "small" }, fmtTime(m.created)), h("td", { class: "small" }, m.source + (m.note ? " — " + m.note : "")), h("td", { class: "small" }, m.changed.join(", ")),
      h("td", { style: { whiteSpace: "nowrap" } },
        m.changed[0] !== "(first revision)" ? h("button", { class: "ghost", title: "What this revision changed compared with the one before", onclick: async () => {
          const i = H.manifests.findIndex((x) => x.id === m.id), prev = H.manifests[i + 1]; if (!prev) return;
          const d = await GET("/api/revision/diff?" + q({ path: E.path, a: prev.id, b: m.id }));
          showDiff(`${prev.id} → ${m.id}`, [...d.settings.map((s) => h("div", { class: "small" }, h("b", {}, "settings " + s.field), ": ", h("span", { class: "fail" }, s.from), "  →  ", h("span", { class: "pass" }, s.to))),
            ...d.cases.flatMap((c) => [h("div", { style: { marginTop: "8px" } }, h("b", {}, c.case), "  ", c.change + (c.rev_b ? ` (rev ${c.rev_a ?? "–"} → ${c.rev_b})` : "")), ...(c.steps ? diffLines(c.steps) : [])]),
            d.order_changed ? h("div", { class: "small" }, "The order of the test cases changed.") : null]); } }, "Changes") : null,
        m.status !== "default" ? h("button", { class: "ghost", title: "Replace the file with this suite revision (it becomes the default)", onclick: async () => { if (await ask("Make default", `Make suite revision ${m.id} the default? The file on disk becomes exactly that version.`, "Make default")) makeDefault({ kind: "manifest", id: m.id }, "Suite revision " + m.id); } }, "Make default") : null,
        m.status !== "default" ? h("button", { class: "ghost danger", onclick: async () => { if (await ask("Delete revision", `Delete suite revision ${m.id}? The test case revisions stay.`, "Delete")) { try { await DEL("/api/revision?" + q({ path: E.path, kind: "manifest", id: m.id })); reload(); } catch (e) { fail(e); } } } }, "Delete") : null))))),
    h("h3", {}, "Test case ", h("select", { onchange: (e) => { E.ci = Number(e.target.value); reload(); } }, E.suite.cases.map((c, i) => h("option", { value: i, selected: i === E.ci }, c.id)))),
    cs ? h("div", { class: "card", style: { padding: 0 } }, table(["Revision", "Status", "When", "Source", "Note", ""], [...cs.revisions].reverse().map((r) => h("tr", {},
      h("td", {}, h("b", {}, "rev " + r.rev)), h("td", {}, revBadge(r.status)), h("td", { class: "small" }, fmtTime(r.created), r.edited ? h("div", { class: "muted" }, "edited " + fmtTime(r.edited)) : null),
      h("td", { class: "small" }, r.source), h("td", { class: "small" }, r.note || ""),
      h("td", { style: { whiteSpace: "nowrap" } },
        r.status !== "default" && cs.default != null ? h("button", { class: "ghost", title: "Compare with the default revision", onclick: async () => {
          const d = await GET("/api/revision/diff?" + q({ path: E.path, case: caseId, a: cs.default, b: r.rev })); showDiff(`${caseId}: default (rev ${cs.default}) → rev ${r.rev}`, diffLines(d.changes)); } }, "Compare with default") : null,
        r.status !== "default" ? h("button", { class: "ghost", title: "Open this revision in the editor. Saving changes THIS revision (no new revision).", onclick: async () => {
          if (S.dirty && !(await ask("Unsaved changes", "Opening another revision discards your unsaved changes in the editor.", "Continue"))) return;
          const c = (await GET("/api/revision/case?" + q({ path: E.path, case: caseId, rev: r.rev }))).case;
          const settings = { ...E.suite }; delete settings.cases; E.mainSuite = E.suite; E.suite = { ...settings, cases: [c] }; E.ci = 0; E.si = null; E.rev = { case: caseId, rev: r.rev, status: r.status }; E.tab = "steps"; S.dirty = false; drawEditor(); runValidate(); } }, "Edit this revision") : null,
        r.status !== "default" ? h("button", { class: "ghost", onclick: () => makeDefault({ kind: "case", case: caseId, rev: r.rev }, `${caseId} revision ${r.rev}`) }, "Make default") : null,
        r.status !== "default" ? h("button", { class: "ghost danger", onclick: async () => { if (await ask("Delete revision", `Delete revision ${r.rev} of ${caseId}?`, "Delete")) { try { await DEL("/api/revision?" + q({ path: E.path, kind: "case", case: caseId, rev: r.rev })); reload(); } catch (e) { fail(e); } } } }, "Delete") : null)))))
      : h("div", { class: "muted small" }, "This test case has no history yet (it is new)."),
    H.settings.length > 1 ? h("p", { class: "muted small" }, `Suite settings (base URL, variables, connections …) have their own history: ${H.settings.map((s) => s.id + (s.status === "default" ? " (default)" : "")).join(", ")}.`) : null);
}

/* editing a revision that is not the default: the normal editor on that revision's content; Save keeps the same revision */
async function saveRevisionInPlace() {
  const c = cleanSuite(E.suite).cases[0];
  try { await PUT("/api/revision/inplace", { path: E.path, case: E.rev.case, rev: E.rev.rev, content: c }); S.dirty = false; updateHeader(); toast(`Saved revision ${E.rev.rev} of ${E.rev.case} (same revision)`, "ok"); return true; }
  catch (e) { fail(e); return false; }
}
function revisionBanner() {
  if (!E || !E.rev) return null;
  return h("div", { class: "banner warn" }, h("b", {}, `Editing ${E.rev.case} revision ${E.rev.rev} (${E.rev.status}, not the default). `), "Save changes this revision itself — no new revision is created, and the test case that runs is not affected. ",
    h("button", { onclick: async () => { await saveRevisionInPlace(); } }, "Save this revision"), " ",
    h("button", { onclick: async () => { if (await saveRevisionInPlace()) { await POST("/api/revision/default", { path: E.path, kind: "case", case: E.rev.case, rev: E.rev.rev }); toast("Now the default", "ok"); go("edit", { path: E.path, r: Date.now() }); } } }, "Save and make default"), " ",
    h("button", { onclick: async () => { if (!S.dirty || await confirmBox("Unsaved changes", "Leave this revision without saving?", "Leave")) { S.dirty = false; go("edit", { path: E.path, r: Date.now() }); } } }, "Back to the default"));
}


/* ------------------------------------------------------------------ the agent's recommendation on a test case: review it, apply it (partly or fully), or skip it */
const stepLine = (s) => `${s.action || ""}${s.description ? " — " + s.description : ""}${s.target ? "  [" + describeTarget(s.target) + "]" : ""}${s.input ? "  ← " + String(s.input).slice(0, 40) : ""}`;
const wordsOf = (t) => new Set(String(t || "").toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length > 2));
function similarity(a, b) { const x = wordsOf(a), y = wordsOf(b); if (!x.size || !y.size) return 0; let n = 0; x.forEach((w) => y.has(w) && n++); return n / Math.max(x.size, y.size); }

/* the box on the case: pending recommendation with Review / Skip, or what happened to the last one */
function recommendationCard(c) {
  const r = c.ai_recommendation; if (!r) return null;
  if (r.status === "pending") {
    return h("div", { class: "banner warn" }, h("b", {}, r.kind === "heal" ? "Self-healing recommendation. " : "AI recommendation. "), r.kind === "heal" ? `In a passing run (${fmtTime(r.created)}) ${r.steps.length} step(s) used an element the AI fallback found because the scripted target no longer matched. The corrected targets are suggested; nothing changes until you review them. ` : `After a passing run (${fmtTime(r.created)}, ${r.model || "agent"}) the agent suggests ${r.steps.length} step(s) with the elements it identified. Nothing changes until you review it. `,
      h("button", { class: "primary", onclick: () => reviewRecommendation(c) }, "Review…"), " ", h("button", { onclick: async () => { try { await POST("/api/recommendation", { path: E.path, case: c.id, action: "skipped" }); go("edit", { path: E.path, r: Date.now() }); } catch (e) { fail(e); } } }, "Skip it"));
  }
  return h("div", { class: "muted small", style: { margin: "6px 0" } }, `Last AI recommendation (${fmtTime(r.created)}): ${r.status}${r.reviewed ? " " + fmtTime(r.reviewed) : ""}. `,
    r.status === "skipped" ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); reviewRecommendation(c); } }, "Review it again") : null);
}

function reviewRecommendation(c) {
  const r = c.ai_recommendation, orig = c.steps || [], recs = r.steps || [], used = new Set();
  /* default choice per recommended step: replace the original step it most resembles (same action, similar words), else add it */
  const choice = recs.map((rs) => {
    if (rs.replaces_step) { const j = orig.findIndex((o, k) => o.step_no === rs.replaces_step && !used.has(k)); if (j >= 0) { used.add(j); return "replace:" + j; } }
    let best = -1, score = 0.45;
    orig.forEach((os, j) => { if (used.has(j)) return; const sc = similarity(os.description, rs.description) + (os.action === rs.action ? 0.2 : 0); if (sc > score) { score = sc; best = j; } });
    if (best >= 0) used.add(best);
    return best >= 0 ? "replace:" + best : "add";
  });
  const st = { removeRest: false, target: "editor", variables: !!(r.variables && Object.keys(r.variables).length) };
  const picks = recs.map((rs, i) => selectInput([["skip", "Skip this step"], ["add", "Add it (keep the original steps)"], ...orig.map((os, j) => ["replace:" + j, `Replace original step ${os.step_no}: ${(os.description || os.action || "").slice(0, 46)}`])], choice[i], (v) => { choice[i] = v; preview(); }));
  const box = h("div", {});
  const compute = () => {
    const out = orig.map((s) => ({ ...s })), slot = out.slice(), replaced = new Set(); let last = -1;
    recs.forEach((rs, i) => {
      const ch = choice[i]; if (ch === "skip") return;
      const step = { ...rs }; delete step.step_no; delete step.replaces_step;
      if (ch.startsWith("replace:")) { const j = Number(ch.slice(8)), at = out.indexOf(slot[j]); out[at] = step; slot[j] = step; replaced.add(step); last = at; }
      else { const at = last >= 0 ? last + 1 : out.length; out.splice(at, 0, step); last = at; replaced.add(step); }
    });
    const final = st.removeRest ? out.filter((s) => replaced.has(s)) : out;
    final.forEach((s, i) => (s.step_no = i + 1));
    return final;
  };
  const preview = () => { const steps = compute(); clear(box).append(h("div", { class: "muted small", style: { margin: "8px 0 4px" } }, `Result: ${steps.length} step(s) (was ${orig.length})`), h("div", { class: "mono small", style: { maxHeight: "180px", overflow: "auto", background: "var(--bg)", padding: "6px 8px", borderRadius: "4px" } }, steps.map((s) => h("div", {}, `${s.step_no}. ${stepLine(s)}`)))); };
  const m = modal({ title: `${r.kind === "heal" ? "Self-healing recommendation" : "AI recommendation"} for ${c.id}`, wide: true, body: h("div", {},
      (r.notes || []).length ? h("div", { class: "muted small", style: { marginBottom: "6px" } }, r.notes.join(" · ")) : null,
      h("p", { class: "muted small" }, `From the run ${r.run || ""} (${r.model || "agent"}), made against revision ${r.base_revision ?? "–"} of this case. Choose what to do with each suggested step — the original steps are only changed where you say so.`),
      table(["#", "Suggested step (with the element the agent identified)", "What to do"], recs.map((rs, i) => h("tr", {}, h("td", {}, i + 1), h("td", { class: "small" }, stepLine(rs)), h("td", {}, picks[i])))),
      h("div", { class: "toolbar", style: { marginTop: "8px" } }, h("button", { onclick: () => { recs.forEach((_, i) => { choice[i] = "skip"; }); drawPicks(); } }, "Skip all"),
        h("button", { onclick: () => { recs.forEach((_, i) => { if (choice[i] === "skip") choice[i] = "add"; }); drawPicks(); } }, "Use all suggested (add the ones that match nothing)"),
        checkInput(st, "removeRest", "full replacement: drop the original steps that are not replaced", preview)),
      r.variables && Object.keys(r.variables).length ? h("div", {}, checkInput(st, "variables", `also add the variables the steps use (${Object.keys(r.variables).join(", ")})`)) : null,
      box,
      h("div", { style: { marginTop: "8px" } }, h("b", {}, "Where to apply it"), h("div", {}, h("label", {}, h("input", { type: "radio", name: "rtarget", checked: true, onchange: () => (st.target = "editor") }), " In the editor — then Save creates a new revision of this case")),
        h("div", {}, h("label", {}, h("input", { type: "radio", name: "rtarget", onchange: () => (st.target = "draft") }), " As a draft revision — the default stays exactly as it is (see the History tab)")))),
    buttons: [{ label: "Cancel" }, { label: "Apply", primary: true, onclick: async () => {
      const steps = compute();
      if (st.target === "draft") {
        if (S.dirty && !(await confirmBox("Unsaved changes", "Your unsaved changes in the editor are not part of the draft. Continue?", "Continue"))) return false;
        const content = JSON.parse(JSON.stringify(c)); delete content.ai_recommendation; content.steps = steps;
        try { await POST("/api/revision/draft", { path: E.path, case: c.id, content, source: "agent recommendation", note: `from run ${r.run || ""}` }); await POST("/api/recommendation", { path: E.path, case: c.id, action: "applied" }); toast("Saved as a draft revision (History tab)", "ok"); S.dirty = false; go("edit", { path: E.path, tab: "history", r: Date.now() }); } catch (e) { fail(e); return false; }
        return;
      }
      c.steps = steps; c.ai_recommendation = { ...r, status: "applied", reviewed: new Date().toISOString() };
      if (st.variables && r.variables) { E.suite.variables = E.suite.variables || {}; Object.entries(r.variables).forEach(([k, v]) => { if (!(k in E.suite.variables)) E.suite.variables[k] = v; }); }
      mark(); drawEditor(); toast("Applied in the editor — Save to keep it (a new revision of this case)", "ok"); } }] });
  function drawPicks() { picks.forEach((p, i) => (p.value = choice[i])); preview(); }
  preview();
  return m;
}

/* ------------------------------------------------------------------ reviews: what waits for a person, across all suites */
routes.reviews = async (main) => {
  const r = await GET("/api/reviews");
  main.append(h("h1", {}, "Reviews"), h("p", { class: "muted" }, "Suggestions and drafts that are waiting for you. Nothing here changes a test until you apply it: the agent's and self-healing recommendations are reviewed in the editor (replace, merge or skip, step by step), drafts are revisions that are not the default yet."),
    h("h2", {}, `Recommendations (${r.recommendations.length})`),
    h("div", { class: "card", style: { padding: 0 } }, table(["Test suite", "Test case", "Kind", "Suggested steps", "When", ""], r.recommendations.length ? r.recommendations.map((x) => h("tr", { class: "clickable", onclick: () => go("edit", { path: x.path, case: x.case }) },
      h("td", {}, h("b", {}, x.path)), h("td", {}, x.case, h("div", { class: "small muted" }, x.title)), h("td", {}, badge(x.kind === "heal" ? "self-healing" : "agent", x.kind === "heal" ? "warn" : "ok")),
      h("td", { class: "small" }, String(x.steps)), h("td", { class: "small" }, fmtTime(x.created), h("div", { class: "muted" }, x.run || "")), h("td", {}, h("button", { onclick: () => go("edit", { path: x.path, case: x.case }) }, "Review…")))) :
      [h("tr", {}, h("td", { colspan: 6, class: "empty" }, "Nothing to review. Turn on “let the agent suggest improvements” (Settings, or per test case) to get recommendations after passing agentic runs."))])),
    h("h2", {}, `Draft revisions (${r.drafts.length})`),
    h("div", { class: "card", style: { padding: 0 } }, table(["Test suite", "Test case", "Revision", "Source", "Note", "When", ""], r.drafts.length ? r.drafts.map((x) => h("tr", { class: "clickable", onclick: () => go("edit", { path: x.path, case: x.case, tab: "history" }) },
      h("td", {}, h("b", {}, x.path)), h("td", {}, x.case), h("td", {}, "rev " + x.rev), h("td", { class: "small" }, x.source || ""), h("td", { class: "small" }, x.note || ""), h("td", { class: "small" }, fmtTime(x.created)),
      h("td", {}, h("button", { onclick: () => go("edit", { path: x.path, case: x.case, tab: "history" }) }, "Open history")))) : [h("tr", {}, h("td", { colspan: 7, class: "empty" }, "No drafts."))])));
};
