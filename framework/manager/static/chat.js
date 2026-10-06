"use strict";
/* Chat with the AI assistant about one test suite. The conversation is kept on disk per suite. An assistant message may carry a PROPOSAL (test cases to add or
   replace, shown as a diff): the user applies it into the editor (a new revision when saved) or saves it as a draft revision -- or dismisses it. */

/* host = { path, getSuite() -> suite (as the user sees it), focusId() / setFocus(id), applyToDefault(changes, settings, note) -> Promise, reload() } */
async function chatPanel(body, host) {
  let history;
  const draw = async () => {
    try { history = await GET("/api/chat?" + q({ path: host.path })); } catch (e) { clear(body).append(h("div", { class: "banner bad" }, e.message)); return; }
    render();
  };
  const messagesBox = h("div", { style: { maxHeight: "460px", overflow: "auto", padding: "4px 2px" } });
  const input = h("textarea", { rows: 3, placeholder: "Describe what you want, e.g. “add a step that logs in before step 3”, “create a case that checks the order total”, “why does step 4 need a wait?”  (Ctrl+Enter sends)", style: { width: "100%" } });
  const status = h("span", { class: "muted small" });
  const sendBtn = h("button", { class: "primary" }, "Send");

  const proposalCard = (m) => {
    const p = m.proposal;
    const lines = p.changes.map((c) => h("div", { style: { marginTop: "6px" } }, h("b", {}, `${c.op === "add" ? "Add" : "Replace"} ${c.case.id}`), ` — ${c.case.title || ""}`,
      c.diff ? h("div", { style: { marginLeft: "12px" } }, diffLines(c.diff)) : h("div", { class: "small muted", style: { marginLeft: "12px" } }, `${(c.case.steps || []).length} step(s): ${(c.case.steps || []).map((s) => s.description || s.action).slice(0, 6).join(" → ")}${(c.case.steps || []).length > 6 ? " …" : ""}`)));
    if (p.settings) lines.push(h("div", { style: { marginTop: "6px" } }, h("b", {}, "Suite settings: "), Object.keys(p.settings).join(", ")));
    const resolved = p.status !== "pending";
    return h("div", { class: "banner", style: { marginTop: "6px" } }, h("div", {}, h("b", {}, "Proposed change "), badge(p.status === "pending" ? "pending" : p.status, p.status === "pending" ? "warn" : (p.status === "dismissed" ? "" : "ok"))),
      ...lines, p.notes && p.notes.length ? h("div", { class: "small muted", style: { marginTop: "4px" } }, p.notes.join(" · ")) : null,
      !resolved ? h("div", { class: "toolbar", style: { marginTop: "8px" } },
        h("button", { class: "primary", title: host.applyLabelHint || "", onclick: async () => { try { await host.applyToDefault(p.changes, p.settings, m.content.slice(0, 120)); await POST("/api/chat/mark", { path: host.path, id: m.id, status: "applied" }); toast(host.appliedMessage || "Applied", "ok"); await draw(); } catch (e) { fail(e); } } }, host.applyLabel || "Apply"),
        h("button", { title: "Keep the default exactly as it is; the proposal becomes a draft revision you can review in the History tab", onclick: async () => {
          try { const r = await POST("/api/chat/draft", { path: host.path, id: m.id, note: m.content.slice(0, 120) }); toast(`Saved as draft revision${r.drafts.length > 1 ? "s" : ""} (History tab)`, "ok"); if (host.afterDraft) host.afterDraft(); await draw(); } catch (e) { fail(e); } } }, "Save as a draft revision"),
        h("button", { onclick: async () => { await POST("/api/chat/mark", { path: host.path, id: m.id, status: "dismissed" }); await draw(); } }, "Dismiss")) : null);
  };
  const bubble = (m) => h("div", { style: { display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start", margin: "6px 0" } },
    h("div", { style: { maxWidth: "88%", padding: "8px 12px", borderRadius: "10px", background: m.role === "user" ? "var(--accent, #0b66c3)" : "var(--panel)", color: m.role === "user" ? "#fff" : "inherit", border: m.role === "user" ? "none" : "1px solid var(--line)", whiteSpace: "pre-wrap" } },
      m.content, m.role === "user" && m.case ? h("div", { style: { opacity: 0.8, fontSize: "11px" } }, "about " + m.case) : null, m.role === "assistant" && m.proposal ? proposalCard(m) : null,
      h("div", { style: { opacity: 0.6, fontSize: "11px", marginTop: "4px" } }, fmtTime(m.at))));
  const render = () => {
    const focus = host.focusId();
    clear(body).append(
      h("div", { class: "toolbar" }, h("b", {}, "Chat about "), selectInput([["", "the whole suite (create cases)"], ...host.cases().map((c) => [c.id, `${c.id} — ${c.title || ""}`])], focus || "", (v) => host.setFocus(v)),
        h("span", { class: "grow" }), h("button", { class: "ghost", onclick: async () => { if (await confirmBox("Clear the conversation", "Delete this suite's chat history from the disk?", "Clear", true)) { await DEL("/api/chat?" + q({ path: host.path })); draw(); } } }, "Clear conversation")),
      h("p", { class: "muted small" }, "The assistant sees this suite's settings, the case you chose in full and the other cases as an outline, plus the last messages. Nothing is sent until you press Send. Its answers are only proposals: you apply them (a new revision when saved) or save them as a draft."),
      messagesBox, h("div", { style: { marginTop: "8px" } }, input), h("div", { class: "toolbar" }, sendBtn, status));
    clear(messagesBox);
    if (!history.messages.length) messagesBox.append(h("div", { class: "muted small" }, "No messages yet."));
    history.messages.forEach((m) => messagesBox.append(bubble(m)));
    messagesBox.scrollTop = messagesBox.scrollHeight;
  };
  const send = async () => {
    const text = input.value.trim(); if (!text) return;
    sendBtn.disabled = true; status.textContent = "Waiting for the AI service… (this can take up to a minute)";
    try {
      const r = await POST("/api/chat", { path: host.path, message: text, case: host.focusId() || undefined, suite: host.getSuite() });
      input.value = ""; await draw();
    } catch (e) { status.textContent = ""; toast(e.message, "bad"); }
    sendBtn.disabled = false; status.textContent = "";
  };
  sendBtn.onclick = send;
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); } });
  await draw();
}

/* the editor's Chat tab: proposals are applied into the editor (unsaved) */
function chatTab(body) {
  if (!S.app.ai.configured) { clear(body).append(aiBanner()); return; }
  return chatPanel(body, {
    path: E.path, getSuite: () => cleanSuite(E.suite), cases: () => E.suite.cases, focusId: () => (E.suite.cases[E.ci] || {}).id,
    setFocus: (id) => { const i = E.suite.cases.findIndex((c) => c.id === id); if (i >= 0) E.ci = i; },
    applyLabel: "Apply to the editor", applyLabelHint: "Changes the open suite in the editor; Save creates a new revision", appliedMessage: "Applied in the editor — Save to keep it (a new revision)",
    applyToDefault: async (changes, settings, note) => {
      changes.forEach((c) => {
        const i = E.suite.cases.findIndex((x) => x.id === c.case.id), neu = JSON.parse(JSON.stringify(c.case));
        if (i >= 0) { if (E.suite.cases[i].ai_recommendation) neu.ai_recommendation = E.suite.cases[i].ai_recommendation; E.suite.cases[i] = neu; E.ci = i; } else { E.suite.cases.push(neu); E.ci = E.suite.cases.length - 1; }
      });
      if (settings) Object.entries(settings).forEach(([k, v]) => { if (k === "variables") E.suite.variables = { ...(E.suite.variables || {}), ...v }; else E.suite[k] = v; });
      E.nextSource = "chat"; E.nextNote = note; mark();
    },
  });
}

/* the AI assistant page's Chat tab: choose a file; applying saves a new revision straight away */
async function chatPage(panel, tests) {
  const ok = tests.filter((t) => ["testbot", "testbot-excel"].includes(t.format));
  const st = { path: ok[0] ? ok[0].path : "", focus: "", suite: null };
  const box = h("div", {});
  const load = async () => { clear(box); if (!st.path) return; try { st.suite = (await GET("/api/test?" + q({ path: st.path }))).suite; } catch (e) { box.append(h("div", { class: "banner bad" }, e.message)); return; }
    st.focus = (st.suite.cases[0] || {}).id || "";
    chatPanel(box, { path: st.path, getSuite: () => st.suite, cases: () => st.suite.cases, focusId: () => st.focus, setFocus: (id) => (st.focus = id),
      applyLabel: "Apply (saves a new revision)", applyLabelHint: "Writes the change into the suite file as a new revision (see the History tab in the editor)", appliedMessage: "Saved as a new revision",
      applyToDefault: async (changes, settings, note) => {
        const cur = await GET("/api/test?" + q({ path: st.path })), suite = cur.suite;
        changes.forEach((c) => { const i = suite.cases.findIndex((x) => x.id === c.case.id); if (i >= 0) suite.cases[i] = { ...c.case, ...(suite.cases[i].ai_recommendation ? { ai_recommendation: suite.cases[i].ai_recommendation } : {}) }; else suite.cases.push(c.case); });
        if (settings) Object.entries(settings).forEach(([k, v]) => { if (k === "variables") suite.variables = { ...(suite.variables || {}), ...v }; else suite[k] = v; });
        await PUT("/api/test", { path: st.path, suite, base_mtime: cur.mtime, overwrite: true, source: "chat", note });
        st.suite = (await GET("/api/test?" + q({ path: st.path }))).suite;
      } });
  };
  panel.append(h("p", { class: "muted" }, "Talk to the assistant about a test suite: ask it to create a case, change steps, explain a step. Its answers are proposals you review as a diff; applying one keeps a revision, so you can always go back."),
    h("div", { class: "card" }, field("Test suite (file)", selectInput(ok.map((t) => t.path), st.path, (v) => { st.path = v; load(); }))), box);
  load();
}
