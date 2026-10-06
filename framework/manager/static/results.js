"use strict";
/* Results: list of runs found in the results folder, and a step-level viewer with screenshots. */

routes.results = async (main, params) => {
  if (params.path) return resultDetail(main, params.path);
  if (params.case) return caseDetail(main, params.suite, params.case);
  if (params.suite) return suiteDetail(main, params.suite);
  if (params.session) return sessionDetail(main, params.session);
  if (params.view && params.view !== "run") return summaryView(main, params.view);
  const [{ runs }, st] = await Promise.all([GET("/api/results"), GET("/api/state")]);
  const f = { text: "", status: "", kind: "" };
  const tbl = table(["Kind", "Run", "When", "Status", "Result", ""], []), body = tbl.querySelector("tbody");
  const draw = () => {
    clear(body);
    const rows = runs.filter((r) => (!f.text || `${r.id} ${r.name} ${r.path}`.toLowerCase().includes(f.text)) && (!f.status || r.status === f.status) && (!f.kind || r.kind === f.kind));
    if (!rows.length) body.append(h("tr", {}, h("td", { colspan: 6, class: "empty" }, runs.length ? "No run matches the filter." : "No results yet. Run a test, then refresh.")));
    rows.forEach((r) => body.append(h("tr", { class: "clickable", onclick: () => go("results", { path: r.path }) },
      h("td", {}, badge(r.kind)), h("td", {}, h("b", {}, r.id || ""), h("div", { class: "small muted" }, r.path)), h("td", { class: "small" }, fmtTime(r.started || r.modified)),
      h("td", {}, statusBadge(r.status)), h("td", {}, r.kind === "load" ? `${r.passed}/${r.cases} runs passed` : `${r.passed}/${r.cases} cases passed`), h("td", { class: "small muted" }, r.elapsed_s ? r.elapsed_s + " s" : ""))));
  };
  main.append(h("h1", {}, "Results"), resultTabs("run"), h("div", { class: "toolbar" },
    h("input", { placeholder: "Filter…", oninput: (e) => { f.text = e.target.value.toLowerCase(); draw(); } }),
    selectInput(["pass", "fail", "empty"], "", (v) => { f.status = v; draw(); }, { blank: "any status" }), selectInput(["suite", "session", "load"], "", (v) => { f.kind = v; draw(); }, { blank: "any kind" }),
    h("button", { onclick: () => go("results", { r: Date.now() }) }, "Refresh"), h("span", { class: "grow" }),
    h("span", { class: "muted small" }, "Folder: ", h("span", { class: "mono" }, st.dirs.results.path), " ", h("a", { href: "#/settings" }, "change"))),
    h("div", { class: "card", style: { padding: 0 } }, tbl));
  draw();
};

function lightbox(src) { modal({ title: "Screenshot", wide: true, body: h("img", { src, style: { maxWidth: "100%" } }), buttons: [{ label: "Close" }] }); }

async function resultDetail(main, path) {
  const r = await GET("/api/result?" + q({ path }));
  const folder = r.folder ? r.folder + "/" : "";
  const fileUrl = (rel) => "/files/results/" + folder.split("/").map(encodeURIComponent).join("/") + rel.split("/").map(encodeURIComponent).join("/");
  main.append(h("div", { class: "toolbar" }, h("a", { href: "#/results" }, "← Results")));
  if (r.kind === "load") return loadDetail(main, r);
  const suites = r.kind === "session" ? r.data.suites : [r.data];
  const all = suites.flatMap((s) => s.cases);
  const passed = all.filter((c) => c.status === "pass").length;
  main.append(h("h1", {}, r.data.session_id || r.data.suite_id, " ", statusBadge(passed === all.length && all.length ? "pass" : "fail")),
    h("div", { class: "kv card" }, h("div", {}, "Started"), h("div", {}, fmtTime(r.data.started_at)), h("div", {}, "Finished"), h("div", {}, fmtTime(r.data.finished_at)),
      h("div", {}, "Result"), h("div", {}, `${passed} of ${all.length} case(s) passed`),
      ...(r.kind === "suite" && r.data.selected ? [h("div", {}, "Selection"), h("div", {}, `${r.data.selected.length} of ${r.data.cases_in_file} case(s) selected, run in this order: ${r.data.selected.join(", ")}`)] : []), h("div", {}, "Files"), h("div", {},
        r.html_report ? h("a", { href: fileUrl(r.html_report), target: "_blank" }, "HTML report") : null, " ", h("span", { class: "mono small" }, r.path)),
      h("div", {}, "Trend"), h("div", { id: "trend" })));
  trend(r.data.session_id || r.data.suite_id);
  logPanel(main, path);
  suites.forEach((s) => {
    if (r.kind === "session") main.append(h("h2", {}, "File: " + s.suite_id, s.selected ? h("span", { class: "muted small" }, `  ${s.selected.length} of ${s.cases_in_file} case(s) selected: ${s.selected.join(", ")}`) : null));
    s.cases.forEach((c) => main.append(h("details", { class: "card", open: c.status !== "pass" },
      h("summary", {}, statusBadge(c.status), " ", h("b", {}, c.case_id), " — ", c.title, " ", h("span", { class: "muted small" }, (c.duration_ms / 1000).toFixed(1) + " s, " + c.steps.length + " steps" + (c.finished_at ? ", finished " + fmtTime(c.finished_at) : "")), " ",
        h("a", { href: "#", class: "small", onclick: (e) => { e.preventDefault(); e.stopPropagation(); go("results", { suite: s.suite_id, case: c.case_id.replace(/~\d+$/, "") }); } }, "history")),
      table(["#", "Description", "Action", "Data used", "Status", "Expected / actual", "Evidence"], c.steps.map((st) => h("tr", {},
        h("td", {}, st.step_no), h("td", {}, st.description), h("td", { class: "small" }, st.action), h("td", { class: "small mono", style: { maxWidth: "260px", wordBreak: "break-all" } }, st.data_used || ""),
        h("td", {}, h("span", { class: st.status }, st.status), st.error ? h("div", { class: "small fail", style: { maxWidth: "300px", whiteSpace: "pre-wrap" } }, st.error.slice(0, 500)) : null),
        h("td", { class: "small", style: { maxWidth: "320px", whiteSpace: "pre-wrap", wordBreak: "break-word" } }, expectedActual(st)),
        h("td", {}, st.screenshot_path ? h("img", { class: "thumb", src: fileUrl(st.screenshot_path), loading: "lazy", onclick: () => lightbox(fileUrl(st.screenshot_path)) }) : ""))), { class: "steps" }))));
  });
}
/* The step log (run.log) of the run: one line per event, filtered by level / area / text. */
const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"];
function logPanel(main, path) {
  const body = h("div", {}), box = h("details", { class: "card" }, h("summary", {}, h("b", {}, "Run log"), " ", h("span", { class: "muted small", id: "logcount" }, "click to load")), body);
  let loaded = false;
  box.addEventListener("toggle", async () => {
    if (!box.open || loaded) return; loaded = true;
    let r; try { r = await GET("/api/result/log?" + q({ path })); } catch (e) { body.append(h("div", { class: "banner bad" }, e.message)); return; }
    if (!r.found) { body.append(h("div", { class: "muted small" }, "No run log was written for this run (it was made by an older testbot version).")); $("#logcount").textContent = "none"; return; }
    const counts = Object.fromEntries(LOG_LEVELS.map((l) => [l, r.lines.filter((x) => x.level === l).length]));
    const f = { min: "INFO", area: "", text: "" };
    const list = h("div", { class: "mono small", style: { maxHeight: "420px", overflow: "auto", background: "var(--bg)", padding: "6px 8px", borderRadius: "4px" } });
    const areas = [...new Set(r.lines.map((x) => x.area))].sort();
    const draw = () => {
      clear(list);
      const min = LOG_LEVELS.indexOf(f.min), t = f.text.toLowerCase();
      const rows = r.lines.filter((x) => LOG_LEVELS.indexOf(x.level) >= min && (!f.area || x.area === f.area) && (!t || x.msg.toLowerCase().includes(t)));
      rows.slice(0, 2500).forEach((x) => list.append(h("div", { style: { whiteSpace: "pre-wrap", wordBreak: "break-word", borderBottom: "1px solid var(--line)", padding: "1px 0" } },
        h("span", { class: "muted" }, x.t.slice(11) + " "), h("span", { class: x.level === "ERROR" ? "fail" : (x.level === "WARNING" ? "warn" : (x.level === "DEBUG" ? "muted" : "pass")), style: { display: "inline-block", width: "62px" } }, x.level),
        h("span", { class: "muted", style: { display: "inline-block", width: "70px" } }, x.area), x.msg)));
      if (!rows.length) list.append(h("div", { class: "muted" }, "No lines at this level / filter."));
      if (rows.length > 2500) list.append(h("div", { class: "muted" }, `…${rows.length - 2500} more lines – narrow the filter or download the file.`));
      $("#logshown").textContent = `${rows.length} of ${r.lines.length} lines`;
    };
    $("#logcount").textContent = `${r.lines.length} lines · ${counts.ERROR} error · ${counts.WARNING} warning`;
    body.append(h("div", { class: "toolbar" },
      h("label", { class: "small" }, "Show from level "), selectInput([["DEBUG", "Debug (everything)"], ["INFO", "Info"], ["WARNING", "Warning"], ["ERROR", "Error"]], f.min, (v) => { f.min = v; draw(); }),
      h("label", { class: "small" }, "Area "), selectInput([["", "all"], ...areas.map((a) => [a, a])], "", (v) => { f.area = v; draw(); }),
      h("input", { placeholder: "Search…", oninput: (e) => { f.text = e.target.value; draw(); } }),
      h("span", { class: "grow" }), h("span", { class: "muted small", id: "logshown" }),
      h("a", { href: "/files/results/" + r.file.split("/").map(encodeURIComponent).join("/"), target: "_blank" }, "open run.log")), list);
    draw();
  });
  main.append(box);
}
function expectedActual(st) {
  const fmt = (v) => (Array.isArray(v) ? v.join("\n") : (v === null || v === undefined ? "" : String(v)));
  if (st.expected === null && st.actual === null) return "";
  return [h("div", {}, h("span", { class: "muted" }, "expected: "), fmt(st.expected)), h("div", {}, h("span", { class: "muted" }, "actual: "), fmt(st.actual))];
}
async function trend(id) {
  const { runs } = await GET("/api/results"); const same = runs.filter((r) => r.id === id && r.kind !== "load").slice(0, 25).reverse();
  const box = $("#trend"); if (!box) return;
  box.append(h("div", { class: "bars" }, same.map((r) => h("i", { class: r.status === "pass" ? "" : "f", style: { height: "26px" }, title: `${fmtTime(r.started || r.modified)}: ${r.status}`, onclick: () => go("results", { path: r.path }) }))),
    h("div", { class: "small muted" }, `last ${same.length} run(s) of this test (green = pass)`));
}
async function loadDetail(main, r) {
  const d = r.data, s = d.settings || {};
  const { iterations } = await GET("/api/result/iterations?" + q({ dir: r.folder }));
  main.append(h("h1", {}, "Load run ", statusBadge(d.failed === 0 && d.iterations_run ? "pass" : "fail")),
    h("div", { class: "kv card" }, h("div", {}, "Settings"), h("div", {}, `${s.workers} worker(s), iterations ${s.iterations ?? "until duration"}, duration ${s.duration_s ?? "–"} s, ramp-up ${s.ramp_up_s} s`),
      h("div", {}, "Elapsed"), h("div", {}, d.elapsed_s + " s"), h("div", {}, "Runs"), h("div", {}, `${d.iterations_run} (${d.passed} passed, ${d.failed} failed) – ${d.iterations_per_min} per minute`),
      h("div", {}, "Iteration time"), h("div", {}, d.iteration_seconds ? `min ${d.iteration_seconds.min}s / avg ${d.iteration_seconds.avg}s / max ${d.iteration_seconds.max}s` : "")),
    h("h2", {}, "Workers"), table(["Worker", "Runs", "Pass", "Fail", "Error", "Total time (s)"], d.workers.map((w) => h("tr", {}, h("td", {}, w.worker), h("td", {}, w.iterations), h("td", {}, w.pass), h("td", {}, w.fail), h("td", {}, w.error), h("td", {}, w.seconds)))),
    h("h2", {}, "Every run"), table(["Worker", "Run", "Status", "Seconds", "First problem", ""], d.iterations.map((it) => {
      const detail = iterations.find((x) => x.dir.endsWith(`worker-${String(it.worker).padStart(2, "0")}/iter-${String(it.iteration).padStart(3, "0")}`));
      return h("tr", { class: detail ? "clickable" : "", onclick: () => detail && go("results", { path: detail.path }) }, h("td", {}, it.worker), h("td", {}, it.iteration), h("td", {}, statusBadge(it.status)), h("td", {}, it.seconds), h("td", { class: "small" }, it.error || ""), h("td", {}, detail ? "open ▸" : ""));
    })));
}


/* ------------------------------------------------------------------ results organised by suite / session / test case */
const resultTabs = (cur) => h("div", { class: "tabs" }, [["run", "By run"], ["suite", "By suite"], ["session", "By session"], ["case", "By test case"]].map(([k, l]) =>
  h("button", { class: cur === k ? "active" : "", onclick: () => go("results", { view: k }) }, l)));
const resultFileUrl = (folder, rel) => "/files/results/" + (folder ? folder.split("/").map(encodeURIComponent).join("/") + "/" : "") + rel.split("/").map(encodeURIComponent).join("/");
const barsOf = (statuses, h_ = 20) => h("div", { class: "bars", style: { height: h_ + "px" } }, (statuses || []).map((s) => h("i", { class: s === "pass" ? "" : "f", style: { height: h_ + "px" }, title: s })));
const folderOf = (p) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");
const sinceOptions = [["", "any time"], ["1", "last 24 hours"], ["7", "last 7 days"], ["30", "last 30 days"]];
function recentEnough(when, days) { if (!days) return true; const d = new Date(when); return !isNaN(d) && (Date.now() - d.getTime()) <= Number(days) * 86400000; }
function whereLabel(o) { return o.run_kind === "session" ? `session ${o.session_id}${o.entry ? ", entry " + o.entry : ""}` : "suite run"; }
const finalLine = (o) => h("span", {}, fmtTime(o.when), o.duration_ms ? h("span", { class: "muted small" }, `  (${(o.duration_ms / 1000).toFixed(1)} s)`) : null);

/* the lists: one row per suite / session / test case, with the FINAL result and the final test time */
async function summaryView(main, view) {
  const [{ rows }, st] = await Promise.all([GET("/api/results/view?" + q({ view })), GET("/api/state")]);
  const f = { text: "", status: "", since: "", sort: "when" };
  const heads = { case: ["Test case", "Final result", "Final test (date / time)", "Runs", "Recent", "Problem in the final run"],
                  suite: ["Test suite", "Final result", "Final test (date / time)", "Runs", "Recent", "Test cases (final results)"],
                  session: ["Session", "Final result", "Final test (date / time)", "Runs", "Recent", "Last run"] }[view];
  const tbl = table(heads, []), body = tbl.querySelector("tbody"), count = h("span", { class: "muted small" });
  const open = (r) => (view === "case" ? go("results", { suite: r.suite_id, case: r.case_id }) : (view === "suite" ? go("results", { suite: r.suite_id }) : go("results", { session: r.session_id })));
  const draw = () => {
    clear(body);
    const text = f.text.toLowerCase();
    let list = rows.filter((r) => (!f.status || (f.status === "error" ? !["pass", "fail"].includes(r.status) : r.status === f.status)) && recentEnough(r.when, f.since)
      && (!text || `${r.suite_id} ${r.case_id || ""} ${r.title || ""} ${r.session_id || ""} ${r.name || ""}`.toLowerCase().includes(text)));
    const key = { when: (r) => -(new Date(r.when).getTime() || 0), name: (r) => `${r.suite_id || ""}/${r.case_id || r.session_id || ""}`, result: (r) => (r.status === "pass" ? 1 : 0) };
    list = [...list].sort((a, b) => (key[f.sort](a) > key[f.sort](b) ? 1 : -1));
    count.textContent = `${list.length} of ${rows.length}`;
    if (!list.length) body.append(h("tr", {}, h("td", { colspan: 6, class: "empty" }, rows.length ? "Nothing matches the filter." : "No results yet. Run a test, then refresh.")));
    list.forEach((r) => body.append(h("tr", { class: "clickable", onclick: () => open(r) },
      view === "case" ? h("td", {}, h("b", {}, r.case_id), h("div", { class: "small muted" }, `${r.suite_id} — ${r.title || ""}`)) :
        h("td", {}, h("b", {}, view === "suite" ? r.suite_id : r.session_id), view === "session" && r.name ? h("div", { class: "small muted" }, r.name) : null),
      h("td", {}, statusBadge(r.status)),
      h("td", { class: "small" }, fmtTime(r.when), view === "case" ? h("div", { class: "muted" }, whereLabel(r) + (r.occurrence > 1 ? ` (run #${r.occurrence})` : "")) : (view === "suite" && r.kind === "session" ? h("div", { class: "muted" }, "session " + r.session_id) : null)),
      h("td", { class: "small" }, view === "case" ? `${r.runs} (${r.passed} pass${r.failed ? ", " + r.failed + " fail" : ""}${r.errors ? ", " + r.errors + " error" : ""})` : String(r.runs)),
      h("td", {}, barsOf(r.recent)),
      h("td", { class: "small" }, view === "case" ? (r.status === "pass" ? "" : r.problem || "") :
        (view === "suite" ? `${r.cases_pass} pass${r.cases_fail ? ", " + r.cases_fail + " fail" : ""}${r.cases_error ? ", " + r.cases_error + " error" : ""} of ${r.cases}` :
          `${r.entries} entr${r.entries === 1 ? "y" : "ies"}, ${r.passed}/${r.cases} cases passed`)))));
  };
  main.append(h("h1", {}, "Results"), resultTabs(view),
    h("p", { class: "muted small" }, { case: "One row per test case: its final result is the one from the most recent run that included it (suite runs and sessions together). Click a row for its runs, sessions and step results.",
      suite: "One row per test suite: the most recent run of the suite (on its own or inside a session). Click a row for its test cases and runs.",
      session: "One row per session: the most recent run of the session. Click a row for its runs." }[view]),
    h("div", { class: "toolbar" }, h("input", { placeholder: "Filter…", oninput: (e) => { f.text = e.target.value; draw(); } }),
      selectInput([["pass", "pass"], ["fail", "fail"], ["error", "error / inconclusive"]], "", (v) => { f.status = v; draw(); }, { blank: "any final result" }),
      selectInput(sinceOptions, "", (v) => { f.since = v; draw(); }), selectInput([["when", "newest first"], ["name", "by name"], ["result", "failures first"]], "when", (v) => { f.sort = v; draw(); }),
      h("button", { onclick: () => go("results", { view, r: Date.now() }) }, "Refresh"), count, h("span", { class: "grow" }),
      h("span", { class: "muted small" }, "Folder: ", h("span", { class: "mono" }, st.dirs.results.path))),
    h("div", { class: "card", style: { padding: 0 } }, tbl));
  draw();
}

/* the steps of one test case in one run (same columns as the run page) */
function stepsTable(c, fileUrl) {
  return table(["#", "Description", "Action", "Data used", "Status", "Expected / actual", "Evidence"], c.steps.map((st) => h("tr", {},
    h("td", {}, st.step_no), h("td", {}, st.description), h("td", { class: "small" }, st.action), h("td", { class: "small mono", style: { maxWidth: "260px", wordBreak: "break-all" } }, st.data_used || ""),
    h("td", {}, h("span", { class: st.status }, st.status), st.error ? h("div", { class: "small fail", style: { maxWidth: "300px", whiteSpace: "pre-wrap" } }, st.error.slice(0, 500)) : null),
    h("td", { class: "small", style: { maxWidth: "320px", whiteSpace: "pre-wrap", wordBreak: "break-word" } }, expectedActual(st)),
    h("td", {}, st.screenshot_path ? h("img", { class: "thumb", src: fileUrl(st.screenshot_path), loading: "lazy", onclick: () => lightbox(fileUrl(st.screenshot_path)) }) : ""))), { class: "steps" });
}

const backLink = (label, view) => h("div", { class: "toolbar" }, h("a", { href: "#", onclick: (e) => { e.preventDefault(); go("results", { view }); } }, "← " + label));

/* one test case: final result, every run (standalone and in sessions), the sessions that include it, and the step results of the chosen run */
async function caseDetail(main, suite, caseId) {
  let d;
  try { d = await GET("/api/results/case?" + q({ suite, case: caseId })); } catch (e) { main.append(backLink("Results: by test case", "case"), h("div", { class: "banner bad" }, e.message)); return; }
  const f = { status: "", where: "" }, detail = h("div", {}), runsTbl = table(["Date / time", "Result", "Duration", "Where it ran", "Selection", "Problem"], []), body = runsTbl.querySelector("tbody");
  let chosen = null;
  const fin = d.final;
  main.append(backLink("Results: by test case", "case"),
    h("h1", {}, d.case_id, " ", statusBadge(fin.status)),
    h("div", { class: "muted", style: { marginBottom: "8px" } }, d.title + " · suite ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); go("results", { suite: d.suite_id }); } }, d.suite_id)),
    h("div", { class: "kv card" }, h("div", {}, "Final result"), h("div", {}, statusBadge(fin.status), "  ", fin.problem ? h("span", { class: "small fail" }, fin.problem) : ""),
      h("div", {}, "Final test"), h("div", {}, finalLine(fin), h("span", { class: "muted small" }, "  · " + whereLabel(fin))),
      h("div", {}, "All runs"), h("div", {}, `${d.counts.runs} (${d.counts.passed} pass, ${d.counts.failed} fail, ${d.counts.errors} error / inconclusive)`),
      h("div", {}, "Recent"), h("div", {}, barsOf(d.recent, 26))));

  const sessionIds = [...new Set(d.occurrences.map((o) => o.session_id).filter(Boolean))];
  const drawRuns = () => {
    clear(body);
    const list = d.occurrences.filter((o) => (!f.status || (f.status === "error" ? !["pass", "fail"].includes(o.status) : o.status === f.status)) && (!f.where || (f.where === "_suite" ? o.run_kind === "suite" : o.session_id === f.where)));
    if (!list.length) body.append(h("tr", {}, h("td", { colspan: 6, class: "empty" }, "No run matches the filter.")));
    list.forEach((o) => body.append(h("tr", { class: "clickable" + (chosen === o ? " active" : ""), onclick: () => { chosen = o; drawRuns(); openRun(o); } },
      h("td", { class: "small" }, fmtTime(o.when)), h("td", {}, statusBadge(o.status)), h("td", { class: "small" }, o.duration_ms ? (o.duration_ms / 1000).toFixed(1) + " s" : ""),
      h("td", { class: "small" }, badge(o.run_kind), " ", o.run_kind === "session" ? `${o.session_id}${o.session_name && o.session_name !== o.session_id ? " — " + o.session_name : ""}, entry ${o.entry}` : o.run_id,
        o.occurrence > 1 ? h("span", { class: "muted" }, ` (run #${o.occurrence} in that run)`) : null),
      h("td", { class: "small muted" }, o.selected ? `selected ${o.selected.length} of ${o.cases_in_file}` : "whole suite", o.revision != null ? h("div", {}, `rev ${o.revision}${o.suite_revision ? " · " + o.suite_revision : ""}`) : null),
      h("td", { class: "small" }, o.status === "pass" ? "" : o.problem || ""))));
  };
  async function openRun(o) {
    clear(detail).append(h("span", { class: "muted" }, "Loading…"));
    let r; try { r = await GET("/api/result/case?" + q({ path: o.run_path, suite: o.suite_id, case: o.case_id, ...(o.occurrence > 1 ? { occ: o.occurrence } : {}), ...(o.run_kind === "session" && o.entry ? { entry: o.entry } : {}) })); }
    catch (e) { clear(detail).append(h("div", { class: "banner bad" }, e.message)); return; }
    const url = (rel) => resultFileUrl(r.folder, rel), c = r.case;
    clear(detail).append(h("div", { class: "card" },
      h("h3", { style: { marginTop: 0 } }, statusBadge(c.status), " ", c.case_id, " — ", c.title, " ", h("span", { class: "muted small" }, `${fmtTime(c.finished_at || o.when)} · ${(c.duration_ms / 1000).toFixed(1)} s · ${c.steps.length} steps`)),
      h("div", { class: "toolbar" }, h("span", { class: "small muted" }, `${r.run.kind === "session" ? "Session" : "Suite run"} ${r.run.id}${r.run.entry ? ", entry " + r.run.entry : ""} · started ${fmtTime(r.run.started)} · finished ${fmtTime(r.run.finished)}`), h("span", { class: "grow" }),
        h("button", { onclick: () => go("results", { path: r.run.path }) }, "Open the full run"),
        r.html_report ? h("a", { href: url(r.html_report), target: "_blank" }, "HTML report") : null,
        h("button", { onclick: async () => { try { const l = await GET("/api/result/log?" + q({ path: r.run.path })); if (l.found) window.open("/files/results/" + l.file.split("/").map(encodeURIComponent).join("/"), "_blank"); else toast("No run log was written for this run", ""); } catch (e) { fail(e); } } }, "Run log")),
      stepsTable(c, url)));
  }
  main.append(h("h2", {}, "Runs of this test case"), h("div", { class: "toolbar" },
    selectInput([["pass", "pass"], ["fail", "fail"], ["error", "error / inconclusive"]], "", (v) => { f.status = v; drawRuns(); }, { blank: "any result" }),
    selectInput([["_suite", "standalone suite runs"], ...sessionIds.map((s) => [s, "session " + s])], "", (v) => { f.where = v; drawRuns(); }, { blank: "everywhere it ran" }),
    h("span", { class: "muted small" }, "Click a run to see its step results below.")),
    h("div", { class: "card", style: { padding: 0 } }, runsTbl), detail);
  chosen = d.occurrences[0]; drawRuns(); openRun(chosen);

  main.append(groupsSection(d.groups, "Test groups that include this test case"), h("h2", {}, "Sessions that include this test case"),
    d.sessions.length ? h("div", { class: "card", style: { padding: 0 } }, table(["Session", "In entry", "Latest run of the session", "When", ""], d.sessions.map((s) => h("tr", {},
      h("td", {}, h("b", {}, s.session_id), h("div", { class: "small muted" }, `${s.name || ""} · ${s.path}`)), h("td", { class: "small" }, s.entries.map((e) => `#${e.entry}${e.selection ? " (" + e.selection.join(", ") + ")" : " (whole suite)"}`).join("; ")),
      h("td", {}, s.last_status ? statusBadge(s.last_status) : h("span", { class: "muted small" }, "never run")), h("td", { class: "small" }, fmtTime(s.last_when)),
      h("td", {}, h("button", { onclick: () => go("sessions", { path: s.path }) }, "Open session"), " ", s.last_run_path ? h("button", { onclick: () => go("results", { path: s.last_run_path }) }, "Last run") : null))))) :
      h("div", { class: "muted small" }, "No saved session includes this test case."));
}

/* one suite: final result, its test cases (final results) and its runs */
async function suiteDetail(main, suite) {
  let d;
  try { d = await GET("/api/results/suite?" + q({ suite })); } catch (e) { main.append(backLink("Results: by test suite", "suite"), h("div", { class: "banner bad" }, e.message)); return; }
  const fin = d.final;
  const ana = h("div", {}); analysisPanel(ana, "suite", d.suite_id);
  main.append(backLink("Results: by test suite", "suite"), h("h1", {}, d.suite_id, " ", statusBadge(fin.status)),
    h("div", { class: "kv card" }, h("div", {}, "Final test"), h("div", {}, fmtTime(fin.when), h("span", { class: "muted small" }, `  · ${fin.kind === "session" ? "session " + fin.session_id + ", entry " + fin.entry : "suite run"} · ${fin.duration_s} s`)),
      h("div", {}, "Final result"), h("div", {}, `${fin.passed}/${fin.cases} case(s) passed` + (fin.selected ? ` (selected ${fin.selected.length} of ${fin.cases_in_file})` : "")),
      h("div", {}, "All runs"), h("div", {}, String(d.runs.length)), h("div", {}, "Environment"), h("div", {}, fin.environment || "")),
    ana, groupsSection(d.groups, "Test groups that include this suite"),
    h("h2", {}, "Test cases (final result of each)"),
    h("div", { class: "card", style: { padding: 0 } }, table(["Test case", "Final result", "Final test", "Runs", "Recent", "Problem"], d.cases.map((c) => h("tr", { class: "clickable", onclick: () => go("results", { suite: c.suite_id, case: c.case_id }) },
      h("td", {}, h("b", {}, c.case_id), h("div", { class: "small muted" }, c.title)), h("td", {}, statusBadge(c.status)), h("td", { class: "small" }, fmtTime(c.when), h("div", { class: "muted" }, whereLabel(c))),
      h("td", { class: "small" }, String(c.runs)), h("td", {}, barsOf(c.recent)), h("td", { class: "small" }, c.status === "pass" ? "" : c.problem || ""))))),
    h("h2", {}, "Runs of this suite"),
    h("div", { class: "card", style: { padding: 0 } }, table(["Date / time", "Result", "Where", "Cases", "Duration", ""], d.runs.map((r) => h("tr", { class: "clickable", onclick: () => go("results", { path: r.path }) },
      h("td", { class: "small" }, fmtTime(r.when)), h("td", {}, statusBadge(r.status)), h("td", { class: "small" }, r.kind === "session" ? `session ${r.session_id}, entry ${r.entry}` : "suite run"),
      h("td", { class: "small" }, `${r.passed}/${r.cases} passed` + (r.selected ? ` · selected ${r.selected.length} of ${r.cases_in_file}` : "")), h("td", { class: "small muted" }, r.duration_s + " s"), h("td", { class: "small muted" }, r.path))))));
}

/* one session: final result and its runs */
async function sessionDetail(main, session) {
  let d;
  try { d = await GET("/api/results/session?" + q({ session })); } catch (e) { main.append(backLink("Results: by session", "session"), h("div", { class: "banner bad" }, e.message)); return; }
  const fin = d.final;
  const ana = h("div", {}); analysisPanel(ana, "session", d.session_id);
  main.append(backLink("Results: by session", "session"), h("h1", {}, d.session_id, " ", statusBadge(fin.status)), d.name ? h("div", { class: "muted", style: { marginBottom: "8px" } }, d.name) : null,
    h("div", { class: "kv card" }, h("div", {}, "Final test"), h("div", {}, fmtTime(fin.when), h("span", { class: "muted small" }, `  · ${fin.duration_s} s`)),
      h("div", {}, "Final result"), h("div", {}, `${fin.passed}/${fin.cases} case(s) passed`), h("div", {}, "All runs"), h("div", {}, String(d.runs.length)),
      h("div", {}, "Open"), h("div", {}, h("button", { onclick: () => go("results", { path: fin.path }) }, "The latest run in detail"))),
    ana,
    h("h2", {}, "Runs of this session"),
    h("div", { class: "card", style: { padding: 0 } }, table(["Date / time", "Result", "Cases", "Duration", ""], d.runs.map((r) => h("tr", { class: "clickable", onclick: () => go("results", { path: r.path }) },
      h("td", { class: "small" }, fmtTime(r.when)), h("td", {}, statusBadge(r.status)), h("td", { class: "small" }, `${r.passed}/${r.cases} passed${r.failed ? ", " + r.failed + " failed" : ""}${r.errors ? ", " + r.errors + " error" : ""}`),
      h("td", { class: "small muted" }, r.duration_s + " s"), h("td", { class: "small muted" }, r.path))))));
}
