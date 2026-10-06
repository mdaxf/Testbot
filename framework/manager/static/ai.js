"use strict";
/* AI assistant: generate a test case from a description, optimise an existing one, convert text or another format.
   Results are only ever PROPOSED: preview first, nothing is saved until you choose to. */

function aiBanner() {
  const a = S.app.ai;
  return a.configured ? h("div", { class: "banner ok" }, `AI provider: ${a.provider} (${a.model}). Text you enter here (and the test case, for Optimize) is sent to that service only when you press an AI button.`)
    : h("div", { class: "banner warn" }, h("b", {}, "AI is not configured. "), a.error ? a.error : `Set ${a.missing_env.join(", ")} in the environment before starting the manager (the key is never stored in a file), and choose the provider in Settings.`);
}

function suiteSummary(s) {
  return h("div", {}, s.cases.map((c) => h("div", { style: { marginBottom: "8px" } }, h("b", {}, `${c.id} — ${c.title}`),
    table(["#", "Action", "Description", "Target", "Input", "Check"], c.steps.map((st, i) => h("tr", {}, h("td", {}, i + 1), h("td", {}, st.action), h("td", {}, st.description), h("td", { class: "small" }, describeTarget(st.target)), h("td", { class: "small" }, st.input || ""), h("td", { class: "small" }, describeCheck(st.expected))))))));
}

function aiResultView(res, actions) {
  const errs = res.problems.filter((p) => p.level === "error");
  return h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Proposal ", errs.length ? badge(errs.length + " problem(s)", "bad") : badge("valid", "ok"), " ", h("span", { class: "muted small" }, res.model + (res.repaired ? " · corrected after a validation round" : ""))),
    res.notes.length ? h("div", { class: "banner" }, h("b", {}, "Notes"), res.notes.map((n) => h("div", { class: "small" }, "• " + n))) : null,
    errs.length ? h("div", { class: "banner bad" }, errs.slice(0, 10).map((p) => h("div", { class: "small" }, (p.step !== undefined ? `step ${p.step + 1}: ` : "") + p.message))) : null,
    suiteSummary(res.suite), h("div", { class: "toolbar" }, actions));
}

async function saveNewFromAi(suite) {
  const name = await promptBox("Save as test case", "File name", (suite.suite_id || "ai-test").toLowerCase().replace(/[^a-z0-9_.-]+/g, "-") + ".json"); if (!name) return;
  try { const r = await PUT("/api/test", { path: name.endsWith(".json") ? name : name + ".json", suite, overwrite: false }); toast("Saved " + r.path, "ok"); go("edit", { path: r.path }); } catch (e) { fail(e); }
}

/* Optimise one case or every case of a suite: one request per case (the model never sees - and never shortens - the other cases).
   Each answer is the whole suite with that case replaced, and is the input of the next request. A case that fails stays as it was. */
async function optimizeCases(suite, goals, ids, progress) {
  let cur = suite, last = null; const notes = []; let repaired = false;
  for (let i = 0; i < ids.length; i++) {
    progress(`Optimising ${ids[i]} (${i + 1} of ${ids.length})… this can take up to a minute per test case`);
    try { last = await POST("/api/ai/optimize", { suite: cur, goals, case_id: ids[i] }); } catch (e) { notes.push(`${ids[i]}: NOT optimised, kept as it was (${e.message})`); continue; }
    cur = last.suite; notes.push(...last.notes); repaired = repaired || last.repaired;
  }
  if (!last) throw new Error(notes.join("\n") || "nothing was optimised");
  return { suite: cur, notes, model: last.model, repaired, problems: last.problems };
}

routes.ai = async (main) => {
  const { tests } = await GET("/api/tests");
  const ok = tests.filter((t) => ["testbot", "testbot-excel"].includes(t.format));
  const tabs = { generate: "Generate", optimize: "Optimize", chat: "Chat", convert: "Convert" }; let cur = "generate";
  const panel = h("div", {}), tabBar = h("div", { class: "tabs" });
  const draw = () => {
    clear(tabBar).append(...Object.entries(tabs).map(([k, l]) => h("button", { class: cur === k ? "active" : "", onclick: () => { cur = k; draw(); } }, l)));
    clear(panel);
    const out = h("div", {});
    const busy = async (btn, fn) => { btn.disabled = true; clear(out).append(h("span", { class: "muted" }, "Waiting for the AI service… (this can take up to a minute)")); try { await fn(); } catch (e) { clear(out).append(h("div", { class: "banner bad" }, e.message)); } btn.disabled = false; };
    if (cur === "generate") {
      const f = { description: "", base_url: "", page_html: "" };
      const btn = h("button", { class: "primary", onclick: () => busy(btn, async () => { const r = await POST("/api/ai/generate", f); clear(out).append(aiResultView(r, [h("button", { class: "primary", onclick: () => saveNewFromAi(r.suite) }, "Save as new test case…")])); }) }, "Generate test case");
      panel.append(h("p", { class: "muted" }, "Describe what should be tested in plain words. Optionally paste the HTML of the page so the steps use the real labels and ids."),
        h("div", { class: "card" }, field("What should the test do?", h("textarea", { rows: 5, oninput: (e) => (f.description = e.target.value), placeholder: "e.g. Log in as a planner, open Production Line Setup, add a production line and check it appears in the list" })),
          h("div", { class: "grid g2" }, field("Base URL (optional)", textInput(f, "base_url")), field("Page HTML (optional, helps a lot)", h("textarea", { rows: 3, oninput: (e) => (f.page_html = e.target.value) }))), btn), out);
    } else if (cur === "optimize") {
      const f = { path: ok[0] ? ok[0].path : "", caseId: "", goals: "" };
      let loaded = null; const caseBox = h("div", {});
      const loadCases = async () => {
        loaded = null; f.caseId = ""; clear(caseBox); if (!f.path) return;
        try { loaded = (await GET("/api/test?" + q({ path: f.path }))).suite; } catch (e) { caseBox.append(h("div", { class: "banner bad" }, e.message)); return; }
        caseBox.append(field("Which test case", selectInput([["", `All ${loaded.cases.length} test case(s) in the file – one at a time`], ...loaded.cases.map((c) => [c.id, `${c.id} — ${c.title}`])], "", (v) => (f.caseId = v)),
          "Every test case is sent to the AI separately, so a long file is never cut short. Pick one case to optimise only that one."));
      };
      const btn = h("button", { class: "primary", onclick: () => busy(btn, async () => {
        if (!f.path) throw new Error("choose a test case file");
        const suite = loaded || (await GET("/api/test?" + q({ path: f.path }))).suite;
        const ids = f.caseId ? [f.caseId] : suite.cases.map((c) => c.id);
        const r = await optimizeCases(suite, f.goals, ids, (m) => clear(out).append(h("span", { class: "muted" }, m)));
        const what = f.caseId ? `only ${f.caseId} changed; the other cases stay as they are` : `${ids.length} test case(s)`;
        clear(out).append(aiResultView(r, [h("button", { class: "primary", onclick: () => saveNewFromAi(r.suite) }, "Save as new test case…"),
          h("button", { class: "danger", onclick: async () => { if (await confirmBox("Replace the original", `Overwrite ${f.path} with this version (${what})? The previous file is kept as .bak.`, "Replace", true)) { try { await PUT("/api/test", { path: f.path, suite: r.suite, overwrite: true }); toast("Replaced", "ok"); go("edit", { path: f.path }); } catch (e) { fail(e); } } } }, "Replace the original")])); }) }, "Optimize");
      panel.append(h("p", { class: "muted" }, "The AI reviews an existing test case file and proposes a more robust version (better waits and targets, added checks, clearer descriptions) without changing what it tests. Choose the file, then all of its test cases or just one."),
        h("div", { class: "card" }, h("div", { class: "grid g2" }, field("Test case file", selectInput(ok.map((t) => t.path), f.path, (v) => { f.path = v; loadCases(); })), field("Goals (optional)", h("input", { placeholder: "e.g. remove fixed waits, add URL checks", oninput: (e) => (f.goals = e.target.value) }))), caseBox, btn), out);
      loadCases();
    } else if (cur === "chat") {
      if (!S.app.ai.configured) panel.append(aiBanner()); else chatPage(panel, tests);
    } else {
      const f = { content: "", hint: "", base_url: "" };
      const fileIn = h("input", { type: "file", onchange: async () => { const file = fileIn.files[0]; if (file) { f.content = await file.text(); f.hint = f.hint || file.name; ta.value = f.content; } } });
      const ta = h("textarea", { rows: 10, oninput: (e) => (f.content = e.target.value), placeholder: "Paste manual test steps, a script from another tool, CSV text, …" });
      const btn = h("button", { class: "primary", onclick: () => busy(btn, async () => { const r = await POST("/api/ai/convert", f); clear(out).append(aiResultView(r, [h("button", { class: "primary", onclick: () => saveNewFromAi(r.suite) }, "Save as new test case…")])); }) }, "Convert");
      panel.append(h("p", { class: "muted" }, "Turn free text or another tool's script into a testbot test case. (For Excel/CSV/JSON tables the Import page is more exact – you decide the column mapping.)"),
        h("div", { class: "card" }, field("Source", ta), h("div", { class: "grid g3" }, field("…or load a text file", fileIn), field("What is it? (optional)", textInput(f, "hint", { placeholder: "e.g. Selenium Python script" })), field("Base URL (optional)", textInput(f, "base_url"))), btn), out);
    }
  };
  main.append(h("h1", {}, "AI assistant"), aiBanner(), tabBar, panel); draw();
};

/* the AI tab inside the editor: improve THIS suite, or add generated steps to the open case */
function aiEditorTab(E, body, mark, redraw) {
  const out = h("div", {}), f = { goals: "", description: "" };
  const busy = async (btn, fn) => { btn.disabled = true; clear(out).append(h("span", { class: "muted" }, "Waiting for the AI service…")); try { await fn(); } catch (e) { clear(out).append(h("div", { class: "banner bad" }, e.message)); } btn.disabled = false; };
  const optimizeBtn = (label, ids) => { const b = h("button", { onclick: () => busy(b, async () => {
    const r = await optimizeCases(cleanSuite(E.suite), f.goals, ids(), (m) => clear(out).append(h("span", { class: "muted" }, m)));
    clear(out).append(aiResultView(r, [h("button", { class: "primary", onclick: () => { E.suite = r.suite; E.si = null; E.ci = Math.min(E.ci, r.suite.cases.length - 1); mark(); E.tab = "steps"; redraw(); toast("Applied to the editor – review and Save", "ok"); } }, "Apply to the editor (not saved yet)")])); }) }, label); return b; };
  const optBtn = optimizeBtn(`Optimize this test case (${E.suite.cases[E.ci] ? E.suite.cases[E.ci].id : ""})`, () => [E.suite.cases[E.ci].id]);
  const optAllBtn = E.suite.cases.length > 1 ? optimizeBtn(`Optimize all ${E.suite.cases.length} test cases`, () => E.suite.cases.map((x) => x.id)) : null;
  const c = E.suite.cases[E.ci];
  const genBtn = h("button", { onclick: () => busy(genBtn, async () => {
    const r = await POST("/api/ai/generate", { description: f.description, base_url: E.suite.base_url || "", variables: Object.keys(E.suite.variables || {}) });
    const steps = r.suite.cases.flatMap((x) => x.steps);
    clear(out).append(aiResultView(r, [h("button", { class: "primary", onclick: () => { c.steps.push(...steps); c.steps.forEach((s, i) => (s.step_no = i + 1)); mark(); E.tab = "steps"; redraw(); toast(`${steps.length} steps added to ${c.id}`, "ok"); } }, `Add these ${steps.length} steps to ${c.id}`)])); }) }, "Generate steps");
  body.append(aiBanner(),
    h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Improve test cases"), field("Goals (optional)", textInput(f, "goals", { placeholder: "e.g. replace waits, add checks" })), h("div", { class: "toolbar" }, optBtn, optAllBtn), h("div", { class: "muted small" }, "The open test case is sent on its own; “all” sends them one at a time, so none is cut short.")),
    h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, `Generate more steps for ${c ? c.id : "the case"}`), field("What should the new steps do?", h("textarea", { rows: 3, oninput: (e) => (f.description = e.target.value) })), genBtn), out);
}
