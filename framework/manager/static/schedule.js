"use strict";
/* Sessions (ordered suite lists that can share a logged-in browser) and Schedules (what runs when, in what order). */

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const loadFields = (obj, onchange) => h("div", { class: "grid g4" }, field("Workers", numInput(obj, "workers", { onchange })), field("Iterations per worker", numInput(obj, "iterations", { onchange })),
  field("Duration (s)", numInput(obj, "duration", { onchange })), field("Ramp-up (s)", numInput(obj, "ramp_up", { onchange })));

/* ------------------------------------------------------------------ sessions */
routes.sessions = async (main, params) => {
  const [{ sessions }, { tests }] = await Promise.all([GET("/api/sessions"), GET("/api/tests")]);
  const suiteOptions = tests.filter((t) => ["testbot", "testbot-excel"].includes(t.format)).map((t) => t.path);
  main.append(h("h1", {}, "Sessions"), h("p", { class: "muted" }, "A session runs whole test suites, or just some of their test cases, in the order you choose. An entry can continue in the same logged-in browser as the previous one."),
    h("div", { class: "toolbar" }, h("button", { class: "primary", onclick: async () => {
      const n = await promptBox("New session", "File name", "my-session"); if (!n) return;
      go("sessions", { path: "sessions/" + n.replace(/\.ya?ml$/i, "") + ".yaml", isnew: 1 }); } }, "+ New session")),
    h("div", { class: "card", style: { padding: 0 } }, table(["File", "Session ID", "Name", "Files", ""], sessions.length ? sessions.map((s) => h("tr", { class: "clickable", onclick: () => go("sessions", { path: s.path }) },
      h("td", {}, s.path), h("td", {}, s.session_id || ""), h("td", {}, s.session_name || ""), h("td", {}, s.files),
      h("td", { onclick: (e) => e.stopPropagation() }, h("button", { class: "icon danger", onclick: async () => { if (await confirmBox("Delete session", `Delete ${s.path}?`, "Delete", true)) { await DEL("/api/session?" + q({ path: s.path })); go("sessions", { r: Date.now() }); } } }, "🗑")))) :
      [h("tr", {}, h("td", { colspan: 5, class: "empty" }, "No sessions yet."))])));
  if (!params.path) return;
  let plan; if (params.isnew) plan = { session_id: params.path.split("/").pop().replace(/\.ya?ml$/i, "").toUpperCase(), session_name: "New session", files: [] }; else plan = (await GET("/api/session?" + q({ path: params.path }))).plan;
  plan.files = plan.files || []; plan.notify = plan.notify || {}; const opt = { workers: plan.workers, iterations: plan.iterations, duration: plan.duration_s, ramp_up: plan.ramp_up_s };
  const filesBox = h("div", {});
  const previewBox = h("div", { class: "card" });
  const drawFiles = () => {
    clear(filesBox);
    plan.files.forEach((f, i) => {
      const summary = h("span", { class: "small" }, f.path ? casesSummary(f.cases) : "");
      const dup = f.cases && new Set(f.cases).size !== f.cases.length;     // a case listed twice (edited in the file): keep it as it is
      if (f.path) suiteCases(f.path).then((cs) => { summary.textContent = casesSummary(f.cases, cs.length); }, () => { summary.textContent = "(file not found)"; });
      filesBox.append(h("div", { class: "card", style: { padding: "8px 10px", marginBottom: "6px" } }, h("div", { class: "toolbar", style: { margin: 0 } },
        h("b", {}, (i + 1) + "."), selectInput(suiteOptions, f.path, (v) => { f.path = v; f.cases = undefined; drawFiles(); }, { blank: "(choose a test file)" }),
        summary, h("button", { disabled: !f.path || dup, title: dup ? "This entry lists a case more than once; edit the session file to change it" : "Run the whole suite, or only some of its test cases, in the order you choose",
          onclick: async () => { const r = await chooseCasesDialog(f.path, f.cases); if (r) { f.cases = r.cases || undefined; drawFiles(); } } }, "Cases…"),
        i > 0 ? checkInput(f, "shares_state_with_previous", "same browser as the previous entry (stay logged in)") : h("span", { class: "muted small" }, "starts a fresh browser"),
        h("span", { class: "grow" }),
        h("label", { class: "small muted" }, "if a case fails: "), selectInput([["stop_session", "stop the session"], ["stop_file", "stop this file, go on"], ["continue", "keep going"]], f.on_fail || "", (v) => (f.on_fail = v || undefined), { blank: "(session default)" }),
        h("button", { class: "icon", disabled: i === 0, onclick: () => { [plan.files[i - 1], plan.files[i]] = [plan.files[i], plan.files[i - 1]]; drawFiles(); } }, "↑"),
        h("button", { class: "icon", disabled: i === plan.files.length - 1, onclick: () => { [plan.files[i + 1], plan.files[i]] = [plan.files[i], plan.files[i + 1]]; drawFiles(); } }, "↓"),
        h("button", { class: "icon danger", onclick: () => { plan.files.splice(i, 1); drawFiles(); } }, "✕"))));
    });
    if (!plan.files.length) filesBox.append(h("div", { class: "muted small" }, "No entries yet. Use “Add to session…” to add a whole test suite or some of its test cases."));
    drawPreview();
  };
  /* the final run order, case by case */
  const drawPreview = async () => {
    const rows = [];
    for (const [i, f] of plan.files.entries()) {
      if (!f.path) continue;
      let ids; try { ids = f.cases && f.cases.length ? f.cases : (await suiteCases(f.path)).map((c) => c.id); } catch (e) { ids = ["(file not found)"]; }
      rows.push(h("div", { class: "small" }, h("b", {}, `${i + 1}. ${f.path}`), h("span", { class: "muted" }, f.shares_state_with_previous && i > 0 ? "  (same browser)" : "  (fresh browser)"), " → ", ids.join("  ›  ")));
    }
    clear(previewBox).append(h("div", { class: "muted small", style: { marginBottom: "4px" } }, "Run order"), rows.length ? rows : h("div", { class: "muted small" }, "–"));
  };
  const addEntryDialog = () => {
    const f = { path: suiteOptions[0] || "", same: false }, chooserBox = h("div", {}); let chooser = null;
    const load = async () => { clear(chooserBox); chooser = null; if (!f.path) return; try { chooser = await casesChooser(f.path, []); chooserBox.append(chooser.node); } catch (e) { chooserBox.append(h("div", { class: "banner bad" }, e.message)); } };
    load();
    modal({ title: "Add to the session", wide: true,
      body: h("div", {}, field("Test suite (file)", selectInput(suiteOptions, f.path, (v) => { f.path = v; load(); })), plan.files.length ? h("div", { style: { marginBottom: "10px" } }, checkInput(f, "same", "continue in the same browser as the previous entry (stay logged in)")) : null,
        h("b", {}, "What to add"), h("div", { style: { marginTop: "6px" } }, chooserBox)),
      buttons: [{ label: "Cancel" }, { label: "Add", primary: true, onclick: () => {
        if (!chooser) { toast("Choose a test suite", "bad"); return false; }
        const ids = chooser.get(); if (ids && !ids.length) { toast("Tick at least one test case, or choose the whole suite", "bad"); return false; }
        const entry = { path: f.path }; if (ids) entry.cases = ids; if (f.same && plan.files.length) entry.shares_state_with_previous = true;
        plan.files.push(entry); drawFiles(); } }] });
  };
  drawFiles();
  const save = async () => {
    const out = { ...plan }; ["workers", "iterations", "duration_s", "ramp_up_s"].forEach((k) => delete out[k]);
    if (opt.workers) out.workers = opt.workers; if (opt.iterations) out.iterations = opt.iterations; if (opt.duration) out.duration_s = opt.duration; if (opt.ramp_up) out.ramp_up_s = opt.ramp_up;
    if (!out.environment) delete out.environment;
    out.files = out.files.filter((f) => f.path).map((f) => { const o = { path: f.path }; if (f.cases && f.cases.length) o.cases = f.cases; if (f.shares_state_with_previous) o.shares_state_with_previous = true; if (f.on_fail) o.on_fail = f.on_fail; return o; });
    if (!out.on_fail || out.on_fail === "stop_session") delete out.on_fail;
    delete out.notify; const nf = plan.notify || {}, nn = {}; if (nf.on) nn.on = nf.on; if (nf.to && nf.to.length) nn.to = nf.to; if (nf.attach_report) nn.attach_report = true; if (Object.keys(nn).length) out.notify = nn;
    try { const r = await PUT("/api/session", { path: params.path, plan: out }); toast("Saved", "ok"); return r.path; } catch (e) { fail(e); return null; }
  };
  main.append(h("h2", {}, "Edit " + params.path), h("div", { class: "card" }, h("div", { class: "grid g3" },
    field("Session ID", textInput(plan, "session_id")), field("Name", textInput(plan, "session_name")), field("Environment (optional)", textInput(plan, "environment")))),
    h("h3", {}, "What runs, in order"), filesBox, h("button", { class: "primary", onclick: addEntryDialog }, "+ Add to session…"), previewBox,
    h("div", { class: "card", style: { marginTop: "10px" } }, field("If a test case fails", selectInput([["stop_session", "Stop the session after that file (default)"], ["stop_file", "Stop that file, go on with the next entry"], ["continue", "Keep going, never stop early"]], plan.on_fail || "stop_session", (v) => (plan.on_fail = v)),
      "“Stop the session” lets the file's remaining cases finish (per the file's own setting) and then stops before the next entry. An entry can override this.")),
    h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Email when the session completes"),
      h("div", { class: "grid g3" }, field("Send", selectInput([["always", "every time"], ["failure", "only when something did not pass"], ["never", "never"]], plan.notify.on || "", (v) => (plan.notify.on = v), { blank: "(as the environment says)" })),
        field("Recipients (replace the environment's)", csvInput(plan.notify, "to", { placeholder: "leave empty to use the environment's list" })),
        h("div", { style: { paddingTop: "18px" } }, checkInput(plan.notify, "attach_report", "attach the HTML report"))),
      h("p", { class: "muted small" }, "The mail server comes from the environment of this session (the Environment field above): set it up on the Environments & SQL page. Nothing is sent unless that environment or this session asks for it.")),
    h("h3", {}, "Load test (optional)"), loadFields(opt, () => {}),
    h("div", { class: "toolbar", style: { marginTop: "14px" } }, h("button", { class: "primary", onclick: save }, "Save"),
      h("button", { onclick: async () => { const p = await save(); if (p) runDialog(p, "session", plan.environment || ""); } }, "Save & run ▶")));
};

/* ------------------------------------------------------------------ schedules */
function triggerSummary(t) {
  if (!t) return "";
  switch (t.type) {
    case "manual": return "manual only";
    case "once": return "once, " + (t.at || "").replace("T", " ");
    case "interval": return `every ${t.every} ${t.unit || "minutes"}`;
    case "daily": return "daily at " + t.time;
    case "weekly": return (t.days || []).join(", ") + " at " + t.time;
    case "monthly": return `day ${t.day || 1} of each month, ${t.time}`;
    default: return t.type;
  }
}

routes.schedules = async (main, params) => {
  const [{ schedules, scheduler }, { tests }, { sessions }, { environments }, { groups }] = await Promise.all([GET("/api/schedules"), GET("/api/tests"), GET("/api/sessions"), GET("/api/environments"), GET("/api/groups")]);
  const groupOptions = groups.map((g) => [g.id, `${g.name} (${g.total} test cases)`]);
  const suitePaths = tests.filter((t) => ["testbot", "testbot-excel"].includes(t.format)).map((t) => t.path), sessionPaths = sessions.map((s) => s.path);
  const banner = scheduler.running
    ? h("div", { class: "banner ok" }, `The scheduler is running (process ${scheduler.pid}, last check ${fmtTime(scheduler.last_beat)}). Scheduled tests start automatically.`)
    : h("div", { class: "banner warn" }, h("b", {}, "The scheduler is not running. "), "Schedules only start tests while the scheduler program is running. Start it with ", h("span", { class: "mono" }, "testbot-manager.exe scheduler run"),
      " or let Windows start it at logon: ", h("button", { onclick: async () => { try { const r = await POST("/api/scheduler/install", { at: "logon" }); toast(r.message, "ok"); go("schedules", { r: Date.now() }); } catch (e) { fail(e); } } }, "Start automatically at logon"),
      scheduler.installed ? h("button", { onclick: async () => { try { toast((await POST("/api/scheduler/uninstall")).message, "ok"); go("schedules", { r: Date.now() }); } catch (e) { fail(e); } } }, "Remove automatic start") : null,
      h("div", { class: "small muted" }, "You can still run any schedule by hand with “Run now”, without the scheduler."));
  main.append(h("h1", {}, "Schedules"), banner,
    h("div", { class: "toolbar" }, h("button", { class: "primary", onclick: () => go("schedules", { id: "new" }) }, "+ New schedule")),
    h("div", { class: "card", style: { padding: 0 } }, table(["Schedule", "When", "Tests (in order)", "Next run", "Last run", ""], schedules.length ? schedules.map((s) => {
      const last = s.status.last_run;
      return h("tr", { class: "clickable", onclick: () => go("schedules", { id: s.id }) },
        h("td", {}, h("b", {}, s.name || s.id), h("div", { class: "small muted" }, s.id), s.enabled === false ? badge("disabled", "warn") : null),
        h("td", {}, triggerSummary(s.trigger)), h("td", { class: "small" }, (s.items || []).map((i, n) => h("div", {}, `${n + 1}. ${i.type === "group" ? "group " + i.path + (i.mode === "remaining" ? " (not passed yet)" : "") : i.path}`))),
        h("td", { class: "small" }, s.status.next_run ? fmtTime(s.status.next_run) : "–"),
        h("td", { class: "small" }, s.status.running ? badge("running…", "warn") : (last ? [statusBadge(last.status), " ", fmtTime(last.started)] : "never")),
        h("td", { onclick: (e) => e.stopPropagation(), style: { whiteSpace: "nowrap" } }, h("button", { class: "icon", title: "Run now", onclick: () => runScheduleNow(s) }, "▶"),
          h("button", { class: "icon", title: "History", onclick: () => historyDialog(s) }, "🕘")));
    }) : [h("tr", {}, h("td", { colspan: 6, class: "empty" }, "No schedules yet."))])));
  if (!params.id) return;
  const sched = params.id === "new" ? { id: "", name: "New schedule", enabled: true, trigger: { type: "daily", time: "02:00" }, items: [] } : deep(schedules.find((s) => s.id === params.id));
  delete sched.status;
  const box = h("div", {}); main.append(box);
  const trigBox = h("div", {}), itemsBox = h("div", {});
  const drawTrigger = () => {
    clear(trigBox); const t = sched.trigger;
    const typeSel = selectInput(["manual", "once", "interval", "daily", "weekly", "monthly"], t.type, (v) => { const n = { type: v }; if (v === "once") n.at = ""; if (v === "interval") { n.every = 30; n.unit = "minutes"; } if (["daily", "weekly", "monthly"].includes(v)) n.time = "02:00"; if (v === "weekly") n.days = ["Mon"]; if (v === "monthly") n.day = 1; sched.trigger = n; drawTrigger(); });
    const parts = [field("Run", typeSel)];
    if (t.type === "once") parts.push(field("Date and time", h("input", { type: "datetime-local", value: t.at || "", oninput: (e) => (t.at = e.target.value) })));
    if (t.type === "interval") parts.push(field("Every", numInput(t, "every")), field("Unit", selectInput(["minutes", "hours"], t.unit || "minutes", (v) => (t.unit = v))));
    if (["daily", "weekly", "monthly"].includes(t.type)) parts.push(field("At (24 h)", h("input", { type: "time", value: t.time || "02:00", oninput: (e) => (t.time = e.target.value) })));
    if (t.type === "monthly") parts.push(field("Day of month", numInput(t, "day")));
    trigBox.append(h("div", { class: "grid g4" }, parts));
    if (t.type === "weekly") trigBox.append(h("div", { style: { marginTop: "8px" } }, DAYS.map((d) => h("label", { style: { marginRight: "12px" } }, h("input", { type: "checkbox", checked: (t.days || []).includes(d), onchange: (e) => { t.days = t.days || []; if (e.target.checked) t.days.push(d); else t.days = t.days.filter((x) => x !== d); } }), " " + d))));
    if (t.type === "interval") trigBox.append(h("div", { class: "muted small" }, "Counted from the end of the previous run."));
    if (t.type === "manual") trigBox.append(h("div", { class: "muted small" }, "Only runs when you press Run now."));
  };
  const drawItems = () => {
    clear(itemsBox);
    sched.items.forEach((it, i) => {
      it.options = it.options || {};
      const opts = { workers: it.options.workers, iterations: it.options.iterations, duration: it.options.duration, ramp_up: it.options.ramp_up };
      const sync = () => Object.assign(it.options, { workers: opts.workers, iterations: opts.iterations, duration: opts.duration, ramp_up: opts.ramp_up });
      itemsBox.append(h("div", { class: "card", style: { padding: "10px" } },
        h("div", { class: "toolbar", style: { margin: 0 } }, h("b", {}, (i + 1) + "."),
          selectInput([["suite", "Test file"], ["session", "Session"], ["group", "Test group"]], it.type, (v) => { it.type = v; it.path = ""; it.cases = undefined; it.mode = v === "group" ? "all" : undefined; drawItems(); }),
          selectInput(it.type === "session" ? sessionPaths : (it.type === "group" ? groupOptions : suitePaths), it.path, (v) => { it.path = v; it.cases = undefined; drawItems(); }, { blank: "(choose)" }),
          it.type === "group" ? selectInput([["all", "all its test cases"], ["remaining", "only the cases not passed yet"]], it.mode || "all", (v) => (it.mode = v)) : null,
          it.type === "suite" ? h("button", { disabled: !it.path, title: "Run the whole file, or only some of its test cases, in the order you choose",
            onclick: async () => { const r = await chooseCasesDialog(it.path, it.cases); if (r) { it.cases = r.cases || undefined; drawItems(); } } }, "Cases: " + (it.cases && it.cases.length ? it.cases.join(", ") : "all")) : null,
          it.type === "suite" ? selectInput(Object.keys(environments), it.env || "", (v) => (it.env = v), { blank: "(no environment)" }) : null,
          checkInput(it, "stop_on_failure", "stop the schedule if this fails"),
          h("span", { class: "grow" }), h("button", { class: "icon", disabled: i === 0, onclick: () => { [sched.items[i - 1], sched.items[i]] = [sched.items[i], sched.items[i - 1]]; drawItems(); } }, "↑"),
          h("button", { class: "icon", disabled: i === sched.items.length - 1, onclick: () => { [sched.items[i + 1], sched.items[i]] = [sched.items[i], sched.items[i + 1]]; drawItems(); } }, "↓"),
          h("button", { class: "icon danger", onclick: () => { sched.items.splice(i, 1); drawItems(); } }, "✕")),
        h("details", {}, h("summary", { class: "small muted" }, "Options: load test, screenshots, browser window"),
          loadFields(opts, sync), h("div", { class: "grid g4", style: { marginTop: "8px" } },
            field("Screenshots", selectInput(["all", "fail", "none"], it.options.screenshots || "", (v) => (it.options.screenshots = v), { blank: "(default)" })),
            field("Mode", selectInput(S.catalog.modes, it.options.mode || "", (v) => (it.options.mode = v), { blank: "(default)" })),
            field("Time limit (seconds)", numInput(it, "timeout_s")), h("div", { style: { paddingTop: "18px" } }, checkInput(it.options, "headed", "show the browser")),
            h("div", { style: { paddingTop: "18px" } }, checkInput(it.options, "confirm_load", "allow more than 10 workers"))))));
    });
    if (!sched.items.length) itemsBox.append(h("div", { class: "muted small" }, "No tests yet."));
  };
  box.append(h("h2", {}, params.id === "new" ? "New schedule" : "Edit schedule"),
    h("div", { class: "card" }, h("div", { class: "grid g3" }, field("ID (file name)", h("input", { value: sched.id, disabled: params.id !== "new", oninput: (e) => (sched.id = e.target.value.trim()) })), field("Name", textInput(sched, "name")),
      h("div", { style: { paddingTop: "18px" } }, checkInput(sched, "enabled", "enabled")))),
    h("h3", {}, "When"), h("div", { class: "card" }, trigBox), h("h3", {}, "What runs, in this order"), itemsBox,
    h("button", { onclick: () => { sched.items.push({ type: "suite", path: "" }); drawItems(); } }, "+ Add test"),
    h("div", { style: { marginTop: "10px" } }, checkInput(sched, "stop_on_failure", "if one test fails, skip the rest")),
    h("div", { class: "toolbar", style: { marginTop: "14px" } }, h("button", { class: "primary", onclick: async () => {
      try { const r = await PUT("/api/schedule", { schedule: cleanSchedule(sched) }); toast("Saved" + (r.problems.length ? " – " + r.problems.map((p) => p.message).join("; ") : ""), r.problems.length ? "" : "ok"); go("schedules", { id: sched.id }); } catch (e) { fail(e); } } }, "Save"),
      params.id !== "new" ? h("button", { onclick: () => runScheduleNow(sched) }, "Run now ▶") : null,
      params.id !== "new" ? h("button", { class: "danger", onclick: async () => { if (await confirmBox("Delete schedule", `Delete ${sched.id}?`, "Delete", true)) { await DEL("/api/schedule?" + q({ id: sched.id })); go("schedules", { r: Date.now() }); } } }, "Delete") : null,
      h("button", { onclick: () => go("schedules") }, "Close")));
  drawTrigger(); drawItems();
};

function cleanSchedule(s) {
  const o = deep(s);
  (o.items || []).forEach((it) => { const op = it.options || {}; ["workers", "iterations", "duration", "ramp_up"].forEach((k) => { if (!op[k]) delete op[k]; }); ["screenshots", "mode"].forEach((k) => { if (!op[k]) delete op[k]; }); if (!op.headed) delete op.headed; if (!op.confirm_load) delete op.confirm_load; it.options = op; if (it.type !== "group") delete it.mode; if (!it.cases || !it.cases.length || it.type !== "suite") delete it.cases; if (!it.tags || !it.tags.length || it.type !== "suite") delete it.tags; if (!it.env) delete it.env; if (!it.timeout_s) delete it.timeout_s; if (!it.stop_on_failure) delete it.stop_on_failure; });
  if (!o.stop_on_failure) delete o.stop_on_failure;
  return o;
}
const runScheduleNow = async (s) => {
  try { await POST("/api/schedule/run", { id: s.id }); } catch (e) { return fail(e); }
  const log = h("pre", { class: "log" }, "Starting…"), status = h("div", { class: "muted" }, "Starting…"); let stop = false;
  modal({ title: "Running: " + (s.name || s.id), wide: true, body: h("div", {}, status, log), buttons: [{ label: "Cancel the run", danger: true, stay: true, onclick: async () => { await POST("/api/schedule/cancel", { id: s.id }); toast("Cancelling after the current test…"); } }, { label: "Close" }], onclose: () => (stop = true) });
  while (!stop) {
    try {
      const r = await GET("/api/run/status?" + q({ id: s.id })); log.textContent = r.log || ""; log.scrollTop = log.scrollHeight;
      status.textContent = (r.running ? "Running… " : "Finished: " + r.record.status + " ") + (r.record.items || []).map((i) => `${i.path}: ${i.status}`).join(" · ");
      if (!r.running) break;
    } catch (e) { break; }
    await new Promise((res) => setTimeout(res, 1200));
  }
};
async function historyDialog(s) {
  const { history } = await GET("/api/schedule/history?" + q({ id: s.id }));
  modal({ title: "History: " + (s.name || s.id), wide: true, body: history.length ? table(["Started", "Trigger", "Status", "Tests", "Log"], history.map((r) => h("tr", {}, h("td", { class: "small" }, fmtTime(r.started)), h("td", {}, r.trigger), h("td", {}, statusBadge(r.status)),
    h("td", { class: "small" }, (r.items || []).map((i) => h("div", {}, `${i.n}. ${i.path}: ${i.status}${i.seconds ? " (" + i.seconds + " s)" : ""}`))),
    h("td", {}, r.dir ? h("a", { href: "/files/results/" + r.dir + "/run.log", target: "_blank" }, "log") : "")))) : h("div", { class: "empty" }, "Never run."), buttons: [{ label: "Close" }] });
}
