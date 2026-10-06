"use strict";
/* Choosing test cases of a test file: a picker (tick + order), and the dialogs built on it:
   - runCasesDialog      run some of a file's cases, in the order chosen
   - chooseCasesDialog   which cases a session entry / schedule item uses
   - addToSessionDialog  add a whole file, or some of its cases, to a session
   A selection is a list of case ids in run order; null/empty = the whole file in file order (the same rule as the runner's --case). */

let radioSeq = 0;

async function suiteCases(path) {
  const r = await GET("/api/test?" + q({ path }));
  return (r.suite.cases || []).map((c) => ({ id: c.id, title: c.title || "", steps: (c.steps || []).length, objective: !!c.objective, tags: c.tags || [], depends_on: c.depends_on || [] }));
}

/* the list: a tick per case, ↑↓ for the order. Run order = ticked cases in the order shown. */
function casePicker(cases, selected) {
  selected = (selected || []).filter((id, i, a) => cases.some((c) => c.id === id) && a.indexOf(id) === i);
  const order = [...selected, ...cases.map((c) => c.id).filter((id) => !selected.includes(id))];
  const on = new Set(selected);
  const byId = Object.fromEntries(cases.map((c) => [c.id, c]));
  const node = h("div", {});
  const move = (i, j) => { [order[i], order[j]] = [order[j], order[i]]; draw(); };
  function draw() {
    clear(node).append(
      h("div", { class: "toolbar", style: { margin: "0 0 6px" } },
        h("button", { onclick: () => { order.forEach((id) => on.add(id)); draw(); } }, "Select all"), h("button", { onclick: () => { on.clear(); draw(); } }, "Select none"),
        ...[...new Set(cases.flatMap((c) => c.tags))].sort().map((t) => h("button", { title: `Tick every test case tagged "${t}"`, onclick: () => { on.clear(); cases.filter((c) => c.tags.includes(t)).forEach((c) => on.add(c.id)); draw(); } }, "tag: " + t)),
        h("span", { class: "muted small" }, `${on.size} of ${cases.length} selected – they run in the order shown` + (cases.some((c) => c.depends_on.length) ? "; a case that “needs” others gets them just before it, in the same browser" : ""))),
      h("div", { style: { maxHeight: "300px", overflow: "auto" } }, table(["", "Test case", "Title", "Tags", "Steps", ""], order.map((id, i) => h("tr", {},
        h("td", {}, h("input", { type: "checkbox", checked: on.has(id), onchange: (e) => { if (e.target.checked) on.add(id); else on.delete(id); draw(); } })),
        h("td", {}, h("b", {}, id), byId[id].depends_on.length ? h("div", { class: "small muted" }, "needs " + byId[id].depends_on.join(", ")) : null), h("td", {}, byId[id].title),
        h("td", { class: "small" }, byId[id].tags.join(", ")), h("td", { class: "small" }, byId[id].objective && !byId[id].steps ? "natural language" : byId[id].steps),
        h("td", { style: { whiteSpace: "nowrap" } }, h("button", { class: "icon", disabled: i === 0, onclick: () => move(i, i - 1) }, "↑"),
          h("button", { class: "icon", disabled: i === order.length - 1, onclick: () => move(i, i + 1) }, "↓")))))));
  }
  draw();
  return { node, get: () => order.filter((id) => on.has(id)) };
}

/* "whole test suite" or "selected test cases" (+ the picker). get() -> null (whole file) or the ids in run order. */
async function casesChooser(path, current) {
  const cases = await suiteCases(path);
  const name = "cm" + (++radioSeq), state = { mode: current && current.length ? "some" : "all" };
  const picker = casePicker(cases, current || []), box = h("div", {});
  const draw = () => clear(box).append(
    h("label", { style: { display: "block", marginBottom: "4px" } }, h("input", { type: "radio", name, checked: state.mode === "all", onchange: () => { state.mode = "all"; draw(); } }), ` Whole test suite – all ${cases.length} test case(s), in file order`),
    h("label", { style: { display: "block", marginBottom: "6px" } }, h("input", { type: "radio", name, checked: state.mode === "some", onchange: () => { state.mode = "some"; draw(); } }), " Selected test cases"),
    state.mode === "some" ? picker.node : null);
  draw();
  return { node: box, total: cases.length, get: () => (state.mode === "all" ? null : picker.get()) };
}

/* what a selection reads like in a list */
function casesSummary(ids, total) {
  if (!ids || !ids.length) return total ? `whole suite (${total} cases)` : "whole suite";
  return `${ids.join(", ")}` + (total ? ` (${new Set(ids).size} of ${total})` : "");
}

/* Which cases does this session entry / schedule item run? Resolves to {cases: ids|null} or null when cancelled. */
function chooseCasesDialog(path, current) {
  return new Promise(async (resolve) => {
    let chooser;
    try { chooser = await casesChooser(path, current); } catch (e) { fail(e); return resolve(null); }
    modal({ title: "Test cases of " + path, wide: true, body: chooser.node, onclose: () => resolve(null),
      buttons: [{ label: "Cancel", onclick: () => resolve(null) },
        { label: "OK", primary: true, onclick: () => { const ids = chooser.get(); if (ids && !ids.length) { toast("Tick at least one test case, or choose the whole suite", "bad"); return false; } resolve({ cases: ids }); } }] });
  });
}

/* Run only some of a file's cases (in the order chosen) */
async function runCasesDialog(path, env, preselect) {
  let chooser;
  try { chooser = await casesChooser(path, preselect); } catch (e) { return fail(e); }
  modal({ title: "Run test cases of " + path, wide: true, body: h("div", {}, chooser.node,
      h("div", { class: "muted small", style: { marginTop: "6px" } }, "Each case starts in a clean browser, in the order shown. Whole suite = every case, as the ▶ button does.")),
    buttons: [{ label: "Cancel" },
      { label: "Copy to a new test file…", onclick: async () => {
        const ids = chooser.get(); if (!ids || !ids.length) { toast("Choose “Selected test cases” and tick the ones to copy", "bad"); return false; }
        const name = await promptBox("Copy the selected test cases", "Name of the new test file", path.replace(/\.[^.]+$/, "") + "-selected.json"); if (!name) return false;
        try { const r = await POST("/api/test/extract", { path, cases: ids, new_path: name }); toast(`Created ${r.path} with ${r.cases.length} test case(s)` + (r.prerequisites_added.length ? ` (prerequisites added: ${r.prerequisites_added.join(", ")})` : ""), "ok"); } catch (e) { fail(e); } } },
      { label: "Run ▶", primary: true, onclick: () => {
      const ids = chooser.get(); if (ids && !ids.length) { toast("Tick at least one test case", "bad"); return false; }
      runDialog(path, "suite", env || "", {}, ids || undefined); } }] });
}

/* Add a whole file, or some of its cases, to a session (an existing one or a new one) */
async function addToSessionDialog(path, preselect) {
  let sessions;
  try { sessions = (await GET("/api/sessions")).sessions; } catch (e) { return fail(e); }
  let chooser;
  try { chooser = await casesChooser(path, preselect); } catch (e) { return fail(e); }
  const f = { session: sessions.length ? sessions[0].path : "", newName: "", same: false };
  const pick = selectInput([...sessions.map((s) => [s.path, `${s.path}  (${s.files} entr${s.files === 1 ? "y" : "ies"})`]), ["", "+ a new session…"]], f.session, (v) => { f.session = v; sync(); });
  const nameBox = h("div", {}), sameBox = h("div", {});
  const sync = () => {
    clear(nameBox); clear(sameBox);
    if (!f.session) nameBox.append(field("Name of the new session", textInput(f, "newName", { placeholder: "my-session" })));
    else sameBox.append(checkInput(f, "same", "continue in the same browser as the previous entry of that session (stay logged in)"));
  };
  sync();
  modal({ title: "Add to a session: " + path, wide: true,
    body: h("div", {}, field("Session", pick), nameBox, h("div", { style: { marginBottom: "10px" } }, sameBox), h("b", {}, "What to add"), h("div", { style: { marginTop: "6px" } }, chooser.node)),
    buttons: [{ label: "Cancel" }, { label: "Add", primary: true, onclick: async () => {
      const ids = chooser.get(); if (ids && !ids.length) { toast("Tick at least one test case, or choose the whole suite", "bad"); return false; }
      const entry = { path };
      if (ids) entry.cases = ids;
      try {
        let target = f.session, plan;
        if (target) { plan = (await GET("/api/session?" + q({ path: target }))).plan; plan.files = plan.files || []; if (f.same && plan.files.length) entry.shares_state_with_previous = true; }
        else {
          const nm = (f.newName || "").trim().replace(/\.ya?ml$/i, "").replace(/[^A-Za-z0-9_.-]+/g, "-"); if (!nm) { toast("Give the new session a name", "bad"); return false; }
          target = "sessions/" + nm + ".yaml"; plan = { session_id: nm.toUpperCase(), session_name: nm, files: [] };
        }
        plan.files.push(entry);
        const r = await PUT("/api/session", { path: target, plan });
        toast(`Added ${casesSummary(ids, chooser.total)} to ${r.path}`, "ok");
        setTimeout(() => confirmBox("Session updated", `${r.path} now has ${plan.files.length} entr${plan.files.length === 1 ? "y" : "ies"}. Open it to arrange the order or run it?`, "Open the session").then((yes) => yes && go("sessions", { path: r.path })), 50);
      } catch (e) { fail(e); return false; } } }] });
}
