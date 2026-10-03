"use strict";
/* Import wizard: load Excel / CSV / JSON in any format, map columns onto testbot fields, preview, save. */

const FIELD_LABELS = {
  description: "Step description", context: "Step heading / context (natural-language import)", action: "Action", target_strategy: "Target: how to find (strategy)", target_value: "Target: value / locator",
  target_name: "Target: accessible name (role)", input: "Input", expected_type: "Check: type", expected_value: "Check: value",
  expected_target_value: "Check: element (css)", query: "SQL query", connection: "SQL connection name", timeout_ms: "Timeout (ms)",
  delay_after_ms: "Delay after (ms)", capture_var: "Capture: variable name", capture_from: "Capture: from", capture_target_value: "Capture: element (css)",
};
const VALUE_MAPPED = { action: "actions", target_strategy: "strategies", expected_type: "expected", capture_from: "capture_from" };

routes.import = async (main, params) => {
  const { tests } = await GET("/api/tests");
  const I = { req: null, info: null, sheet: null, arrayPath: null, mapping: { columns: {}, value_maps: {}, defaults: {}, suite: { suite_id: "IMPORTED", suite_name: "Imported test", base_url: "" } },
              style: "steps", sheetsSel: null, groupMode: "single", caseId: "TC-001", caseTitle: "Imported test case", caseColumn: "", titleColumn: "", target: "imported.json" };
  const wiz = h("div", {}), result = h("div", {});
  const pick = h("select", {}, h("option", { value: "" }, "(choose a file already in the test-cases folder)"),
    tests.map((t) => h("option", { value: t.path, selected: t.path === params.path }, `${t.path}  —  ${t.format}`)));
  const fileIn = h("input", { type: "file", accept: ".xlsx,.xlsm,.csv,.json,.txt", onchange: async () => {
    const f = fileIn.files[0]; if (!f) return; I.req = { filename: f.name, content_b64: await readFileB64(f) }; I.sheetsSel = null; I.target = f.name.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_.-]+/g, "-") + ".json"; inspect(); } });
  pick.onchange = () => { if (pick.value) { I.sheetsSel = null; I.req = { path: pick.value }; I.target = pick.value.replace(/\.[^.]+$/, "") + "-imported.json"; inspect(); } };

  async function inspect(extra) {
    clear(result); clear(wiz).append(h("span", { class: "muted" }, "Reading the file…"));
    try {
      I.info = await POST("/api/import/inspect", { ...I.req, ...(extra || {}) });
    } catch (e) { clear(wiz).append(h("div", { class: "banner bad" }, e.message)); return; }
    const info = I.info;
    I.mapping.suite.suite_id = (I.req.filename || I.req.path || "IMPORTED").replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9]+/g, "-").toUpperCase().slice(0, 24) || "IMPORTED";
    I.mapping.suite.suite_name = (I.req.filename || I.req.path || "Imported").replace(/\.[^.]+$/, "");
    if (info.detected === "testbot" || info.detected === "testbot-excel") return drawNative();
    if (info.detected === "apriso") return drawApriso();
    if (info.kind === "table") { I.sheet = (info.sheets.find((s) => s.name === (extra && extra.sheet)) || info.sheets[0]); if (!I.sheet) { clear(wiz).append(h("div", { class: "banner bad" }, "No data found in the file.")); return; } I.mapping.columns = deep(I.sheet.suggested.columns); I.caseColumn = I.sheet.suggested.case_column || ""; I.titleColumn = I.sheet.suggested.case_title_column || ""; I.groupMode = I.caseColumn ? "column" : "single"; if (!I.sheetsSel) { I.sheetsSel = info.sheets.map((x) => x.name); if (info.sheets.length > 1) I.groupMode = "sheet"; } }
    else { I.array = info.arrays.find((a) => a.path === (info.suggested && info.suggested.steps_path)) || info.arrays[0]; if (!I.array) { clear(wiz).append(h("div", { class: "banner bad" }, "This JSON has no list of objects that could be steps.")); return; } I.mapping.columns = deep((info.suggested || {}).columns || {}); I.caseColumn = (info.suggested || {}).case_column || ""; I.titleColumn = (info.suggested || {}).case_title_column || ""; I.groupMode = I.caseColumn ? "column" : "single"; }
    I.mapping.value_maps = {}; drawMapping();
  }

  function drawNative() {
    clear(wiz).append(h("div", { class: "banner ok" }, "This file is already in testbot format – nothing to map."),
      I.req.path ? h("button", { class: "primary", onclick: () => go("edit", { path: I.req.path }) }, "Open in the editor") : h("button", { class: "primary", onclick: () => preview({ kind: "testbot" }) }, "Preview and save a copy"));
  }
  function drawApriso() {
    clear(wiz).append(h("div", { class: "banner ok" }, "This looks like an Apriso AutomaticTest scenario. It is converted with the control map (config/apriso_control_map.yaml): login, screen search, grid/form/tree controls, SQL and the # sequence token."),
      h("button", { class: "primary", onclick: () => preview({ kind: "apriso" }) }, "Convert & preview"));
  }

  function drawMapping() {
    const info = I.info, cols = I.sheet ? I.sheet.columns : I.array.keys, distinct = I.sheet ? I.sheet.distinct : I.array.distinct;
    const colOpts = cols.map((c) => [c, c]);
    const m = I.mapping;
    const mapRows = Object.entries(FIELD_LABELS).map(([f, label]) => h("tr", {}, h("td", {}, label),
      h("td", {}, selectInput(colOpts, m.columns[f] || "", (v) => { if (v) m.columns[f] = v; else delete m.columns[f]; drawMapping(); }, { blank: "(not mapped)" })),
      h("td", { class: "small muted" }, m.columns[f] && distinct && distinct[m.columns[f]] ? (distinct[m.columns[f]].slice(0, 3).join(" | ")) : "")));
    const valueMaps = Object.entries(VALUE_MAPPED).filter(([f]) => m.columns[f]).map(([f, catKey]) => {
      const valid = Object.keys(S.catalog[catKey]);
      const vals = (distinct && distinct[m.columns[f]]) || [];
      m.value_maps[f] = m.value_maps[f] || {};
      return h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, `Translate values of “${m.columns[f]}” → ${FIELD_LABELS[f]}`),
        vals.length ? table(["Value in your file", "becomes (testbot)"], vals.map((v) => {
          const ok = valid.includes(v.trim().toLowerCase());
          return h("tr", {}, h("td", {}, v, ok ? h("span", { class: "badge ok", style: { marginLeft: "6px" } }, "known") : h("span", { class: "badge warn", style: { marginLeft: "6px" } }, "map me")),
            h("td", {}, selectInput(valid, m.value_maps[f][v] || "", (x) => { if (x) m.value_maps[f][v] = x; else delete m.value_maps[f][v]; }, { blank: ok ? "(keep)" : "(choose…)" })));
        })) : h("div", { class: "muted small" }, "No values found."));
    });
    const sheetPicker = I.sheet ? h("div", { class: "grid g3" },
      field("Sheet", selectInput(info.sheets.map((s) => s.name), I.sheet.name, (v) => inspect({ sheet: v }))),
      field("Header row (1 = first row)", h("input", { type: "number", min: 1, value: I.sheet.header_row + 1, onchange: (e) => inspect({ sheet: I.sheet.name, header_row: Math.max(0, Number(e.target.value) - 1) }) })),
      h("div", { class: "muted small", style: { paddingTop: "18px" } }, `${I.sheet.row_count} data rows`)) :
      h("div", { class: "grid g3" }, field("List of steps in the JSON", selectInput(info.arrays.map((a) => [a.path, `${a.path}  (${a.count} items)`]), I.array.path, (v) => { I.array = info.arrays.find((a) => a.path === v); m.columns = {}; I.caseColumn = ""; drawMapping(); })));
    const multi = I.sheet && info.sheets.length > 1;
    const tabPicker = multi ? h("div", { style: { marginTop: "10px" } }, h("b", {}, "Tabs to import"), h("span", { class: "muted small" }, "  – each ticked tab becomes one test case in the suite"),
      h("div", { style: { display: "flex", flexWrap: "wrap", gap: "6px 18px", marginTop: "6px" } }, info.sheets.map((x) => h("label", {},
        h("input", { type: "checkbox", checked: I.sheetsSel.includes(x.name), onchange: (e) => { I.sheetsSel = e.target.checked ? [...I.sheetsSel, x.name] : I.sheetsSel.filter((n) => n !== x.name); } }), ` ${x.name} `, h("span", { class: "muted small" }, `(${x.row_count} rows)`)))),
      h("div", { class: "muted small", style: { marginTop: "4px" } }, "Columns are matched by name; a tab that spells a column differently (e.g. “Keyword” instead of “Action”) is matched with its own suggestion. The mapping below is shown for the tab chosen in “Sheet”.")) : null;
    const preview_ = I.sheet ? table(I.sheet.columns, I.sheet.preview.map((r) => h("tr", {}, r.map((c) => h("td", { class: "small" }, c))))) : null;
    clear(wiz).append(
      h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "1. Where is the data?"), sheetPicker, tabPicker, preview_ ? h("div", { style: { overflow: "auto", maxHeight: "220px", marginTop: "8px" } }, preview_) : null),
      h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "2. Which column is which?"), h("p", { class: "muted small" }, "Suggestions are pre-filled from the column names. Only map what your file has."),
        table(["testbot field", "Column in your file", "examples"], mapRows)),
      valueMaps,
      h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "3. Import as"),
        h("label", {}, h("input", { type: "radio", name: "sty", checked: I.style === "steps", onchange: () => { I.style = "steps"; drawMapping(); } }), " Script steps (fixed actions and targets)"), " ",
        h("label", {}, h("input", { type: "radio", name: "sty", checked: I.style === "agentic", onchange: () => { I.style = "agentic"; drawMapping(); } }), " Natural language (agentic) test case"),
        I.style === "agentic" ? h("div", { class: "muted small", style: { marginTop: "6px" } }, "Each case gets an objective written from its rows (step description, plus action / target / input as hints) and the “Check: value” cells become the expected results. The AI agent works out how to do it when the suite runs in agentic mode – an AI provider key is needed (see Settings).") : null),
      h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "4. Test cases"),
        (multi ? [h("label", {}, h("input", { type: "radio", name: "grp", checked: I.groupMode === "sheet", onchange: () => { I.groupMode = "sheet"; drawMapping(); } }), " Each ticked tab is one test case "), " "] : null),
        h("label", {}, h("input", { type: "radio", name: "grp", checked: I.groupMode === "single", onchange: () => { I.groupMode = "single"; drawMapping(); } }), " All rows are one test case"),
        " ", h("label", {}, h("input", { type: "radio", name: "grp", checked: I.groupMode === "column", onchange: () => { I.groupMode = "column"; drawMapping(); } }), " Start a new test case whenever a column changes"),
        I.groupMode === "sheet" ? h("div", { class: "grid g3", style: { marginTop: "8px" } }, field("Title column (optional)", selectInput(colOpts, I.titleColumn, (v) => (I.titleColumn = v), { blank: "(use the tab name)" }), "The case ID is the tab name; the title is the first value in this column.")) : I.groupMode === "single" ? h("div", { class: "grid g3", style: { marginTop: "8px" } }, field("Case ID", textInput(I, "caseId")), field("Title", textInput(I, "caseTitle"))) :
          h("div", { class: "grid g3", style: { marginTop: "8px" } }, field("Case column", selectInput(colOpts, I.caseColumn, (v) => (I.caseColumn = v), { blank: "(choose)" }), "Blank cells continue the previous case."),
            field("Title column (optional)", selectInput(colOpts, I.titleColumn, (v) => (I.titleColumn = v), { blank: "(none)" }))),
        h("div", { class: "grid g3", style: { marginTop: "8px" } }, field("If a target has no “how to find”, use", selectInput(Object.keys(S.catalog.strategies), m.defaults.target_strategy || "css", (v) => (m.defaults.target_strategy = v))))),
      h("div", { class: "toolbar" }, h("button", { class: "primary", onclick: () => preview({ kind: I.sheet ? "table" : "json" }) }, "Preview")));
  }

  async function preview(base) {
    const m = { ...deep(I.mapping), ...base };
    if (base.kind === "table") { m.sheet = I.sheet.name; m.header_row = I.sheet.header_row; m.header_rows = Object.fromEntries(I.info.sheets.map((x) => [x.name, x.header_row])); m.header_rows[I.sheet.name] = I.sheet.header_row; m.sheets = I.groupMode === "sheet" ? I.sheetsSel : [I.sheet.name]; m.case_per_sheet = I.groupMode === "sheet"; if (I.groupMode === "sheet" && !m.sheets.length) { clear(result).append(h("div", { class: "banner bad" }, "Tick at least one tab to import.")); return; } }
    if (base.kind === "table" || base.kind === "json") m.style = I.style;
    if (base.kind === "json") m.steps_path = I.array.path;
    if (base.kind === "table" || base.kind === "json") {
      if (I.groupMode === "sheet") { m.case_title_column = I.titleColumn || null; /* ids come from the tab names, titles from the title column */ } else if (I.groupMode === "column" && I.caseColumn) { m.case_column = I.caseColumn; m.case_title_column = I.titleColumn || null; } else { m.case_id = I.caseId; m.case_title = I.caseTitle; }
    }
    clear(result).append(h("span", { class: "muted" }, "Converting…"));
    let r; try { r = await POST("/api/import/convert", { ...I.req, mapping: m }); } catch (e) { clear(result).append(h("div", { class: "banner bad" }, e.message)); return; }
    I.result = r;
    const s = r.suite, total = s.cases.reduce((n, c) => n + c.steps.length, 0), nl = s.cases.filter((c) => c.objective && !c.steps.length).length;
    const errs = r.problems.filter((p) => p.level === "error");
    clear(result).append(h("div", { class: "card" },
      h("h3", { style: { marginTop: 0 } }, "Preview: ", nl ? `${s.cases.length} natural-language test case(s)` : `${s.cases.length} test case(s), ${total} step(s)`, " ", errs.length ? badge(errs.length + " problem(s) to fix", "bad") : badge("valid", "ok")),
      r.warnings.length ? h("div", { class: "banner warn" }, h("b", {}, "Things to check"), r.warnings.slice(0, 40).map((w) => h("div", { class: "small" }, w)), r.warnings.length > 40 ? h("div", { class: "small" }, `…and ${r.warnings.length - 40} more`) : null) : null,
      errs.length ? h("div", { class: "banner bad" }, errs.slice(0, 12).map((p) => h("div", { class: "small" }, (p.case !== undefined ? `${s.cases[p.case]?.id} step ${p.step + 1}: ` : "") + p.message)), h("div", { class: "small" }, "You can still save and fix these in the editor.")) : null,
      s.cases.slice(0, 8).map((c) => c.objective && !c.steps.length ? h("div", { style: { marginTop: "8px" } }, h("b", {}, `${c.id} — ${c.title}`), " ", badge("natural language", "ok"), h("pre", { class: "small", style: { whiteSpace: "pre-wrap", margin: "4px 0" } }, c.objective), (c.expect || []).length ? h("div", { class: "small" }, h("b", {}, "Expected: "), (c.expect || []).join("  ·  ")) : null) : h("div", {}, h("b", {}, `${c.id} — ${c.title}`), table(["#", "Action", "Description", "Target", "Input"], c.steps.slice(0, 12).map((st, i) => h("tr", {}, h("td", {}, i + 1), h("td", {}, st.action || ""), h("td", {}, st.description), h("td", { class: "small" }, describeTarget(st.target)), h("td", { class: "small" }, st.input || ""))), { class: "steps" }), c.steps.length > 12 ? h("div", { class: "muted small" }, `…${c.steps.length - 12} more steps`) : null)),
      s.mode === "agentic" ? h("div", { class: "banner ok small" }, "Suite mode is set to agentic: run it with the AI agent (needs an AI provider key).") : null,
      h("div", { class: "grid g3", style: { marginTop: "10px" } }, field("Suite ID", textInput(s, "suite_id")), field("Suite name", textInput(s, "suite_name")), field("Base URL", textInput(s, "base_url", { placeholder: "http://server/app" })),
        field("Save as (file in the test-cases folder)", textInput(I, "target"))),
      h("div", { class: "toolbar" }, h("button", { class: "primary", onclick: save }, "Save as test case"))));
  }
  async function save() {
    let name = I.target.trim(); if (!name.toLowerCase().endsWith(".json")) name += ".json";
    try { const r = await PUT("/api/test", { path: name, suite: I.result.suite, overwrite: false }); toast("Saved " + r.path, "ok"); go("edit", { path: r.path }); }
    catch (e) { if (e.status === 409 && await confirmBox("File exists", `${name} already exists. Overwrite it?`, "Overwrite", true)) { try { const r = await PUT("/api/test", { path: name, suite: I.result.suite, overwrite: true }); go("edit", { path: r.path }); } catch (e2) { fail(e2); } } else if (e.status !== 409) fail(e); }
  }

  main.append(h("h1", {}, "Import test cases"),
    h("p", { class: "muted" }, "Load an Excel, CSV or JSON file – even if it is not in testbot format. You tell testbot which column is which, preview the result, then save it as a normal test case."),
    h("div", { class: "card" }, h("div", { class: "grid g2" }, field("Upload a file", fileIn, "Excel (.xlsx), CSV or JSON"), field("Or use a file that is already in the folder", pick))), wiz, result);
  if (params.path) { I.req = { path: params.path }; I.target = params.path.replace(/\.[^.]+$/, "") + "-imported.json"; inspect(); }
};
