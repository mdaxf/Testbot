"use strict";
/* Test groups: sessions, suites and test cases tracked together over a time window (for example a UAT cycle), with charts.
   The charts are plain SVG drawn here (no library: the manager must work offline); each can be downloaded as SVG or PNG. */

const STATUS_ORDER = ["pass", "fail", "error", "inconclusive", "not_run"];
const STATUS_COLOR = { pass: "#1a7f37", fail: "#cf222e", error: "#bc4c00", inconclusive: "#9a6700", not_run: "#8c959f" };
const STATUS_TEXT = { pass: "Pass", fail: "Fail", error: "Error", inconclusive: "Inconclusive", not_run: "Not run" };
const SVGNS = "http://www.w3.org/2000/svg";
const resultBadge = (s) => (s === "not_run" ? badge("not run", "") : statusBadge(s));

function svg(tag, attrs = {}, ...kids) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  kids.flat().forEach((c) => { if (c !== null && c !== undefined) e.append(c.nodeType ? c : document.createTextNode(String(c))); });
  return e;
}
const svgRoot = (w, hgt, ...kids) => svg("svg", { xmlns: SVGNS, width: w, height: hgt, viewBox: `0 0 ${w} ${hgt}`, "font-family": "Segoe UI, Arial, sans-serif" }, ...kids);

function legend(counts, onPick, withCounts = true) {
  return h("div", { class: "small", style: { display: "flex", flexWrap: "wrap", gap: "4px 14px", marginTop: "6px" } }, STATUS_ORDER.map((s) =>
    h("span", { style: { cursor: onPick ? "pointer" : "default" }, onclick: () => onPick && onPick(s), title: onPick ? "Show these test cases" : "" },
      h("span", { style: { display: "inline-block", width: "10px", height: "10px", background: STATUS_COLOR[s], marginRight: "4px", borderRadius: "2px" } }), withCounts ? `${STATUS_TEXT[s]} ${counts[s] || 0}` : STATUS_TEXT[s])));
}

/* donut of the final results; clicking a slice (or its legend entry) calls onPick(status) */
function donutChart(counts, onPick, size = 190) {
  const total = STATUS_ORDER.reduce((n, s) => n + (counts[s] || 0), 0), c = size / 2, r = size / 2 - 8, r2 = r * 0.58, parts = [];
  let a0 = -Math.PI / 2;
  STATUS_ORDER.forEach((s) => {
    const n = counts[s] || 0; if (!n) return;
    const a1 = a0 + (2 * Math.PI * n) / (total || 1), pt = (rad, a) => `${(c + rad * Math.cos(a)).toFixed(1)},${(c + rad * Math.sin(a)).toFixed(1)}`;
    const el = n === total ? svg("circle", { cx: c, cy: c, r: (r + r2) / 2, fill: "none", stroke: STATUS_COLOR[s], "stroke-width": r - r2 })
      : svg("path", { d: `M${pt(r, a0)} A${r},${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${pt(r, a1)} L${pt(r2, a1)} A${r2},${r2} 0 ${a1 - a0 > Math.PI ? 1 : 0} 0 ${pt(r2, a0)} Z`, fill: STATUS_COLOR[s] });
    el.style.cursor = onPick ? "pointer" : "default"; el.addEventListener("click", () => onPick && onPick(s)); el.append(svg("title", {}, `${STATUS_TEXT[s]}: ${n}`)); parts.push(el); a0 = a1;
  });
  if (!total) parts.push(svg("circle", { cx: c, cy: c, r: (r + r2) / 2, fill: "none", stroke: "#d0d7de", "stroke-width": r - r2 }));
  parts.push(svg("text", { x: c, y: c + 2, "text-anchor": "middle", "font-size": 26, fill: "#24292f" }, total ? Math.round((100 * (counts.pass || 0)) / total) + "%" : "–"),
    svg("text", { x: c, y: c + 20, "text-anchor": "middle", "font-size": 11, fill: "#57606a" }, total ? `${counts.pass || 0} of ${total} passed` : "no test cases"));
  return h("div", {}, svgRoot(size, size, ...parts), legend(counts, onPick));
}

/* stacked columns per day: the final result of every case at the end of each day (progress), or the executions of the day */
function dailyChart(daily, keys, colors, labels, { width = 620, height = 220 } = {}) {
  if (!daily.length) return h("div", { class: "muted small" }, "No days to show.");
  const left = 34, bottom = 26, top = 14, bw = Math.max(5, Math.min(44, (width - left - 8) / daily.length - 4)), step = bw + 4;
  const w = Math.max(width, left + daily.length * step + 8), totals = daily.map((d) => keys.reduce((n, k) => n + (d[k] || 0), 0)), max = Math.max(1, ...totals), parts = [];
  parts.push(svg("line", { x1: left, y1: height - bottom, x2: w, y2: height - bottom, stroke: "#d0d7de" }), svg("text", { x: 2, y: top + 8, "font-size": 10, fill: "#57606a" }, String(max)), svg("text", { x: 2, y: height - bottom, "font-size": 10, fill: "#57606a" }, "0"));
  daily.forEach((d, i) => {
    let y = height - bottom; const x = left + i * step;
    keys.forEach((k) => {
      const v = d[k] || 0, hh = ((height - bottom - top) * v) / max; if (hh <= 0) return;
      y -= hh; parts.push(svg("rect", { x, y, width: bw, height: hh, fill: colors[k], rx: 1 }, svg("title", {}, `${d.date}: ${labels[k]} ${v}`)));
    });
    if (daily.length <= 14 || i % Math.ceil(daily.length / 12) === 0) parts.push(svg("text", { x: x + bw / 2, y: height - 10, "text-anchor": "middle", "font-size": 9, fill: "#57606a" }, d.date.slice(5)));
  });
  return h("div", { style: { overflowX: "auto" } }, svgRoot(w, height, ...parts), h("div", { class: "small", style: { display: "flex", gap: "14px", marginTop: "4px", flexWrap: "wrap" } },
    keys.map((k) => h("span", {}, h("span", { style: { display: "inline-block", width: "10px", height: "10px", background: colors[k], marginRight: "4px", borderRadius: "2px" } }), labels[k]))));
}
const progressChart = (daily) => dailyChart(daily, STATUS_ORDER, STATUS_COLOR, STATUS_TEXT);
const runsChart = (daily) => dailyChart(daily, ["ran_pass", "ran_fail", "ran_other"], { ran_pass: STATUS_COLOR.pass, ran_fail: STATUS_COLOR.fail, ran_other: STATUS_COLOR.error },
  { ran_pass: "Passed", ran_fail: "Failed", ran_other: "Error / inconclusive" }, { height: 170 });

/* one horizontal stacked bar per member (session / suite / cases) */
function memberChart(rows) {
  if (!rows.length) return h("div", { class: "muted small" }, "No members.");
  const w = 640, rowH = 26, left = 250, parts = [];
  rows.forEach((m, i) => {
    const y = 6 + i * rowH, total = m.total || 1; let x = left;
    parts.push(svg("text", { x: left - 8, y: y + 14, "text-anchor": "end", "font-size": 11, fill: "#24292f" }, m.label.length > 40 ? m.label.slice(0, 38) + "…" : m.label, svg("title", {}, m.label)));
    STATUS_ORDER.forEach((s) => {
      const v = s === "error" ? (m.error || 0) + (m.inconclusive || 0) : (s === "inconclusive" ? 0 : m[s] || 0), ww = ((w - left - 40) * v) / total; if (ww <= 0) return;
      parts.push(svg("rect", { x, y, width: ww, height: 18, fill: STATUS_COLOR[s] }, svg("title", {}, `${STATUS_TEXT[s]}: ${v}`))); x += ww;
    });
    parts.push(svg("text", { x: x + 6, y: y + 14, "font-size": 11, fill: "#57606a" }, `${m.pass || 0}/${m.total}`));
  });
  return h("div", {}, svgRoot(w, rows.length * rowH + 10, ...parts), legend({}, null, false));
}

function saveBlob(blob, name) { const a = h("a", { href: URL.createObjectURL(blob), download: name }); document.body.appendChild(a); a.click(); a.remove(); }
function downloadChart(node, name, fmt) {
  const s = node.querySelector("svg"); if (!s) return;
  const xml = new XMLSerializer().serializeToString(s);
  if (fmt === "svg") return saveBlob(new Blob([xml], { type: "image/svg+xml" }), name + ".svg");
  const img = new Image(), w = Number(s.getAttribute("width")), hh = Number(s.getAttribute("height"));
  img.onload = () => { const c = document.createElement("canvas"); c.width = w * 2; c.height = hh * 2; const x = c.getContext("2d"); x.fillStyle = "#ffffff"; x.fillRect(0, 0, c.width, c.height); x.drawImage(img, 0, 0, c.width, c.height); c.toBlob((b) => b && saveBlob(b, name + ".png")); };
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(xml);
}
function chartCard(title, node, fileName, note) {
  return h("div", { class: "card" }, h("div", { class: "toolbar", style: { margin: "0 0 6px" } }, h("b", {}, title), h("span", { class: "grow" }),
    h("button", { class: "ghost", title: "Download as an image (PNG)", onclick: () => downloadChart(node, fileName, "png") }, "PNG"), h("button", { class: "ghost", title: "Download as a vector image", onclick: () => downloadChart(node, fileName, "svg") }, "SVG")),
    note ? h("div", { class: "muted small", style: { marginBottom: "4px" } }, note) : null, node);
}

/* ------------------------------------------------------------------ analysis of one suite / session over the last N days (used on their result pages) */
function analysisPanel(main, kind, id) {
  const f = { days: "30" }, box = h("div", {});
  const draw = async () => {
    clear(box).append(h("span", { class: "muted" }, "Loading…"));
    let t; try { t = await GET("/api/results/analysis?" + q({ kind, id, days: f.days })); } catch (e) { clear(box).append(h("div", { class: "banner bad" }, e.message)); return; }
    clear(box);
    if (!t.total) { box.append(h("div", { class: "muted small" }, `No test case of this ${kind} ran in the last ${f.days} days.`)); return; }
    const donut = donutChart(t.counts, null), prog = progressChart(t.daily), runs = runsChart(t.daily);
    box.append(h("div", { class: "grid g2" }, chartCard("Final results of its test cases", donut, `${kind}-${id}-final-results`, `${t.total} test case(s); each counted by its latest result in the last ${f.days} days.`),
      chartCard("Results by day", prog, `${kind}-${id}-by-day`, "The final result of each test case at the end of each day.")), chartCard("Executions per day", runs, `${kind}-${id}-executions`));
  };
  main.append(h("h2", {}, "Analysis"), h("div", { class: "toolbar" }, h("label", { class: "small muted" }, "Time range: "),
    selectInput([["7", "last 7 days"], ["30", "last 30 days"], ["90", "last 90 days"], ["180", "last 180 days"]], f.days, (v) => { f.days = v; draw(); })), box);
  draw();
}

/* ------------------------------------------------------------------ test groups */
const dateRange = (g) => (g.start || g.end ? `${g.start || "…"} → ${g.end || "today"}` : "all time");
const miniBar = (counts, total) => h("div", { style: { display: "flex", height: "10px", width: "140px", background: "#eaeef2", borderRadius: "3px", overflow: "hidden" }, title: STATUS_ORDER.map((s) => `${STATUS_TEXT[s]} ${counts[s] || 0}`).join(", ") },
  STATUS_ORDER.map((s) => (counts[s] ? h("div", { style: { width: (100 * counts[s]) / (total || 1) + "%", background: STATUS_COLOR[s] } }) : null)));

routes.groups = async (main, params) => {
  if (params.id) return groupPage(main, params.id, params.tab || "overview");
  if (params.new) return groupEditor(main, null);
  const { groups } = await GET("/api/groups");
  main.append(h("h1", {}, "Test groups"), h("p", { class: "muted" }, "A test group is a set of sessions, test suites and test cases that you track together — for example a UAT cycle of 100 test cases that starts on Monday. Open a group to see how far it is: final results, progress by day, what has not run, what is failing."),
    h("div", { class: "toolbar" }, h("button", { class: "primary", onclick: () => go("groups", { new: 1 }) }, "+ New test group")),
    h("div", { class: "card", style: { padding: 0 } }, table(["Group", "Members", "Period", "Test cases", "Progress", "Executed / passed", ""], groups.length ? groups.map((g) => h("tr", { class: "clickable", onclick: () => go("groups", { id: g.id }) },
      h("td", {}, h("b", {}, g.name), g.closed ? h("span", { style: { marginLeft: "6px" } }, badge("closed", "ok")) : null, g.frozen ? h("span", { style: { marginLeft: "6px" } }, badge("frozen list", "")) : null, h("div", { class: "small muted" }, g.broken ? "⚠ " + g.broken : (g.description || g.id))),
      h("td", { class: "small" }, String(g.members || 0)), h("td", { class: "small" }, dateRange(g), g.environment ? h("div", { class: "muted" }, "environment " + g.environment) : null), h("td", {}, String(g.total)),
      h("td", {}, miniBar(g.counts, g.total)), h("td", { class: "small" }, g.total ? `${g.pct_executed}% / ${g.pct_pass}%` : ""),
      h("td", { onclick: (e) => e.stopPropagation(), style: { whiteSpace: "nowrap" } },
        h("button", { class: "icon", title: "Duplicate", onclick: async () => { const n = await promptBox("Duplicate the group", "New group id", g.id + "-copy"); if (!n) return; try { await POST("/api/group/duplicate", { id: g.id, new_id: n }); toast("Duplicated", "ok"); go("groups", { id: n }); } catch (e) { fail(e); } } }, "⧉"),
        h("button", { class: "icon danger", title: "Delete the group (tests and results are not touched)", onclick: async () => { if (await confirmBox("Delete group", `Delete the group “${g.name}”? Test cases and results are not touched.`, "Delete", true)) { try { await DEL("/api/group?" + q({ id: g.id })); go("groups", { r: Date.now() }); } catch (e) { fail(e); } } } }, "🗑")))) :
      [h("tr", {}, h("td", { colspan: 7, class: "empty" }, "No test groups yet. Create one to track a test effort."))])));
};

async function groupPage(main, id, tab) {
  let data;
  try { data = await GET("/api/group/track?" + q({ id })); } catch (e) { main.append(backLinkTo("Test groups", () => go("groups")), h("div", { class: "banner bad" }, e.message)); return; }
  const g = data.group, t = data.track;
  const tabs = h("div", { class: "tabs" }, [["overview", "Progress & results"], ["settings", "Members & settings"]].map(([k, l]) => h("button", { class: tab === k ? "active" : "", onclick: () => go("groups", { id, tab: k }) }, l)));
  main.append(backLinkTo("Test groups", () => go("groups")), h("h1", {}, g.name, " ", g.closed ? badge("closed " + fmtTime(g.closed.at), "ok") : badge("open", ""), g.frozen ? " " : "", g.frozen ? badge("frozen list", "") : null),
    h("div", { class: "muted", style: { marginBottom: "6px" } }, `${dateRange(g)}${g.environment ? " · environment " + g.environment : ""}${g.description ? " · " + g.description : ""}`), tabs);
  if (tab === "settings") return groupEditor(main, g, true);

  const f = { status: "", text: "" }, tbl = table(["Test case", "Final result", "Final test", "Attempts", "Member", "Problem / manual note", ""], []), body = tbl.querySelector("tbody"), shown = h("span", { class: "muted small" });
  const donut = donutChart(t.counts, (s) => { f.status = f.status === s ? "" : s; drawCases(); statusSel.value = f.status; });
  const statusSel = selectInput([["not_run", "not run"], ["fail", "fail"], ["error", "error"], ["inconclusive", "inconclusive"], ["pass", "pass"], ["retested", "retested (failed, then passed)"], ["todo", "not passed yet"]], f.status, (v) => { f.status = v; drawCases(); }, { blank: "all test cases" });
  const recordManual = (c) => {
    const m = { status: "pass", by: localStorage.getItem("tb_tester") || "", comment: "" };
    modal({ title: `Record a manual result: ${c.suite_id} / ${c.case_id}`, body: h("div", {}, h("p", { class: "muted small" }, "Use this when the test case was tested by hand. It counts like a run: the latest result wins."),
        field("Result", selectInput([["pass", "Pass"], ["fail", "Fail"]], m.status, (v) => (m.status = v))), field("Tested by", textInput(m, "by")), field("Comment (what you saw)", areaInput(m, "comment", { rows: 3 }))),
      buttons: [{ label: "Cancel" }, { label: "Record", primary: true, onclick: async () => { try { localStorage.setItem("tb_tester", m.by); } catch (e) { /* private window */ }
        try { await POST("/api/group/manual", { id, suite_id: c.suite_id, case_id: c.case_id, ...m }); toast("Recorded", "ok"); go("groups", { id, r: Date.now() }); } catch (e) { fail(e); return false; } } }] });
  };
  function drawCases() {
    clear(body); const txt = f.text.toLowerCase();
    const list = t.cases.filter((r) => (!f.status || (f.status === "retested" ? t.retested.some((x) => x.case_id === r.case_id && x.suite_id === r.suite_id) : (f.status === "todo" ? r.status !== "pass" : r.status === f.status)))
      && (!txt || `${r.suite_id} ${r.case_id} ${r.title} ${r.sources.join(" ")}`.toLowerCase().includes(txt)));
    shown.textContent = `${list.length} of ${t.cases.length}`;
    if (!list.length) body.append(h("tr", {}, h("td", { colspan: 7, class: "empty" }, "No test case matches.")));
    list.forEach((r) => body.append(h("tr", { class: "clickable", onclick: () => r.attempts ? go("results", { suite: r.suite_id, case: r.case_id }) : null },
      h("td", {}, h("b", {}, r.case_id), h("div", { class: "small muted" }, `${r.suite_id} — ${r.title}`)), h("td", {}, resultBadge(r.status), r.kind === "manual" ? h("span", { class: "muted small", style: { marginLeft: "4px" } }, "manual") : null),
      h("td", { class: "small" }, fmtTime(r.when), r.run_kind ? h("div", { class: "muted" }, r.run_kind === "session" ? `session ${r.session_id}, entry ${r.entry}` : "suite run") : null),
      h("td", { class: "small" }, r.attempts ? `${r.attempts}${r.first_status === "pass" ? " (passed first time)" : ""}` : ""), h("td", { class: "small muted" }, r.sources.join("; ")),
      h("td", { class: "small" }, r.kind === "manual" ? `${r.by || ""}${r.comment ? ": " + r.comment : ""}` : (r.status === "pass" ? "" : r.problem || "")),
      h("td", { onclick: (e) => e.stopPropagation(), style: { whiteSpace: "nowrap" } }, h("button", { class: "ghost", title: "Record a manual result", onclick: () => recordManual(r) }, "Record result…")))));
  }
  const dl = async (fmt) => { try { const r = await GET("/api/group/export?" + q({ id, format: fmt })); saveBlob(new Blob([r.content], { type: fmt === "html" ? "text/html" : "text/csv" }), r.filename); } catch (e) { fail(e); } };
  const todo = t.counts.fail + t.counts.error + t.counts.inconclusive + t.counts.not_run;
  main.append(h("div", { class: "toolbar" },
    h("button", { disabled: !!g.closed || !t.total, title: g.closed ? "The cycle is closed" : "Runs every test case of the group (a session built from them)", onclick: () => runDialog(id, "group-all", "") }, "Run all cases ▶"),
    h("button", { class: "primary", disabled: !todo || !!g.closed, title: g.closed ? "The cycle is closed" : "Runs only the test cases that have not passed yet (not run or failing)", onclick: () => runDialog(id, "group-remaining", "") }, `Run what has not passed (${todo}) ▶`),
    h("button", { onclick: () => dl("csv") }, "Export CSV"), h("button", { onclick: () => dl("html") }, "Sign-off report (HTML)"), h("span", { class: "grow" }),
    g.closed ? h("button", { onclick: async () => { await POST("/api/group/close", { id, closed: false }); go("groups", { id, r: Date.now() }); } }, "Re-open")
      : h("button", { title: "Records the sign-off time and fixes the end date at today", onclick: async () => { if (await confirmBox("Close the cycle", `Sign off “${g.name}” now? Results after today no longer count. You can re-open it.`, "Close the cycle")) { await POST("/api/group/close", { id, closed: true }); go("groups", { id, r: Date.now() }); } } }, "Close the cycle (sign-off)"),
    h("button", { onclick: () => go("groups", { id, r: Date.now() }) }, "Refresh")),
    h("div", { class: "kv card" }, h("div", {}, "Test cases"), h("div", {}, `${t.total}${t.overlap ? ` (${t.overlap} reached through more than one member, counted once)` : ""}`),
      h("div", {}, "Executed"), h("div", {}, `${t.executed} of ${t.total} (${t.pct_executed}%)`), h("div", {}, "Passed"), h("div", {}, `${t.counts.pass} of ${t.total} (${t.pct_pass}%) — ${t.first_time_pass} at the first attempt`),
      h("div", {}, "Still to do"), h("div", {}, `${t.counts.not_run} not run, ${t.counts.fail + t.counts.error + t.counts.inconclusive} failing`)),
    h("div", { class: "grid g2" }, chartCard("Final results", donut, `${id}-final-results`, "Each test case counts by its latest result in the period. Click a slice to list those test cases."),
      chartCard("Progress by day", progressChart(t.daily), `${id}-progress`, "The final result of every test case at the end of each day.")),
    h("div", { class: "grid g2" }, chartCard("By member", memberChart(t.by_member), `${id}-by-member`, "Passed / total for each session, suite or set of cases in the group."), chartCard("Executions per day", runsChart(t.daily), `${id}-executions`)),
    t.members.some((m) => m.problem) ? h("div", { class: "banner warn" }, t.members.filter((m) => m.problem).map((m) => h("div", { class: "small" }, `${m.label}: ${m.problem}`))) : null,
    h("h2", {}, "Test cases"), h("div", { class: "toolbar" }, h("input", { placeholder: "Filter…", oninput: (e) => { f.text = e.target.value; drawCases(); } }), statusSel, shown), h("div", { class: "card", style: { padding: 0 } }, tbl));
  drawCases();
}

const backLinkTo = (label, fn) => h("div", { class: "toolbar" }, h("a", { href: "#", onclick: (e) => { e.preventDefault(); fn(); } }, "← " + label));

/* create / edit a group: its details and its members (sessions, suites, chosen test cases) with a live count */
async function groupEditor(main, existing, inPage) {
  const [{ sessions }, { tests }, { environments }] = await Promise.all([GET("/api/sessions"), GET("/api/tests"), GET("/api/environments")]);
  const suiteFiles = tests.filter((x) => ["testbot", "testbot-excel"].includes(x.format)).map((x) => x.path);
  const g = existing ? deep(existing) : { id: "", name: "", description: "", start: "", end: "", environment: "", frozen: false, members: [] };
  g.members = g.members || []; g.notify = g.notify || {};
  const isNew = !existing, preview = h("div", { class: "card" }), membersBox = h("div", {});
  let autoId = isNew;
  const refresh = debounce(async () => {
    if (!g.members.length) { clear(preview).append(h("div", { class: "muted small" }, "No members yet: add sessions, test suites or test cases below.")); return; }
    try {
      const p = await POST("/api/group/preview", { group: { ...g, frozen: false } });
      clear(preview).append(h("div", {}, h("b", {}, `${p.count} test case(s)`), p.overlap ? h("span", { class: "muted small" }, `  (${p.overlap} reached through more than one member, counted once)`) : null,
        g.frozen ? h("span", { class: "muted small" }, "  — the list is frozen when you save") : null),
        p.members.filter((m) => m.problem).map((m) => h("div", { class: "small fail" }, `${m.label}: ${m.problem}`)));
    } catch (e) { clear(preview).append(h("div", { class: "banner bad" }, e.message)); }
  }, 250);
  const drawMembers = () => {
    clear(membersBox).append(table(["Type", "Member", "Test cases", ""], g.members.length ? g.members.map((m, i) => h("tr", {},
      h("td", {}, badge(m.type === "case" ? "test cases" : m.type)), h("td", {}, h("b", {}, m.path)), h("td", { class: "small" }, m.type === "case" ? m.cases.join(", ") : (m.type === "suite" ? "whole suite" : "all the cases the session runs")),
      h("td", {}, h("button", { class: "icon danger", onclick: () => { g.members.splice(i, 1); drawMembers(); } }, "✕")))) : [h("tr", {}, h("td", { colspan: 4, class: "empty" }, "No members yet."))]));
    refresh();
  };
  const addSession = async () => {
    const f = { path: sessions[0] ? sessions[0].path : "" };
    modal({ title: "Add a session", body: sessions.length ? field("Session", selectInput(sessions.map((s) => [s.path, `${s.path} — ${s.session_name || s.session_id || ""}`]), f.path, (v) => (f.path = v)), "Every test case the session runs becomes part of the group.") : h("div", { class: "muted" }, "There are no sessions yet. Create one on the Sessions page."),
      buttons: [{ label: "Cancel" }, ...(sessions.length ? [{ label: "Add", primary: true, onclick: () => { if (!g.members.some((m) => m.type === "session" && m.path === f.path)) g.members.push({ type: "session", path: f.path }); drawMembers(); } }] : [])] });
  };
  const addSuite = async () => {
    const f = { path: suiteFiles[0] || "" };
    modal({ title: "Add a test suite", body: suiteFiles.length ? field("Test suite (file)", selectInput(suiteFiles, f.path, (v) => (f.path = v)), "All of its test cases become part of the group.") : h("div", { class: "muted" }, "There are no test suites yet."),
      buttons: [{ label: "Cancel" }, ...(suiteFiles.length ? [{ label: "Add", primary: true, onclick: () => { if (!g.members.some((m) => m.type === "suite" && m.path === f.path)) g.members.push({ type: "suite", path: f.path }); drawMembers(); } }] : [])] });
  };
  const addCases = async () => {
    const f = { path: suiteFiles[0] || "" }, box = h("div", {}); let chooser = null;
    const load = async () => { clear(box); chooser = null; if (!f.path) return; try { chooser = await casesChooser(f.path, []); box.append(chooser.node); } catch (e) { box.append(h("div", { class: "banner bad" }, e.message)); } };
    load();
    modal({ title: "Add test cases", wide: true, body: h("div", {}, field("Test suite (file)", selectInput(suiteFiles, f.path, (v) => { f.path = v; load(); })), box),
      buttons: [{ label: "Cancel" }, { label: "Add", primary: true, onclick: () => {
        if (!chooser) { toast("Choose a test suite", "bad"); return false; }
        const ids = chooser.get(); if (ids && !ids.length) { toast("Tick at least one test case", "bad"); return false; }
        g.members.push(ids ? { type: "case", path: f.path, cases: ids } : { type: "suite", path: f.path }); drawMembers(); } }] });
  };
  const save = async () => {
    const out = deep(g); delete out.notify; { const nf = g.notify || {}, nn = {}; if (nf.on) nn.on = nf.on; if (nf.to && nf.to.length) nn.to = nf.to; if (nf.attach_report) nn.attach_report = true; if (Object.keys(nn).length) out.notify = nn; }
    if (!out.id) out.id = (out.name || "").trim().replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
    try { const r = await PUT("/api/group", { group: out, create: isNew }); toast("Saved", "ok"); go("groups", { id: r.group.id }); } catch (e) { fail(e); }
  };
  const idField = isNew ? field("Group id", textInput(g, "id", { onchange: () => { autoId = false; }, placeholder: "uat-cycle-1" }), "Letters, digits, - _ . (filled from the name)") : field("Group id", h("input", { value: g.id, disabled: true }));
  const nameIn = textInput(g, "name", { onchange: (v) => { if (autoId) { g.id = v.trim().replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60); const el = main.querySelector("input[placeholder='uat-cycle-1']"); if (el) el.value = g.id; } } });
  if (!inPage) main.append(backLinkTo("Test groups", () => go("groups")), h("h1", {}, "New test group"));
  main.append(h("div", { class: "card" }, h("div", { class: "grid g3" }, field("Name", nameIn), idField, field("Description (optional)", textInput(g, "description"))),
      h("div", { class: "grid g3", style: { marginTop: "8px" } }, field("Start date", h("input", { type: "date", value: g.start || "", oninput: (e) => (g.start = e.target.value) }), "Results before this day do not count."),
        field("End date (optional)", h("input", { type: "date", value: g.end || "", oninput: (e) => (g.end = e.target.value) }), "Empty = up to today."),
        field("Environment (optional)", selectInput(Object.keys(environments), g.environment || "", (v) => (g.environment = v), { blank: "(any environment)" }), "Only runs against this environment count, e.g. uat.")),
      checkInput(g, "frozen", "Freeze the list of test cases when saving (a live group follows its sessions and suites as they change)", refresh)),
    h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Email when this group finishes running"),
      h("div", { class: "grid g3" }, field("Send", selectInput([["always", "every time"], ["failure", "only when something did not pass"], ["never", "never"]], g.notify.on || "", (v) => (g.notify.on = v), { blank: "(as Settings / the environment say)" })),
        field("Recipients (replace the default ones)", csvInput(g.notify, "to", { placeholder: "leave empty to use the default list" })), h("div", { style: { paddingTop: "18px" } }, checkInput(g.notify, "attach_report", "attach the HTML report"))),
      h("p", { class: "muted small" }, "Applies when the group is run with “Run all cases”, “Run what has not passed” or a schedule. The mail server comes from Settings or from the group's environment.")),
    h("h3", {}, "Members"), h("p", { class: "muted small" }, "Mix sessions, test suites and individual test cases. A test case reached through two members is counted once."),
    membersBox, h("div", { class: "toolbar" }, h("button", { onclick: addSession }, "+ Session…"), h("button", { onclick: addSuite }, "+ Test suite…"), h("button", { onclick: addCases }, "+ Test cases…")),
    preview, h("div", { class: "toolbar", style: { marginTop: "12px" } }, h("button", { class: "primary", onclick: save }, "Save"), h("button", { onclick: () => go("groups", existing ? { id: g.id } : undefined) }, "Cancel")));
  drawMembers();
}

/* "Test groups that include this ..." on the case and suite result pages */
function groupsSection(list, title) {
  return h("div", {}, h("h2", {}, title), (list || []).length ? h("div", { class: "card", style: { padding: 0 } }, table(["Test group", "Status of this test case", "Group progress", ""], list.map((x) => h("tr", { class: "clickable", onclick: () => go("groups", { id: x.id }) },
    h("td", {}, h("b", {}, x.name)), h("td", {}, x.status ? resultBadge(x.status) : h("span", { class: "muted small" }, `${x.cases} test case(s) of this suite`)), h("td", { class: "small" }, `${x.pct_pass}% passed`), h("td", {}, h("button", { onclick: (e) => { e.stopPropagation(); go("groups", { id: x.id }); } }, "Open group")))))) :
    h("div", { class: "muted small" }, "No test group includes it."));
}
