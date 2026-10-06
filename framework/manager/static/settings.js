"use strict";
/* Settings: which folders the manager uses, the test runner, AI provider, scheduler. */

routes.settings = async (main) => {
  const st = await GET("/api/state");
  const s = deep(st.settings); s.ai = s.ai || {}; s.server = s.server || {}; s.agent = s.agent || {}; s.logging = s.logging || {}; s.email = s.email || {};
  if (s.ai.ssl_use_os_truststore === undefined) s.ai.ssl_use_os_truststore = s.ai.use_system_certs !== false;
  const result = h("div", { style: { flex: 1 } });
  const dirRow = (label, key, kind, hint) => field(label, h("div", {}, textInput(s, key), h("div", { class: "hint" }, hint + " – resolves to: ", h("span", { class: "mono" }, st.dirs[kind].path), st.dirs[kind].exists ? "" : "  (will be created)")));
  main.append(h("h1", {}, "Settings"), h("p", { class: "muted" }, "Stored in ", h("span", { class: "mono" }, st.workspace_file), ". Relative folders are relative to that file's folder."),
    h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Folders"), h("div", { class: "grid g2" },
      dirRow("Test cases folder", "test_cases_dir", "test_cases", "Where suites (.json / .xlsx) and sessions are kept"),
      dirRow("Results folder", "results_dir", "results", "Where test results and screenshots are read from and written to"),
      dirRow("Schedules folder", "schedules_dir", "schedules", "One .json file per schedule"),
      dirRow("Config folder", "config_dir", "config", "Contains environments.yaml and apriso_control_map.yaml"))),
    h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Test runner"),
      field("Path of testbot.exe (leave empty to find it automatically)", textInput(s, "runner_command", { placeholder: "C:\\testbot\\testbot.exe" }),
        st.runner ? "Found: " + st.runner.join(" ") : "Not found – “Run” buttons and schedules cannot start tests until it is set."),
      h("p", { class: "muted small" }, "The manager itself does not need the runner: you can edit, import and view results without it. It is only needed to start tests.")),
    h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "AI assistant"), h("div", { class: "grid g3" },
      field("Provider", selectInput(st.ai.providers, s.ai.provider || "anthropic", (v) => (s.ai.provider = v))), field("Model (optional)", textInput(s.ai, "model", { placeholder: "provider default" })),
      h("div", { class: "small muted", style: { paddingTop: "18px" } }, st.ai.configured ? "API key found in the environment." : "Missing environment variable(s): " + (st.ai.missing_env || []).join(", "))),
      h("p", { class: "muted small" }, "API keys are read from environment variables (ANTHROPIC_API_KEY, OPENAI_API_KEY, AZURE_OPENAI_*, GEMINI_API_KEY) and never saved in a file.")),
    h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Agentic testing (defaults for tests started from here)"),
      h("p", { class: "muted small" }, "Natural-language test cases are executed by an AI agent when the mode is agentic (or auto). These defaults are passed to the test runner as TESTBOT_* environment variables; a schedule item, suite or case can still override the mode. Leave empty to keep the runner's own defaults."),
      h("div", { class: "grid g4" }, field("Default mode", selectInput(["script", "agentic", "auto"], s.agent.mode || "", (v) => (s.agent.mode = v), { blank: "(script)" })),
        field("Agent provider", selectInput(["anthropic", "openai", "azure_openai"], s.agent.provider || "", (v) => (s.agent.provider = v), { blank: "(same as AI assistant)" })),
        field("Agent model (optional)", textInput(s.agent, "model")), field("Screenshots to the model", selectInput(["auto", "off", "always"], s.agent.vision || "auto", (v) => (s.agent.vision = v))),
        field("Max actions per test", numInput(s.agent, "max_steps")), field("App profile file (optional)", textInput(s.agent, "profile", { placeholder: "config/app_profile_apriso.yaml" })),
        h("div", { style: { paddingTop: "18px" } }, checkInput(s.agent, "allow_destructive", "allow delete / pay / send actions")),
        h("div", { style: { paddingTop: "18px" } }, checkInput(s.agent, "learn", "let the agent suggest improvements to the test case after a passing run (a recommendation you review in the editor)")))),
    h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Company network: root certificates and proxy"),
      h("p", { class: "muted small" }, "On networks that inspect HTTPS traffic, the AI service is blocked (“certificate verify failed”) until your company's ROOT certificate is trusted. Give its file here. These settings are used by the AI assistant here, and are passed to tests started from this manager (AI element finder, REST calls)."),
      h("div", { class: "grid g2" },
        field("Root certificate file or folder (.pem / .crt / .cer)", textInput(s.ai, "ssl_ca_bundle_file", { placeholder: "C:\\certs\\company-root.pem" }), "Several files or folders can be separated with “;”. Ask your IT department for the root certificate."),
        field("Proxy address (optional)", textInput(s.ai, "proxy", { placeholder: "http://proxy.company.com:8080" }), "Leave empty to use the HTTPS_PROXY environment variable, if any.")),
      checkInput(s.ai, "ssl_use_os_truststore", "Also trust the operating system's certificate store (on Windows: where IT installs company certificates)"),
      h("div", { class: "toolbar", style: { marginTop: "10px" } }, h("button", { onclick: async () => {
        clear(result).append(h("span", { class: "muted" }, "Testing…"));
        try {
          const r = await POST("/api/ai/test", { ssl_ca_bundle_file: s.ai.ssl_ca_bundle_file || "", ssl_use_os_truststore: !!s.ai.ssl_use_os_truststore, proxy: s.ai.proxy || "" });
          clear(result).append(h("div", { class: "banner " + (r.ok ? "ok" : "bad"), style: { whiteSpace: "pre-wrap", margin: 0 } }, (r.ok ? "✔ " : "✘ ") + r.message + "\n" + r.url));
        } catch (e) { clear(result).append(h("div", { class: "banner bad" }, e.message)); }
      } }, "Test connection to the AI service"), result),
      h("p", { class: "muted small" }, "The same can be set with environment variables: TESTBOT_SSL_CA_BUNDLE_FILE, TESTBOT_SSL_USE_OS_TRUSTSTORE (0/1), TESTBOT_PROXY (same idea as mom-mcp\u2019s MOM_MCP_SSL_CA_BUNDLE_FILE). If your company provides an internal AI gateway, set ANTHROPIC_BASE_URL / OPENAI_BASE_URL / AZURE_OPENAI_ENDPOINT.")),
    h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Email (SMTP) defaults"),
      h("p", { class: "muted small" }, "The mail server for the emails testbot sends when a session or test group finishes. These are the defaults for every environment; an environment's own email settings (Environments & SQL page) and a session's or group's own email setting override them field by field. Nothing is sent unless “Send when” is set. The password is never stored here: type the NAME of an environment variable (or a .env entry) that holds it. The settings are passed to the tests the manager starts."),
      ...emailForm(s.email, "default", { scope: "settings" })),
    h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "Logging"),
      h("div", { class: "grid g3" }, field("Log level", selectInput([["", "Default (info)"], ["debug", "Debug – everything, with the data of each step"], ["info", "Info – one line per step"], ["warning", "Warning – failed checks and problems only"], ["error", "Error – only what could not run"]], s.logging.level || "", (v) => (s.logging.level = v)))),
      h("p", { class: "muted small" }, "Every run writes a step log (run.log) next to its screenshots; open it from the run on the Results page and filter it by level. The manager itself logs to logs/manager.log in the workspace folder. Passwords and other secrets are masked. The level applies to the manager and to the tests it starts; it applies to the manager the next time it starts. A --log-level given on the command line wins.")),
    h("div", { class: "card" }, h("h3", { style: { marginTop: 0 } }, "This manager"), h("div", { class: "grid g3" }, field("Address", textInput(s.server, "host")), field("Port", numInput(s.server, "port"))),
      h("p", { class: "muted small" }, "Applies the next time the manager starts. Keep the address 127.0.0.1 unless you understand the risk: anyone who can reach the port and has the access token can change your test files and start tests.")),
    h("div", { class: "toolbar" }, h("button", { class: "primary", onclick: async () => { try { await PUT("/api/workspace", { ...s, email: cleanEmail(s.email) }); toast("Saved", "ok"); await refreshPills(); go("settings", { r: Date.now() }); } catch (e) { fail(e); } } }, "Save settings")));
};
