"use strict";
/* Variables, connections and SQL: the editor tab, the read-only "try a query" dialog, and the Environments page. */

/* ---- one variables table (works on any {name: {source,...}} object) ---- */
function varTable(vars, onchange, { title, hint }) {
  const box = h("div", { class: "card" });
  const draw = () => {
    clear(box);
    const rows = Object.entries(vars).map(([name, v]) => {
      const nameIn = h("input", { value: name, onchange: () => {   // rename = re-key while keeping order
        const nn = nameIn.value.trim(); if (!nn || nn === name) { nameIn.value = name; return; }
        if (vars[nn]) { toast("A variable named " + nn + " already exists", "bad"); nameIn.value = name; return; }
        const entries = Object.entries(vars).map(([k, val]) => [k === name ? nn : k, val]); Object.keys(vars).forEach((k) => delete vars[k]); entries.forEach(([k, val]) => (vars[k] = val)); onchange(); draw(); } });
      const src = v.source || "constant";
      const cells = [];
      if (src === "constant") cells.push(field("Value", textInput(v, "value", { onchange })));
      if (src === "faker") cells.push(field("Generator", textInput(v, "type", { onchange, list: "fakers" })), field("Pattern (bothify)", textInput(v, "pattern", { onchange, placeholder: "ORD-####" })));
      if (src === "sql") cells.push(field("Query", h("textarea", { rows: 2, oninput: (e) => { v.query = e.target.value; onchange(); }, spellcheck: false }, v.query || "")),
        field("Connection", textInput(v, "connection", { onchange, placeholder: "default" })), field("Column", textInput(v, "column", { onchange })));
      return h("tr", {}, h("td", { style: { width: "170px" } }, nameIn),
        h("td", { style: { width: "130px" } }, selectInput(Object.keys(S.catalog.variable_sources), src, (val) => { const keep = { source: val }; Object.keys(v).forEach((k) => delete v[k]); Object.assign(v, keep); onchange(); draw(); })),
        h("td", {}, h("div", { class: "grid " + (cells.length > 2 ? "g3" : "g2") }, cells), src === "sql" ? h("button", { class: "small", onclick: () => sqlTryDialog(v.query || "", v.connection || "default") }, "Try query") : null),
        h("td", { style: { width: "40px" } }, h("button", { class: "icon danger", onclick: () => { delete vars[name]; onchange(); draw(); } }, "✕")));
    });
    box.append(h("div", { class: "toolbar" }, h("h3", { style: { margin: 0 } }, title), h("span", { class: "grow" }),
      h("button", { onclick: () => { let n = 1; while (vars["var" + n]) n++; vars["var" + n] = { source: "constant", value: "" }; onchange(); draw(); } }, "+ Add variable")),
      hint ? h("p", { class: "muted small" }, hint) : null,
      rows.length ? h("table", {}, h("tbody", {}, rows)) : h("div", { class: "muted small" }, "None yet."),
      h("datalist", { id: "fakers" }, S.catalog.faker_types.map((f) => h("option", { value: f }))));
  };
  draw();
  return box;
}

/* ---- the editor tab ---- */
function varsTab(E, body, mark) {
  const s = E.suite; s.variables = s.variables || {}; s.connections = s.connections || {};
  const c = s.cases[E.ci] || s.cases[0]; if (c) c.variables = c.variables || {};
  body.append(
    h("div", { class: "banner" }, "Built-in variables you can always use: ", S.catalog.builtin_variables.map((v) => h("span", { class: "badge mono", style: { marginRight: "4px" } }, "{" + v + "}")),
      " · Captured values become variables from the step that captures them."),
    varTable(s.variables, mark, { title: "Suite variables (shared by every test case)", hint: "Values are available as {name} in inputs, targets, checks and SQL." }),
    c ? varTable(c.variables, mark, { title: "Variables of test case " + (c.id || ""), hint: "Override suite variables of the same name. Switch case in the Cases & steps tab." }) : null,
    connectionsCard(s, mark), sqlOverview(E, body, mark));
}

function connectionsCard(s, mark) {
  const box = h("div", { class: "card" });
  const draw = () => {
    clear(box);
    const rows = Object.entries(s.connections).map(([name, cs]) => {
      const nameIn = h("input", { value: name, onchange: () => { const nn = nameIn.value.trim(); if (nn && nn !== name && !s.connections[nn]) { const ent = Object.entries(s.connections).map(([k, v]) => [k === name ? nn : k, v]); s.connections = Object.fromEntries(ent); mark(); draw(); } } });
      const csIn = h("input", { type: "password", value: cs, style: { width: "100%" }, oninput: () => { s.connections[name] = csIn.value; mark(); } });
      const show = h("button", { class: "icon", title: "Show / hide", onclick: () => { csIn.type = csIn.type === "password" ? "text" : "password"; } }, "👁");
      return h("tr", {}, h("td", { style: { width: "150px" } }, nameIn), h("td", {}, csIn), h("td", { style: { width: "150px", whiteSpace: "nowrap" } }, show,
        h("button", { class: "icon", onclick: () => sqlTryDialog("SELECT 1 AS connected", name, cs) }, "Test"), h("button", { class: "icon danger", onclick: () => { delete s.connections[name]; mark(); draw(); } }, "✕")));
    });
    box.append(h("div", { class: "toolbar" }, h("h3", { style: { margin: 0 } }, "SQL connections of this suite"), h("span", { class: "grow" }),
      h("button", { onclick: () => { let n = s.connections.default ? 2 : 0; const name = n ? "db" + n : "default"; s.connections[name] = ""; mark(); draw(); } }, "+ Add connection")),
      h("p", { class: "muted small" }, "Name → ODBC connection string, e.g. Driver={ODBC Driver 18 for SQL Server};Server=host;Database=db;UID=user;PWD=secret;Encrypt=no;TrustServerCertificate=yes;  Suite connections override environments.yaml. Stored in the test case file – keep such files private."),
      rows.length ? h("table", {}, h("tbody", {}, rows)) : h("div", { class: "muted small" }, "None – connections then come from the selected environment (Environments & SQL page)."));
  };
  draw();
  return box;
}

function sqlOverview(E, body, mark) {
  const items = [];
  Object.entries(E.suite.variables || {}).forEach(([n, v]) => v.source === "sql" && items.push({ where: `suite variable {${n}}`, ref: v, key: "query", conn: v.connection || "default" }));
  E.suite.cases.forEach((c, ci) => {
    Object.entries(c.variables || {}).forEach(([n, v]) => v.source === "sql" && items.push({ where: `${c.id}: variable {${n}}`, ref: v, key: "query", conn: v.connection || "default" }));
    (c.steps || []).forEach((st, si) => (st.action === "sql_query" || st.action === "sql_exec") && items.push({ where: `${c.id}: step ${si + 1} ${st.action}`, ref: st, key: "query", conn: st.connection || "default", jump: [ci, si] }));
  });
  return h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "All SQL in this suite"),
    items.length ? table(["Where", "Connection", "Query", ""], items.map((it) => h("tr", {}, h("td", { class: "small" }, it.where), h("td", {}, it.conn),
      h("td", { class: "mono small", style: { whiteSpace: "pre-wrap" } }, (it.ref[it.key] || "").slice(0, 260)),
      h("td", { style: { whiteSpace: "nowrap" } }, h("button", { onclick: () => sqlTryDialog(it.ref[it.key] || "", it.conn) }, "Try"),
        it.jump ? h("button", { onclick: () => { E.tab = "steps"; E.ci = it.jump[0]; E.si = it.jump[1]; drawEditor(); } }, "Open step") : null)))) : h("div", { class: "muted small" }, "No SQL used yet."));
}

/* ---- try a query (read-only) ---- */
async function sqlTryDialog(query, connName, connString) {
  const envs = (await GET("/api/environments")).environments;
  const suite = typeof E !== "undefined" && E ? E.suite : null;
  const st = { query, conn: connName || "default", cs: connString || (suite && suite.connections && suite.connections[connName || "default"]) || "", env: (suite && suite.environment) || Object.keys(envs)[0] || "" };
  const out = h("div", {}), q = h("textarea", { rows: 4, spellcheck: false, oninput: () => (st.query = q.value) }, st.query);
  const csIn = h("input", { type: "password", value: st.cs, placeholder: "(use the connection above)", oninput: () => (st.cs = csIn.value), style: { width: "100%" } });
  modal({ title: "Try a query (read-only: SELECT only, max 25 rows)", wide: true, body: h("div", {},
    h("div", { class: "grid g3" }, field("Connection name", h("input", { value: st.conn, oninput: (e) => (st.conn = e.target.value) })),
      field("Environment (if the connection is not in the suite)", selectInput(Object.keys(envs), st.env, (v) => (st.env = v), { blank: "(none)" })),
      field("…or a connection string", csIn)),
    field("SQL", q), h("div", { class: "toolbar" }, h("button", { class: "primary", onclick: async () => {
      clear(out).append(h("span", { class: "muted" }, "Running…"));
      try {
        const r = await POST("/api/sql/test", { query: st.query, connection_string: st.cs || undefined, env: st.env, connection: st.conn });
        clear(out).append(r.columns.length ? table(r.columns, r.rows.map((row) => h("tr", {}, row.map((c) => h("td", { class: "mono small" }, c))))) : h("div", { class: "muted" }, "(no columns)"),
          h("div", { class: "muted small" }, r.rows.length + " row(s)" + (r.truncated ? " – more rows exist" : "")));
      } catch (e) { clear(out).append(h("div", { class: "banner bad" }, e.message)); }
    } }, "Run query")), out), buttons: [{ label: "Close" }] });
}

/* ---- Environments page (config/environments.yaml) ---- */
routes.environments = async (main) => {
  const { environments } = await GET("/api/environments"); const envs = deep(environments);
  const drivers = S.app.odbc_drivers || [];
  const box = h("div", {});
  const draw = () => {
    clear(box);
    Object.entries(envs).forEach(([name, env]) => {
      env.connections = env.connections || {};
      const conns = Object.entries(env.connections).map(([cn, cs]) => {
        const csIn = h("input", { type: "password", value: cs, style: { width: "100%" }, oninput: () => (env.connections[cn] = csIn.value) });
        return h("tr", {}, h("td", { style: { width: "140px" } }, cn), h("td", {}, csIn), h("td", { style: { width: "150px", whiteSpace: "nowrap" } },
          h("button", { class: "icon", onclick: () => (csIn.type = csIn.type === "password" ? "text" : "password") }, "👁"),
          h("button", { class: "icon", onclick: () => sqlTryDialog("SELECT 1 AS connected", cn, csIn.value) }, "Test"),
          h("button", { class: "icon danger", onclick: () => { delete env.connections[cn]; draw(); } }, "✕")));
      });
      box.append(h("div", { class: "card" },
        h("div", { class: "toolbar" }, h("h3", { style: { margin: 0 } }, name), h("span", { class: "grow" }),
          h("button", { class: "danger", onclick: async () => { if (await confirmBox("Remove environment", `Remove ${name} from the file?`, "Remove", true)) { delete envs[name]; draw(); } } }, "Remove environment")),
        h("div", { class: "grid g2" }, field("Base URL", textInput(env, "base_url", { placeholder: "http://server/app" }))),
        h("h3", {}, "SQL connections"), conns.length ? h("table", {}, h("tbody", {}, conns)) : h("div", { class: "muted small" }, "None."),
        h("button", { onclick: async () => { const n = await promptBox("New connection", "Connection name", "default"); if (n) { env.connections[n] = ""; draw(); } } }, "+ Add connection")));
    });
    if (!Object.keys(envs).length) box.append(h("div", { class: "empty" }, "No environments yet. Suites can carry their own base URL and connections, so environments are optional."));
  };
  main.append(h("h1", {}, "Environments & SQL"),
    h("p", { class: "muted" }, "Named settings (base URL + SQL connections) in ", h("span", { class: "mono" }, "config/environments.yaml"), ". A suite can pick one as a fallback; the suite's own values win."),
    drivers.length ? null : h("div", { class: "banner warn" }, "No SQL Server ODBC driver was found on this computer – install “ODBC Driver 17/18 for SQL Server” to try queries here or run SQL steps."),
    h("div", { class: "toolbar" }, h("button", { onclick: async () => { const n = await promptBox("New environment", "Name", "qa"); if (n && !envs[n]) { envs[n] = { base_url: "", connections: {} }; draw(); } } }, "+ Add environment"),
      h("button", { class: "primary", onclick: async () => { try { await PUT("/api/environments", { environments: envs }); toast("Saved", "ok"); } catch (e) { fail(e); } } }, "Save"),
      h("span", { class: "muted small" }, "Saving keeps the previous file as environments.yaml.bak")), box);
  draw();
};
