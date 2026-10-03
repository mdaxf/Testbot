"use strict";
/* Results: list of runs found in the results folder, and a step-level viewer with screenshots. */

routes.results = async (main, params) => {
  if (params.path) return resultDetail(main, params.path);
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
  main.append(h("h1", {}, "Results"), h("div", { class: "toolbar" },
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
      h("div", {}, "Result"), h("div", {}, `${passed} of ${all.length} case(s) passed`), h("div", {}, "Files"), h("div", {},
        r.html_report ? h("a", { href: fileUrl(r.html_report), target: "_blank" }, "HTML report") : null, " ", h("span", { class: "mono small" }, r.path)),
      h("div", {}, "Trend"), h("div", { id: "trend" })));
  trend(r.data.session_id || r.data.suite_id);
  logPanel(main, path);
  suites.forEach((s) => {
    if (r.kind === "session") main.append(h("h2", {}, "File: " + s.suite_id));
    s.cases.forEach((c) => main.append(h("details", { class: "card", open: c.status !== "pass" },
      h("summary", {}, statusBadge(c.status), " ", h("b", {}, c.case_id), " — ", c.title, " ", h("span", { class: "muted small" }, (c.duration_ms / 1000).toFixed(1) + " s, " + c.steps.length + " steps")),
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
