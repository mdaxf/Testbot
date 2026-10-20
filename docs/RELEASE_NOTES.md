# testbot 0.1.1.1 — release notes (first official release)

testbot is a data-driven web UI test framework: Excel or JSON test cases drive Playwright browser automation, with SQL-backed test data and pass/fail reporting (HTML and JUnit XML). This is the first official release. It ships as one self-contained Windows application folder, with no Python, `pip` or `playwright install` needed on the target machine.

## What's in the package

| Item | What it is |
|---|---|
| `testbot.exe` | The test runner (command line). Runs suites, chosen cases, sessions, load runs. |
| `testbot-manager.exe` | The management UI in your browser, plus the background scheduler. |
| `testbot-recorder.exe` | Records a browser session as a test case (JSON suite, Excel or Playwright script). |
| `_internal/` | Python, libraries and Chromium shared by the three programs. Keep it next to them. |
| `config/` | Sample configuration: `environments.yaml`, `apriso_control_map.yaml`, `app_profile_apriso.yaml`. |
| `.env.example` | Every setting the programs read, with defaults. Copy to `.env`. |
| `test_cases/` | Examples (`examples/`) and the Apriso demo suite (`ad_demo/`). |
| Documentation | `USER_MANUAL.md`, `README.md`, `AGENTIC_TESTING_RESEARCH.md`. |

Copy or zip the whole folder, not a single `.exe`. The manager creates its own workspace files (`testbot-workspace.json`, `reports`, `groups`, `schedules`, `logs`) on first start.

## Features

**Author tests**
- Record in the browser, write in Excel or JSON, import (multi-tab Excel, Apriso AutomaticTest scenarios), or chat with the AI assistant.
- **Agentic mode:** write the test in plain language (objective, expected outcomes, constraints) and an AI agent carries it out. Anthropic, OpenAI, Azure OpenAI and Gemini are supported. Pilot feature; see manual section 21.

**Run tests**
- A whole suite, chosen cases in your order (`--case`, `--tag`, `depends_on`), a session across several files, or a test group.
- On demand, on a schedule, or from a pipeline. Load runs use many parallel workers.
- Database steps (read-only checks and `sql_exec`), REST calls and message-bus steps.

**Results and history**
- Results by test case, suite or session, with final result and final test time. HTML and JUnit XML reports.
- **Test groups** track a test effort such as UAT, with charts, manual results and sign-off.
- Email when a session or group completes.
- Every change is a **revision** (only the default runs). The agent's findings arrive as **recommendations** to review (replace, merge, skip), including **self-healing** targets for scripted steps.

**Manager UI**
- Pages: Test cases (editor, History, Chat), Variables, Import, Sessions & schedules, AI, Results, Groups, Reviews, Settings.
- The left menu and the page scroll separately, and in the test case editor the case list and the editor scroll separately.

**Security and reliability**
- HTML reports escape every value, and result files are served under a restrictive content-security policy.
- Database access from the agent, the "Try SQL" dialog and SQL checks is read-only by design: always rolled back, values sent as bound parameters, statement keywords and state-changing functions blocked. *A read-only database login is still the real control.*
- The agent checks the site of the page before it clicks or types, and stays within allowed hosts.
- Ids and file paths are validated; reports, screenshots and results stay inside their folders. Email passwords may only come from `TESTBOT_SMTP_*` variables, and a login is never sent without TLS.
- Dead database connections are replaced, load runs stop at their duration limit, and schedule runs can be cancelled.

## Good to know

- **`sql_query` is read-only.** Use `sql_exec` to write data.
- **Quote keyword-named columns.** A bare column named `sleep`, `save`, `set`, `return` and similar words can be refused; write `[sleep]`. Qualified (`t.Save`) and aliased (`AS Return`) names are fine.
- **Ids.** A `suite_id` or `session_id` may use letters, digits, `-`, `_` and `.` only, up to 60 characters, and may not start with `.`.
- **Screenshot steps** write inside the run folder. A relative name works; an absolute path or `..` is an error. Evidence file names for case ids with other characters get a short code added.
- **Agent hosts.** A page that redirects to a site that is not allowed (for example a single sign-on page) cannot be used. Add the host with `TESTBOT_AGENT_ALLOWED_HOSTS` or the profile's `allowed_hosts`.
- **Try SQL and passwords.** A connection string typed in the dialog can use `{env:...}` only after it is saved in an environment. A typed password works.
- **Explicit `--case` and `depends_on`** refuse a draft or superseded revision; make that revision the default first.
- **Manager network access.** Binding `0.0.0.0` or `::` works from the same computer through `localhost`; remote use needs an explicit bind address and the access token.
- **First setup.** Edit `config/environments.yaml` (the `win_apr23` entry is an example with a placeholder read-only login), and copy `.env.example` to `.env` with the keys and passwords you need.

## Known limitations

- No network-level guard against the agent's first request to a disallowed host; the checks before and after each action, plus going back, cover the usual case.
- `TESTBOT_AGENT_HEAL` is read from the environment but nothing else uses it yet.
- Not covered by automated tests: the load-run result drain, per-suite connection overrides and the Windows process-tree cancel (they need running processes or a real database).
- The package targets Windows.
