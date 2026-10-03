# Web Test Framework

Data-driven web UI testing: Excel/JSON test cases -> Playwright browser automation ->
SQL-backed test data -> pass/fail reporting (HTML + JUnit XML for Azure DevOps).

For a task-oriented guide aimed at testers (recorder, Excel/JSON authoring, variables, settings, running,
reports, troubleshooting), see [docs/USER_MANUAL.md](docs/USER_MANUAL.md).

## Setup

    pip install -e .
    playwright install chromium

That's it for running tests that only use semantic locators (`role`/`label`/`css`/etc.)
-- see Configuration below for the environment file and the optional AI vision provider.

### Building a standalone executable (`testbot.exe`)

Ships as one Windows `.exe` with the Python runtime, every pip dependency, and Chromium
itself all baked in -- nothing downloads or installs on the machine that runs it. Build
it in a venv on this machine:

    python -m venv .venv-build && .venv-build\Scripts\activate
    pip install -e . pyinstaller
    set PLAYWRIGHT_BROWSERS_PATH=0
    python -m playwright install chromium
    pyinstaller testbot.spec

`PLAYWRIGHT_BROWSERS_PATH=0` is what makes this possible: it installs Chromium *inside*
`site-packages/playwright/...​/.local-browsers/` instead of the global user cache, so
`testbot.spec`'s `collect_all("playwright")` picks the browser binaries up as ordinary
package data. `scripts/testbot.py` bakes the same env var into its own runtime
(`os.environ.setdefault(...)`), so the resulting `.exe` always looks in its own bundled
copy first -- it never touches `%LOCALAPPDATA%\ms-playwright` or the internet. Verified
by hiding that global cache directory and running the built `.exe` from a machine state
with no venv active and no env vars set: it still ran a full suite correctly (Chromium
launched, screenshots captured, assertions passed) using only what's inside the `.exe`.

The output (`dist/testbot.exe`, ~400 MB -- Chromium and its headless-shell variant
dominate that) needs `config/environments.yaml` sitting next to it (copy the `config/`
folder over) and your test case files passed by path, same as the source scripts:

    testbot.exe suite --suite test_cases\examples\sample_suite.json --env demo
    testbot.exe session --plan test_cases\examples\session\session_plan.yaml

Same subcommands, same flags as `run_suite.py`/`run_session.py` (see "Running a single
suite"/"Running a session" below) -- `testbot.exe` is the same `framework/cli.py` logic
behind one frozen entrypoint, not a separate code path.

**Known trade-offs of this approach** (PyInstaller onefile + Playwright's bundled Node
driver + Chromium + pandas/pyodbc's native extensions is a genuinely fragile
combination to build, even though it's verified working right now):
- Startup is slower than running from source -- a onefile build re-extracts itself to a
  temp directory on every launch.
- Antivirus/SmartScreen may flag an unsigned onefile PyInstaller binary; code-signing it
  is outside this project's scope.
- The build is Windows-only and machine-architecture-specific (built here as x64); it
  won't run on Linux/macOS or ARM.
- Headless-shell (the binary Playwright actually launches for `headless=True` when
  browsers come from the *global* cache) turned out to be unnecessary at runtime in
  this bundled local-browsers layout -- headless launches use `chrome.exe` instead --
  but Playwright's own internal registry still checks for headless-shell's presence, so
  it can't be dropped from the bundle even though it's never executed. Don't try to
  trim it without re-verifying with the clean-machine test described above.

## Configuration

### Environments (`config/environments.yaml`)

One block per environment, selected by `--env` on the CLI:

```yaml
demo:
  base_url: "https://the-internet.herokuapp.com"
  connections: {}

qa:
  base_url: "https://qa.example.local/portal"
  connections:
    default: "Driver={ODBC Driver 17 for SQL Server};Server=QA-SQL01;Database=YourDb;Trusted_Connection=yes;"
```

- `base_url` seeds the `{base_url}` variable available in every test case.
- `connections` maps a connection name (a step's `connection` field, default
  `"default"`) to an ODBC connection string, used by `sql_query`/`sql_exec` steps and
  SQL-sourced `variables`. Leave `{}` if a suite never touches SQL.

A session plan names its own environment directly (`environment: "demo"` in the plan
file) instead of taking `--env` on the CLI -- see "Running a session" below.

### AI vision provider (optional)

Only needed if a test case uses `strategy: "ai"`, or relies on the automatic AI
fallback when a semantic locator matches 0 or more than 1 elements. Playwright itself
needs none of this, and the large majority of steps never touch an LLM at all -- see
"Element targeting" below. Set the credentials for one provider (`.env.example` has the
full list) and pick it with `AI_VISION_PROVIDER` (default `anthropic`):

| `AI_VISION_PROVIDER` | Required env vars |
|---|---|
| `anthropic` (default) | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `azure_openai` | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` |
| `gemini` | `GEMINI_API_KEY` |

`AI_VISION_MODEL` overrides the default model name for the active provider (ignored
for `azure_openai`, which uses `AZURE_OPENAI_DEPLOYMENT` as the model identifier). A
missing/unknown provider or missing credentials raises a clear error naming exactly
what's missing, rather than a raw SDK exception.

## Running a single suite

    python scripts/run_suite.py --suite <path> --env <environment>

| Flag | Required | Meaning |
|---|---|---|
| `--suite` | yes | Path to a `.json` or `.xlsx` test case file |
| `--env` | yes | Environment name from `config/environments.yaml` |
| `--headed` | no | Show the browser window instead of running headless |
| `--report-dir` | no | Where reports/screenshots go (default `reports`) |
| `--device` | no | Playwright device name, e.g. `"iPhone 13"` -- overrides every case's own `device` (see "Viewport / device" below) |
| `--viewport` | no | Custom size `WIDTHxHEIGHT`, e.g. `390x844` -- overrides every case's own `viewport`; ignored if `--device` is set |

Try it against the working example:

    python scripts/run_suite.py --suite test_cases/examples/sample_suite.json --env demo

`TC-001` runs for real against the public login practice page at
the-internet.herokuapp.com. `TC-002` in the same file is illustrative only -- it
references a SQL query and screen that don't exist; it demonstrates the SQL-lookup ->
capture -> SQL-verify pattern documented below.

**Exit code:** `0` if every case passed, `1` if any case failed or errored -- safe to
use directly as a CI gate.

**Reports** land in `--report-dir` (default `reports/`):

- `<suite_id>.html` -- human-readable report, one row per step: description, action,
  resolved data used, status, expected vs. actual, error, and a screenshot link.
- `<suite_id>-junit.xml` -- Azure Pipelines' "Publish Test Results" task ingests this
  directly into the ADO Tests tab, no REST integration needed for that path.
- `<suite_id>-result.json` -- the machine-readable evidence record. Same suite/case/step
  structure and order as the source test case, with the action executed, resolved data
  used, status, actual vs. expected, and a screenshot path filled in for every step.
- `<suite_id>_<timestamp>/` -- one screenshot per step, for both passing and failing
  steps, named `<case_id>_step<n>_<pass|fail|error>.png`. Each run gets its own
  timestamped folder so repeated runs never overwrite each other's evidence.

## Running a session (multiple files, in order)

A single suite file is one unit of test cases; a **session** runs several suite files
(JSON and/or Excel, in any mix) in a required order and produces one combined report.

    python scripts/run_session.py --plan <path>

| Flag | Required | Meaning |
|---|---|---|
| `--plan` | yes | Path to a session plan (`.yaml`/`.yml`/`.json`) |
| `--headed` | no | Show the browser window instead of running headless |
| `--report-dir` | no | Where reports/screenshots go (default `reports`) |
| `--device` | no | Overrides every file/case's own `device` for the whole session |
| `--viewport` | no | Overrides every file/case's own `viewport`; ignored if `--device` is set |

Try it against the working example:

    python scripts/run_session.py --plan test_cases/examples/session/session_plan.yaml

A session plan lists the files in order; the plan's own `environment` picks the
`config/environments.yaml` entry (no `--env` flag for sessions):

```yaml
session_id: "SESSION-LOGIN-LOGOUT"
session_name: "Login then logout, sharing the browser session across files"
environment: "demo"
files:
  - path: "test_cases/examples/session/01_login.json"
  - path: "test_cases/examples/session/02_logout.json"
    shares_state_with_previous: true
```

By default each file starts clean -- a fresh browser context and a fresh variable
store, with its own cases staying isolated from each other exactly like running that
file with `run_suite.py`. Set `shares_state_with_previous: true` on a file to instead
continue the *exact* browser context (cookies, storage, current page) and variable
store left by the previous file's last case; that file's own cases then also chain
continuously (no per-case reset), since sharing only makes sense as one continuous run.
`01_login.json` above logs in; `02_logout.json` inherits that session and proves it by
navigating straight to `/secure` with no re-login before logging out.

**Failure policy:** if any case in a file fails or errors, the session stops before
running the next file (the file itself still finishes its own cases per its own
`on_case_fail` setting; only moving on to the *next file* is blocked). A single suite
file can still be run standalone via `run_suite.py` exactly as before -- sessions are
additive, not a replacement.

**Exit code:** same as a single suite -- `0` if every case across every file passed.

**Reports:** the same three report types as a single suite -- `<session_id>.html`,
`<session_id>-junit.xml` (one `<testsuite>` per file), `<session_id>-result.json` -- plus
one screenshot folder for the whole session, with filenames prefixed by suite ID so
cases with the same ID across different files never collide.

## Authoring test cases

Suites are JSON (see `test_cases/examples/sample_suite.json`) or Excel (see
`test_cases/examples/excel_demo.xlsx`, built by the layout below).

### Excel workbook layout

- **`Suite`** sheet (one row): `SuiteID, SuiteName, Environment, OnCaseFail,
  DefaultStepDelayMs, Device, ViewportWidth, ViewportHeight` (the last three per
  "Viewport / device" below).
- **`Index`** sheet: `Sequence, CaseID, Run` -- one row per test case, controlling both
  which cases run (`Run` = `Y`/`N`, default `Y`) and their execution order (`Sequence`).
  `CaseID` must exactly match a sheet name elsewhere in the workbook.
- **`CommonData`** sheet (optional): `VarName, Source, Query, Connection, Column, Type,
  Pattern, Value` -- test data shared by every case (credentials, lookups), defined once
  instead of repeated per case. Same shape as a JSON case's `variables` entries.
- **One sheet per test case**, named exactly as its `CaseID`: a `Title`/`AreaPath`/
  `Priority`/`Preconditions`/`Device`/`ViewportWidth`/`ViewportHeight` metadata block
  (`Key | Value` rows -- the last three override the Suite sheet's for this case only),
  a blank row, then the Steps table starting at the row whose column A cell reads
  `StepNo`. Step columns mirror ADO Test Case fields (`Description`, `Action`,
  `TargetStrategy`/`Value`/`Name`, `Input`, `Query`, `Connection`,
  `ExpectedType`/`Value`/`Target*`/`Column`, `CaptureVar`/`From`/`Target*`/`Column`,
  `DelayAfterMs`) so cases can be exported/imported via ADO's own Excel/CSV import.

### Element targeting (`target.strategy`)

Priority order, closest to how a person reads the page first: `role` (+ `name` for the
accessible name) -> `label` -> `placeholder` -> `text` -> `testid` -> `css`/`xpath`
(escape hatch) -> `ai` (natural-language description, resolved via a vision-capable LLM
call against a numbered-element screenshot -- see "AI vision provider" above). The `ai`
path is used automatically as a fallback whenever a semantic locator matches 0 or more
than 1 elements, and can also be requested explicitly by setting `strategy: "ai"`.

Add `"nth": N` (1-based) to any target to pick a specific match generically -- "the 1st
item in the list" -- among otherwise-identical elements that don't have a unique label,
e.g. `{"strategy": "role", "value": "button", "name": "Delete", "nth": 2}` for the 2nd
of several identical Delete buttons. Works the same way on a step's own `target`, and on
`expected.target`/`capture.target`, so you can click, assert, or capture text/value from
a specific position in a list or grid row without a unique selector. Pair it with the
`count_equals` expected type (which counts the *base* set, so don't set `nth` on that
target) to verify how many matches exist. See `test_cases/examples/nth_demo.json`.

Two more strategies for tables/grids, and a way to disambiguate any strategy:

- `row_containing` -- `value` is text the row must contain; finds the `<tr>` without
  knowing which column the text is in or writing XPath ancestor lookups, e.g.
  `{"strategy": "row_containing", "value": "John Smith"}` to click or assert on that row.
- `table_cell` -- `value` is a locator for the table itself, `row`/`col` (1-based) pick
  the cell, e.g. `{"strategy": "table_cell", "value": "#orders", "row": 3, "col": 2}`.
  Scoped to `tbody tr` so header rows don't shift the count.
- `scope` (any strategy) -- a CSS selector narrowing the search root, e.g.
  `{"strategy": "row_containing", "value": "Bach", "scope": "#table1"}` when the page
  has more than one table and the same text could match a row in either. Add `nth: 1`
  to a `css` target instead for "the 1st row of the table" positionally, e.g.
  `{"strategy": "css", "value": "#table1 tbody tr td", "nth": 1}`.

See `test_cases/examples/table_scroll_demo.json` for all of these against a real page
with two tables of identical-looking data (the scoping ambiguity is real, not
theoretical -- it's what that example had to work around).

### Dynamic data

Two mechanisms, both landing in the same per-case variable store, so `{name}` works in
`input`, `target.value`, `expected.value`, and SQL `query` text anywhere later in the case:

- **`variables` (case-level, resolved once before step 1)** -- `source: "sql"` (a
  SELECT; the first row/column is captured), `source: "faker"` (any Faker generator via
  `type`, e.g. `bothify` + `pattern`), or `source: "constant"`.
- **`capture` (step-level, resolved as the run progresses)** -- pull a value out of
  what the *app* just produced: `from: "element_text"` / `"element_value"` (read a
  control after an action), `from: "sql_column"` (after a `sql_query` step), `from: "url"`.

A value captured from the UI can drive a later SQL query exactly as easily as a SQL
lookup can drive a later UI action -- see `TC-002` in the sample suite for both
directions in one case.

### Actions

`navigate`, `click`, `type`, `select`, `hover`, `press`, `scan`, `scroll`, `upload`,
`check`, `wait`, `wait_until`, `rest_call`, `bus_subscribe`, `bus_wait`, `bus_publish`, `screenshot`, `sql_query`, `sql_exec`, `set_var`, `assert`.

`wait_until` polls a `target` element until a condition in `input` holds, instead of
sleeping a fixed time like `wait`. Conditions: `hidden` (default -- e.g. the "Loading..."
spinner is gone or absent, so the menu has finished loading), `visible`, `value:<text>`
(input value equals), `has_value` (input value non-empty), `text:<text>` (element text
equals), `text_contains:<text>`. It fails the step if the condition isn't met within the
step's `timeout_ms` (default 10000; Excel column `TimeoutMs`). Example:
`{"Action": "wait_until", "target": {"strategy": "text", "value": "Loading"}, "input": "hidden", "timeout_ms": 30000}`.

`scan` is deliberately different from `type`: it takes **no target**. It just sends
keystrokes to whatever element currently has focus (`page.keyboard.type`), the same way
a real barcode/keyboard-wedge scanner does -- the scanner has no idea which field is
focused, it just types. Use it right after whatever puts focus on the screen's required
field (an auto-focus on load, or a `click` step if you need to force it), then verify
the value actually landed where it should have with a normal `assert`/`value_equals`
step targeting that field by name -- don't just trust that focus was where you expected.
See `test_cases/examples/scan_demo.json` for a full worked example (scan into Username,
verify it landed there, repeat for Password, then submit).

`scroll` has two modes. With a `target` and no `input`: scrolls that element into view
(the common case -- "scroll down until this button is visible"). With `input` as
`"down:500"` / `"up:500"` / `"right:300"` / `"left:300"` / `"top"` / `"bottom"`: scrolls
by that amount (or all the way, for top/bottom) -- the target's own scrollable area if
one is given, otherwise the whole page.

### Recorder (auto-generate a test case by using the site)

    python scripts/testbot.py record        # or just run testbot.exe with no arguments
    python -m framework.recorder

A small window opens: enter the start URL, pick the browser (`chromium` = the bundled
one, or `msedge` / `chrome` if installed), press **Start recording**, and use the site in
the browser window that opens. Clicks, typed values, selects, Enter/Escape and page
navigations appear live in the step list. **Stop** (or close the browser), review/delete
steps, then **Save** as any of: a JSON suite, an Excel workbook, or a standalone Playwright
Python script (default folder `test_cases/recorded/`).

- Targets are chosen the way the runner resolves them: `testid` > `label`/`placeholder` >
  `role`+name > `text`, falling back to a `css` selector when nothing readable is unique.
- After a click/Enter that changes the page, the step gets a `url_contains` assertion; the
  first page and any address-bar navigation become `navigate` steps using `{base_url}`.
- Password fields are never saved as typed text: they become `{password}` (`{password_2}`…),
  defined in the suite's `variables` as `CHANGE_ME` -- fill in the real value (or supply it
  as an environment variable named `PASSWORD` in the Playwright script).
- **Insert wait_until…** adds a hand-specified step at the current point in the recording --
  e.g. wait until text `Loading` is `hidden` -- because a loading indicator can't be detected
  automatically. Use **Delete selected step** to remove mistakes.
- Limits: elements inside iframes are recorded but the runner's page-level locators won't
  find them; file uploads are recorded with an empty path to fill in; hover/drag/right-click
  and keyboard shortcuts other than Enter/Escape aren't captured.

`click` steps accept `config: {"js_click": true}` to dispatch a DOM click instead of a coordinate click
(useful when another element overlays the target).

### Converting Apriso AutomaticTest JSON scenarios

    python scripts/testbot.py convert-apriso --in test_cases/apriso-bpa-pls.json
    # -> test_cases/apriso-bpa-pls.testbot.json  (use --out / --map to override)

Turns a C# "AutomaticTest" scenario (`TestCase` / `Screen` / `Elements` with `Input`, `Action`,
`Result` blocks) into a testbot suite. Control types map to the CSS the Selenium framework uses
(`GetElement.cs`); the mapping lives in `config/apriso_control_map.yaml`, so you can correct or add
control types without code changes. What the converter reproduces:

- Login and opening the screen, with `login_url`, `login_name`, `login_password` as suite
  variables to fill in (`CHANGE_ME` placeholders -- credentials from the source file are not copied).
- Screens live in the `.apr-fullscreen-tab` iframe -> targets carry `frame`; open popups
  (`.apr-popup`) win over the page behind them -> targets carry `scope_if_present`.
- `Wait.ToLoad()` -> a `wait_until` on `.apr-ctspinner` hidden after each click-type step, plus a
  500 ms suite step delay.
- The `#` token in values ("#_TestLine") -> `{seq}`, read from the AUTOMATED_TEST_SEQUENCE table and
  re-read after each `AutomatedTestSequence` SQL call, like the C# runner does at use time.
- `SQLNonQuery` -> `sql_exec`; `SQLQuery` results -> `sql_query` + `sql_result_equals` with `config.poll` (re-runs the query until it matches or `timeout_ms`, since the app may still be committing). These need SQL
  connection `default` (suite `connections` or `--env`).
- `FormCheckbox` -> the `check` action (idempotent: clicks the label only when the state differs).

Unmapped control types (and `@variable` references) are reported as warnings and skipped.
Targets use `frame`/`scope_if_present` (see `Target` in `framework/models.py`), which any suite can use.

### Agentic testing (`framework/agent/`, pilot)

Natural-language test cases (`objective` / `data_hints` / `expect` / `constraints`, or an `agent` step) executed by an AI agent; mode via
`TESTBOT_MODE` / `--mode` / suite+case `mode` (script default, agentic, auto). `observer.py` (frame/pop-up-aware numbered page description + grids),
`actions.py` (tools mapped to ordinary steps; assertions run by the deterministic engine), `policy.py` (host allowlist, destructive-action guard,
secrets never literal, budgets, stuck detection), `verdict.py` (a pass needs passing checks for every expected outcome; else fail/inconclusive),
`llm.py` (Anthropic / OpenAI / Azure tool-calling, enterprise TLS), `loop.py`, `integration.py` (results, `agent-traces/`, `agent-generated/` script
export, secrets redacted). New result status `inconclusive`. `config/app_profile_apriso.yaml` is an application profile. Tested with a scripted
stand-in model and a fake Anthropic server through the real CLI/SDK; not yet with a real model. Section 21 of `docs/USER_MANUAL.md`,
design in `docs/AGENTIC_TESTING_RESEARCH.md`.

### Run log (`framework/logs.py`)

Standard `logging` under the `testbot.*` loggers (areas: runner, step, sql, integration, agent, manager, scheduler). `TESTBOT_LOG_LEVEL` / `--log-level` (debug, info, warning, error), `TESTBOT_LOG_LEVELS=area=level,...`,
`TESTBOT_LOG_FILE`, `TESTBOT_LOG_CONSOLE=off`. Console + a `run.log` in every run folder (`logs.attach_run_log`, called from `Orchestrator.start_run_dir`); the manager also writes `<workspace>/logs/manager.log`
and shows a run's log with level/area/search filters (`/api/result/log`). Secrets are masked (`logs.redact`, `logs.looks_secret`). New code: `log = logs.get("area")` and log at the level that fits.

### Folder build (`testbot-app.spec`)

`pyinstaller testbot-app.spec` builds ONE application folder (onedir): three thin launchers (`testbot.exe`, `testbot-manager.exe`, `testbot-recorder.exe`) plus a shared `_internal` with Python, libraries and Chromium. Nothing is unpacked to `%TEMP%` at start-up, which restricted networks and endpoint protection tend to block in single-file exes. The old `testbot.spec` / `manager.spec` / `recorder.spec` (single-file) are kept.

### Version and IACF notice (`framework/version.py`)

One source for the version (**0.1.1.1**) and the notice (© 2026 IACF — All rights reserved): the exe file properties (`framework/versioninfo.py` feeds the
PyInstaller specs), `--version`, the manager footer/About, the recorder title, the HTML report footer, the wheel (`pyproject.toml` reads it) and the watermark.
`framework/branding.py` stamps the IACF logo (hardcoded in `framework/brand_logo.py`, no external file) on evidence screenshots and on screenshots sent to the agent's model (`TESTBOT_WATERMARK=off` disables);
`scripts/stamp_images.py` stamps `docs/images/*.png` once (manifest-based, idempotent). To release a new version edit `__version__` only.

### `.env` file (`framework/envfile.py`)

`NAME=value` lines from `.env` (current folder, program folder, manager workspace folder) are loaded at startup by `testbot`, `testbot-manager`,
`run_suite`/`run_session`; real environment variables always win; only names are printed. Use it for API keys (`OPENAI_API_KEY`, `AI_VISION_PROVIDER`, ...).

### Company root certificates / proxy (`framework/tlsconfig.py`)

For networks that re-sign HTTPS with a company root CA. One SSL context trusts the OS store (Windows: where IT installs the CA),
certifi and extra root certificates (`.pem`/`.crt`/binary `.cer`, a folder, or `;`-separated) -- mirroring mom-mcp's
`ssl_ca_bundle_file` / `ssl_use_os_truststore`. Env vars `TESTBOT_SSL_CA_BUNDLE_FILE`, `TESTBOT_SSL_USE_OS_TRUSTSTORE`,
`TESTBOT_PROXY` (aliases `AI_CA_BUNDLE`, `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `AI_PROXY`; `HTTPS_PROXY` is honoured). Used by the AI
element finder, the manager's AI assistant and `rest_call` (`config.ca_bundle`). The SDK clients are built with the SDK's own
`DefaultHttpxClient` (SDK versions use different httpx builds and reject a plain `httpx.Client`). The manager's Settings page has a
"Test connection" button and passes the settings to the runs it starts. Section 11.5 of `docs/USER_MANUAL.md`.

### Manager: browser UI + scheduler (`framework/manager/`, `scripts/manager.py`, `manager.spec`)

    python scripts/manager.py                       # UI at http://127.0.0.1:8780/?token=...  (opens the browser)
    python scripts/manager.py scheduler run         # background scheduler (also: run-once, install, uninstall, status)
    python scripts/manager.py run-schedule <id>

A local web app (stdlib `http.server` + a dependency-free JS UI in `framework/manager/static/`) for listing/creating/editing
test cases (catalog-driven dropdowns, auto step numbers, validation), importing arbitrary Excel/CSV/JSON through a column
mapping (`importer.py`), variables/SQL/environments, sessions and schedules (`schedules.py`, `scheduler.py`), results
(`results.py`) and AI generate/optimize/convert (`ai.py`; same providers as the AI element finder, keys from env vars).
Design: UI, scheduler and `testbot.exe` share only the files of one *workspace* (`testbot-workspace.json` -> test-cases /
results / schedules / config folders) so each runs without the others; the manager never imports Playwright (it starts
`testbot.exe` for runs), so `testbot-manager.exe` is ~50 MB. Security: 127.0.0.1 only, per-launch access token in a
SameSite=Strict cookie, Host-header check, every path confined to the workspace folders. The editor works on the raw suite
JSON (not the normalised model) so hand-written files keep their shape; saves keep a `.bak` and detect stale edits. See
section 20 of `docs/USER_MANUAL.md`.

### Visual / order checks (`css_equals`, `has_class`, `list_matches`)

`framework/assertions/style_checks.py`. Colours are compared as the browser computes them (any CSS colour syntax on
either side). `list_matches` reads the top N rows of a `<table>` or Apriso `.DynamicGrid` (rows under `[id*='content']`,
skipping `tr.Dummy`) and checks text / background / colour / css / class per row, in order; `item` / `style_item` /
`rows` select what to read. See section 9.1 of `docs/USER_MANUAL.md`.

### Load testing (multiple worker processes)

    python scripts/testbot.py suite --suite S.json --workers 5 --iterations 3 --ramp-up 20
    python scripts/testbot.py session --plan P.yaml --workers 3 --duration 300

`framework/runner/load.py` starts N independent worker processes (multiprocessing `spawn`, so the frozen exe
works too via `multiprocessing.freeze_support()` in `scripts/testbot.py`). Each worker runs the *whole* suite/session
on its own, repeated `iterations` times or until `duration_s`, started spread over `ramp_up_s`. Settings can also
live in the suite/plan (`workers`, `iterations`, `duration_s`, `ramp_up_s`; Excel: `Workers`, `Iterations`,
`DurationSec`, `RampUpSec`); CLI flags win. Defaults (1 worker, 1 iteration) keep the normal in-process path.
Built-in variables `{worker_id}` and `{iteration}` (both `1` in a normal run) let concurrent copies use distinct data.
Load runs keep screenshots for failures only unless `--screenshots` says otherwise, write
`<report-dir>/worker-NN/iter-NNN/` reports plus `load-summary.json`, and refuse more than 10 workers without
`--confirm-load`. See section 17 of `docs/USER_MANUAL.md`.

### Settings live in the suite (environments.yaml is an optional fallback)

A suite can carry everything it needs, so `--env` and `config/environments.yaml` are no
longer required:

- `base_url` -- seeds `{base_url}` (Excel: `BaseUrl` on the Suite sheet).
- `connections` -- SQL name -> ODBC string (Excel: a `Connections` sheet with `Name`,
  `ConnectionString`).
- `variables` -- shared by every case, same shape as a case's `variables` (Excel: the
  existing `CommonData` sheet). A case's own variable of the same name wins.

Precedence is suite value > `environments.yaml` value: `--env X` (or the suite's
`environment` field, or a session plan's `environment`) still loads that block as the
fallback, and suite `base_url`/`connections` override it per file -- including inside a
multi-file session, where each file's settings apply only to that file. `{base_url}` is
simply undefined (and a step using it errors clearly) if neither source sets it.
`test_cases/examples/rest_bus_demo.json` uses no environment at all.

### REST calls and message-bus steps (`config` block)

`rest_call`, `bus_subscribe`, `bus_wait` and `bus_publish` take **no target**; everything
is configured inline in the step's `config` object (Excel: a `Config` column holding JSON).
Every string inside `config` supports `{variable}` substitution, so credentials and IDs can
come from `variables`/`CommonData`/earlier captures. `timeout_ms` bounds the step. A failed
expectation marks the step **failed** (not errored). Paths use `a.b[0].c` (optional `$` prefix).

**`rest_call`** -- `url`, `method` (default GET), `params`, `headers`, `body` (object/array
is sent as JSON, a string as-is), `auth` (`{"type":"bearer","token":..}` /
`{"type":"basic","username":..,"password":..}` / `{"type":"header","name":..,"value":..}`),
`verify_tls` (default true), `expect_status` (number or list), `expect_json`
(`{path: value}`), `capture` (`{var: path}`; `$status` and `$body` are also available).

    {"StepNo": 1, "Description": "Create order", "Action": "rest_call", "config": {
        "url": "{api}/orders", "method": "POST", "auth": {"type": "bearer", "token": "{token}"},
        "body": {"item": "A-100", "qty": 2}, "expect_status": 201,
        "expect_json": {"status": "New"}, "capture": {"order_id": "id"}}}

**Message bus (MQTT / AMQP / Kafka)** -- `config.broker` is `mqtt`, `amqp` or `kafka`.
Subscribe *before* the step that triggers the message (a UI click, a `rest_call`, a
`bus_publish`), then `bus_wait` for it; messages received between the two are kept.
Subscriptions are closed automatically at the end of the case.

- `bus_subscribe` -- `name` (default `"default"`), `host`, `port`, `username`, `password`,
  `tls`, plus per broker: MQTT `topic` (wildcards ok), `qos`; AMQP `queue` (existing) *or*
  `exchange` + `routing_key` (private queue is bound), `vhost`; Kafka `topic`, `group_id`,
  `bootstrap_servers` (or host/port), `sasl_mechanism`. Kafka starts from the latest offset.
- `bus_wait` -- `name`, `match` (`{path: value}` on the JSON payload; non-JSON payloads are
  plain strings), `topic_contains`, `expect_json`, `capture` (`{var: path}`; `$topic`, `$body`).
  Fails if no matching message arrives within `timeout_ms`.
- `bus_publish` -- broker + connection fields as above, `topic` (`routing_key` for AMQP;
  `exchange` optional), `payload` (object/array sent as JSON, else as text).

See `test_cases/examples/rest_bus_demo.json`. Client libraries are `paho-mqtt`, `pika`,
`kafka-python`; the MQTT path is tested against a real broker, AMQP and Kafka are not yet.

### Pacing (delays between steps)

Playwright auto-waits for DOM/navigation readiness, but not for app-side async work (a
business rule finishing after a click, an Apriso DFC step completing). Two knobs, both
optional:

- `TestSuite.default_step_delay_ms` -- applied after every step's action, before that
  step's result is checked and before the next step starts. Default `0`.
- `TestStep.delay_after_ms` -- overrides the suite default for one specific step only.

### Viewport / device (desktop, mobile, custom sizes)

Every case runs in its own fresh browser context, so different cases in the *same*
suite can each target a different layout -- e.g. one case for desktop, another for
mobile, another for a custom size. Priority: a CLI flag (`--device`/`--viewport`, see
"Running a single suite" above) wins over a case's own setting, which wins over the
suite's.

- `device` -- a Playwright built-in device name (`"iPhone 13"`, `"Pixel 7"`, `"iPad
  Mini"`, `"Desktop Chrome"`, ...). Bundles viewport, user agent, device scale factor,
  touch support -- real emulation, not just a resized window. This framework always
  launches Chromium, though, so an "iPhone" device means Chromium rendering at iPhone
  viewport/UA/touch, not real Safari/WebKit.
- `viewport: {"width": W, "height": H}` -- a custom size only (no UA/touch/scale
  changes), used when `device` isn't set.

Settable on `TestSuite` (applies to every case in the file) and overridable per
`TestCase` (that case only). See `test_cases/examples/device_demo.json` -- three cases
in one suite, one desktop (default), one `device: "iPhone 13"`, one custom
`viewport: 800x1200`; verified their screenshots actually come out at three different
resolutions.

Within a session, a file/case flagged `shares_state_with_previous` inherits the exact
browser context it's chained from -- its own device/viewport is ignored if it differs,
since cookies/storage continuity is the point of chaining.

### Pass/fail semantics

The first failed step stops that test case immediately (later steps assume earlier
state). The suite continues to the next case by default; set `"on_case_fail": "stop"`
on the suite to abort the whole run on the first failing case instead.

## Not built yet (phase 2)

Live Azure DevOps REST integration (pulling Test Plan/Suite/Case work items, pushing
Test Run results directly via the Test Results API) isn't implemented -- today's ADO
story is the JUnit XML above, plus authoring cases in the ADO-shaped Excel format. Add
a `framework/integrations/ado_client.py` module when that's actually needed.

## Known v1 limitations

- Chromium only; sequential case execution (no parallelism) -- both are config points
  to revisit, not architectural limits.
- SQL query/exec text is built via simple `{name}` substitution, not bound parameters
  -- suites are authored by trusted test engineers, but don't feed raw untrusted input
  into a `query` string.
- The `visible`/`hidden` assertion types don't support the `ai` locator strategy yet.
