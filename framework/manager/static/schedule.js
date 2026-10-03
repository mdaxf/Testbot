"use strict";
/* Sessions (ordered suite lists that can share a logged-in browser) and Schedules (what runs when, in what order). */

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const loadFields = (obj, onchange) => h("div", { class: "grid g4" }, field("Workers", numInput(obj, "workers", { onchange })), field("Iterations per worker", numInput(obj, "iterations", { onchange })),
  field("Duration (s)", numInput(obj, "duration", { onchange })), field("Ramp-up (s)", numInput(obj, "ramp_up", { onchange })));

/* ------------------------------------------------------------------ sessions */
routes.sessions = async (main, params) => {
  const [{ sessions }, { tests }] = await Promise.all([GET("/api/sessions"), GET("/api/tests")]);
  const suiteOptions = tests.filter((t) => ["testbot", "testbot-excel"].includes(t.format)).map((t) => t.path);
  main.append(h("h1", {}, "Sessions"), h("p", { class: "muted" }, "A session runs several test files in a fixed order. A file can continue in the same logged-in browser as the previous one."),
    h("div", { class: "toolbar" }, h("button", { class: "primary", onclick: async () => {
      const n = await promptBox("New session", "File name", "my-session"); if (!n) return;
      go("sessions", { path: "sessions/" + n.replace(/\.ya?ml$/i, "") + ".yaml", isnew: 1 }); } }, "+ New session")),
    h("div", { class: "card", style: { padding: 0 } }, table(["File", "Session ID", "Name", "Files", ""], sessions.length ? sessions.map((s) => h("tr", { class: "clickable", onclick: () => go("sessions", { path: s.path }) },
      h("td", {}, s.path), h("td", {}, s.session_id || ""), h("td", {}, s.session_name || ""), h("td", {}, s.files),
      h("td", { onclick: (e) => e.stopPropagation() }, h("button", { class: "icon danger", onclick: async () => { if (await confirmBox("Delete session", `Delete ${s.path}?`, "Delete", true)) { await DEL("/api/session?" + q({ path: s.path })); go("sessions", { r: Date.now() }); } } }, "🗑")))) :
      [h("tr", {}, h("td", { colspan: 5, class: "empty" }, "No sessions yet."))])));
  if (!params.path) return;
  let plan; if (params.isnew) plan = { session_id: params.path.split("/").pop().replace(/\.ya?ml$/i, "").toUpperCase(), session_name: "New session", files: [] }; else plan = (await GET("/api/session?" + q({ path: params.path }))).plan;
  plan.files = plan.files || []; const opt = { workers: plan.workers, iterations: plan.iterations, duration: plan.duration_s, ramp_up: plan.ramp_up_s };
  const filesBox = h("div", {});
  const drawFiles = () => {
    clear(filesBox);
    plan.files.forEach((f, i) => filesBox.append(h("div", { class: "card", style: { padding: "8px 10px", marginBottom: "6px" } }, h("div", { class: "toolbar", style: { margin: 0 } },
      h("b", {}, (i + 1) + "."), selectInput(suiteOptions, f.path, (v) => (f.path = v), { blank: "(choose a test file)" }),
      i > 0 ? checkInput(f, "shares_state_with_previous", "same browser as the previous file (stay logged in)") : h("span", { class: "muted small" }, "starts a fresh browser"),
      h("span", { class: "grow" }), h("button", { class: "icon", disabled: i === 0, onclick: () => { [plan.files[i - 1], plan.files[i]] = [plan.files[i], plan.files[i - 1]]; drawFiles(); } }, "↑"),
      h("button", { class: "icon", disabled: i === plan.files.length - 1, onclick: () => { [plan.files[i + 1], plan.files[i]] = [plan.files[i], plan.files[i + 1]]; drawFiles(); } }, "↓"),
      h("button", { class: "icon danger", onclick: () => { plan.files.splice(i, 1); drawFiles(); } }, "✕")))));
    if (!plan.files.length) filesBox.append(h("div", { class: "muted small" }, "No files yet."));
  };
  drawFiles();
  const save = async () => {
    const out = { ...plan }; ["workers", "iterations", "duration_s", "ramp_up_s"].forEach((k) => delete out[k]);
    if (opt.workers) out.workers = opt.workers; if (opt.iterations) out.iterations = opt.iterations; if (opt.duration) out.duration_s = opt.duration; if (opt.ramp_up) out.ramp_up_s = opt.ramp_up;
    if (!out.environment) delete out.environment;
    out.files = out.files.filter((f) => f.path).map((f) => { const o = { path: f.path }; if (f.shares_state_with_previous) o.shares_state_with_previous = true; return o; });
    try { const r = await PUT("/api/session", { path: params.path, plan: out }); toast("Saved", "ok"); return r.path; } catch (e) { fail(e); return null; }
  };
  main.append(h("h2", {}, "Edit " + params.path), h("div", { class: "card" }, h("div", { class: "grid g3" },
    field("Session ID", textInput(plan, "session_id")), field("Name", textInput(plan, "session_name")), field("Environment (optional)", textInput(plan, "environment")))),
    h("h3", {}, "Test files, in order"), filesBox, h("button", { onclick: () => { plan.files.push({ path: "" }); drawFiles(); } }, "+ Add file"),
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
  const [{ schedules, scheduler }, { tests }, { sessions }, { environments }] = await Promise.all([GET("/api/schedules"), GET("/api/tests"), GET("/api/sessions"), GET("/api/environments")]);
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
        h("td", {}, triggerSummary(s.trigger)), h("td", { class: "small" }, (s.items || []).map((i, n) => h("div", {}, `${n + 1}. ${i.path}`))),
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
          selectInput([["suite", "Test file"], ["session", "Session"]], it.type, (v) => { it.type = v; it.path = ""; drawItems(); }),
          selectInput(it.type === "session" ? sessionPaths : suitePaths, it.path, (v) => (it.path = v), { blank: "(choose)" }),
          selectInput(Object.keys(environments), it.env || "", (v) => (it.env = v), { blank: "(no environment)" }),
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
  (o.items || []).forEach((it) => { const op = it.options || {}; ["workers", "iterations", "duration", "ramp_up"].forEach((k) => { if (!op[k]) delete op[k]; }); ["screenshots", "mode"].forEach((k) => { if (!op[k]) delete op[k]; }); if (!op.headed) delete op.headed; if (!op.confirm_load) delete op.confirm_load; it.options = op; if (!it.env) delete it.env; if (!it.timeout_s) delete it.timeout_s; if (!it.stop_on_failure) delete it.stop_on_failure; });
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
