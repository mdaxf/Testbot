# Agentic AI testing for testbot — research and design proposal

*Status (2026-09-30): research and proposal, **plus a built pilot** of phases 1–3 of §9 (mode switch, natural-language cases, agent loop, assertion-backed verdicts, trace, script export) — see section 21 of `docs/USER_MANUAL.md`. Not yet run with a real model on a real application; the evaluation in §8 has not been run. Decisions taken: own agent core inside testbot; pilot first; screenshots allowed (default `auto`); Anthropic/OpenAI/Azure OpenAI providers. Not built: heal-as-diff proposals, the manager's Proposals screen, agent + load mode.*

**Question.** Can testbot run a test that is written in **natural language** ("create a production line and check it shows up
in the list"), where an **AI agent decides which test data to use and what to validate**, and can an **environment setting**
switch between that *agentic* way of testing and the existing *script-based* auto test?

**Short answer.** Yes, and testbot is unusually well placed to do it, because it already owns the expensive parts (a Playwright
runtime, AI providers with enterprise TLS/proxy support, an assertion engine, SQL access, evidence reports, a recorder and a
management UI). The safest design is **not** "let the AI run everything every time", but:

1. an **agent mode** that executes a natural-language test live and produces an evidence-backed verdict, **and**
2. the ability to **turn a successful agent run into an ordinary testbot script** (deterministic, cheap, reviewable) that then runs
   in script mode, with the agent stepping in only to *propose* repairs when the script breaks.

That is the "hybrid" that the research report's own findings point to. A switch `TESTBOT_MODE=script|agentic|auto` selects the behaviour.

---

## 1. What the research report says (distilled)

Source: *Agentic AI Test Solutions — Research Report (Sept 2026)*, 19 tools, 53 sources.

| Finding in the report | What it means for testbot |
|---|---|
| **Two architectures.** *NL-driven execution* (an agent executes plain English live, vision + DOM) versus *NL → code generation* (English becomes deterministic Playwright/Selenium code). A few tools do both (KaneAI, Autify). | We should support both: agent execution **and** export to a testbot script. |
| **Biggest risk is verdict trust.** Non-determinism, false positives, and self-healing that *silently rewrites assertions* and hides real bugs. Best practice: every heal is a human-reviewable diff; a **pass needs corroborating evidence** (DOM state, URL change, network response, console, screenshot — Kane CLI's design). | A pass must be backed by deterministic checks that the framework runs, not by the model saying "looks good". Heals are proposals, never silent edits. |
| **Cost models punish growth** (per-step credits, per-test pricing; some charge for AI-generated and healed steps). | Cost must be measurable and capped per test; cached/exported scripts avoid repeat model cost (Kane CLI "cached replay"). |
| **Slower than scripts** (LLM reasoning per step). Mitigation: cached replay or small purpose-built models. | Agent for authoring/exploration/healing; scripts for regression runs. |
| **Graveyard.** Octomind and ZeroStep are dead (2026); Magnitude pivoted; Reflect absorbed; Shortest and auto-playwright dormant. "Prefer tools whose output you own or that are open source." | Avoid depending on a start-up's cloud. Own the loop; keep output as testbot scripts. |
| **Fastest path to "write English, watch it run"**: Kane CLI (free CLI, real Chrome, self-heal, verified verdict, Playwright export; needs a vendor account). **Best in-code options:** Stagehand (MIT, TypeScript-first, `act/extract/observe/agent`, caching), Microsoft Playwright Test Agents / playwright-mcp (free), Midscene.js (MIT, pure-vision, HTML reports with reasoning traces; can use self-hosted models). | Prior art to copy ideas from (set-of-marks, act/observe primitives, cached replay, reasoning-trace reports). See §4 for build-versus-adopt. |
| **Not automatable**: exploratory strategy, judging acceptable quality, complex conditional business logic beyond the NL ceiling; humans still own test strategy. | Agent mode reduces authoring and maintenance effort; it does not remove the need to state *what matters*. |
| **Immature evaluation culture**; vendor accuracy claims unverified. | We need our own evaluation harness (§8) before trusting any verdict. |

**Caveats about the report itself.** Its method is "web-index sources; no live browser verification". Prices and accuracy figures are
vendor or third-party claims and are marked directional by the report. I have not verified any tool myself, and the report does not
cover every relevant option (for example, it does not mention other open-source Python browser-agent libraries; I have not evaluated
any and do not recommend one from memory).

---

## 2. Requirements as I understand them

1. A test case can be **natural language** instead of a detailed script: an objective, optional data hints, optional expected outcomes.
2. The **agent determines the test data** (what to type/select/choose) rather than the author listing every value.
3. The **agent determines what to validate** (what a correct outcome looks like), in addition to anything the author states.
4. An **environment setting chooses the mode**: agentic testing or the existing auto test with test scripts.
5. It must work in an **enterprise environment** (TLS-inspecting proxy, internal AI gateway, no data leaving where it should not, secrets protected).

---

## 3. What testbot already has (reuse) and what is missing

| Already there | Use in agent mode |
|---|---|
| Playwright runtime, bundled Chromium, iframe/popup targeting (`frame`, `scope_if_present`) | The agent's hands and eyes. |
| Step actions and targets (`click`, `type`, `select`, `check`, `wait_until`, strategies role/label/text/css…) | The agent's **tool set** maps almost 1:1 to existing actions, so every agent action can be recorded as a normal step. |
| Assertion engine (`url_*`, `text_*`, `visible`, `count_equals`, `css_equals`, `has_class`, `list_matches`, `sql_result_equals`) | The agent's validations are **executed by this deterministic engine**, which is what makes a pass trustworthy. |
| SQL steps (`sql_query` with polling) and read-only query helper | Data lookup and persistence verification. |
| Variables (`constant`, `faker`, `sql`), built-ins (`{worker_id}`…), captures | Test-data mechanism; secrets stay as variables. |
| AI provider layer (Anthropic, OpenAI, Azure OpenAI, Gemini) + enterprise root-certificate/proxy support (`tlsconfig.py`) | Model access that already works behind company proxies. |
| Evidence: per-step screenshots, expected/actual, HTML/JUnit/JSON reports, manager results viewer | Extended with the agent's reasoning trace. |
| Recorder, converter, manager UI (AI generate/optimize/convert), scheduler, load mode | Natural-language editing, generated-script review, scheduled agent runs. |
| `strategy: "ai"` locator fallback ("set-of-marks" screenshot) | A seed for the agent's observation — but it only marks **top-level** elements and does not see inside iframes (Apriso screens live in one), so the agent needs a frame-aware observer. |

**Missing:** the agent loop itself; a compact observation format (accessibility/DOM summary across frames and popups); tool-calling
integration with the providers (current code only asks single-shot questions); a policy layer (budgets, domain allowlist, destructive-action
guard); the mode switch; a trace report; script export; heal-as-diff review; an evaluation harness.

---

## 4. Options: adopt or build

| Option | Pros | Cons |
|---|---|---|
| **A. Build a small agent core in testbot** (Python, reusing Playwright, providers, assertion engine) — *recommended* | Owns the output and the verdict logic; one runtime and one exe; reuses enterprise TLS/proxy and reports; provider-agnostic (including an internal or self-hosted OpenAI-compatible endpoint); no vendor-death risk; fits Apriso specifics (iframes, popups, spinners). | We build and tune the loop ourselves (the report's "immature eval culture" applies to us too); ongoing model-behaviour tuning. |
| **B. Adopt Stagehand or Midscene.js** | Mature primitives, caching/reports already designed; active open source. | TypeScript/Node runtime next to a Python exe (packaging, updates, second ecosystem); no shared assertion engine; enterprise TLS/proxy and Apriso iframes become our integration problem anyway. |
| **C. Use Playwright Test Agents / playwright-mcp with a coding agent** | Free; Microsoft-backed; good for *authoring* scripts in a repo. | Targets Playwright code, not testbot's JSON/Excel; needs a developer-operated coding agent; not a scheduled, evidence-producing runner. |
| **D. Use a vendor CLI/platform (e.g. Kane CLI, testRigor, mabl)** | Fastest to try; vendor handles self-healing. | Vendor account/cloud (approval in enterprise), cost growth, lock-in (tests often live in the platform), graveyard risk. |

**Recommendation: A**, borrowing ideas proven by B–D: set-of-marks observation, `act/observe/extract` style primitives, cached replay, reasoning-trace reports,
corroborated verdicts. Keep an adapter point so a different agent engine could be plugged in later if it proves better.

---

## 5. Proposed design

### 5.1 The mode switch

| Setting | Values | Meaning |
|---|---|---|
| `TESTBOT_MODE` (environment) | `script` (default), `agentic`, `auto` | See below. |
| `--mode` (CLI) and `mode` (suite/case field) | same | Command line > environment > suite/case > default `script`. |

- **`script`** — exactly today's behaviour: only scripted steps run; no model is used to drive the test. (The optional AI element finder keeps working as it does today.) A natural-language-only case is reported as *skipped: no script*, never guessed.
- **`agentic`** — natural-language objectives are executed by the agent. A case that only has a script is run by treating each step's description as an instruction (or run as a script — configurable), so one suite can mix both.
- **`auto`** — *use the script when there is one; use the agent when there is not; if a script step fails, optionally ask the agent to **propose** a repair* (`TESTBOT_AGENT_HEAL=off|suggest`). The run still reports the original failure; the proposal is a reviewable diff, never applied silently.

### 5.2 Natural-language test cases

A test case gains optional natural-language fields next to (or instead of) `steps`:

```json
{ "id": "TC-PL-01", "title": "Add a production line",
  "objective": "Log in as the planner, open Production Line Setup, add a new production line with a unique number and a description, and confirm it appears in the list.",
  "data_hints": "Line numbers are prefixed TEST_ and must be unique per run. The description is free text.",
  "expect": ["The new line is listed", "No error message is shown", "The line exists in the database (table WIP_LINE)"],
  "constraints": ["Do not delete any existing line"],
  "start_url": "{base_url}/apriso/portal" }
```

- Only `objective` is required. `expect` is optional — the agent also derives validations (§5.5).
- **Step-level natural language**: a new action `agent` with the instruction in `input` lets a scripted test delegate one hard part ("fill the order form with valid data and submit") and continue scripted — the same idea as Stagehand's `act()`.
- Excel gets matching columns (`Objective`, `DataHints`, `Expect`, `Constraints`); the manager gets a natural-language editor and a "run with agent" button.

### 5.3 The agent loop

```
objective + data hints + app profile + variables (names only, secrets hidden)
        │
        ▼
   observe  ──►  decide (model tool call)  ──►  act (Playwright, through existing step actions)
      ▲                                              │
      └────────── verify / record evidence ◄─────────┘      stop: objective done · budget hit · blocked · unsafe
```

- **Observation** (what the model sees, compact): URL and title; an accessibility-style list of the visible interactive elements of the page **and every frame and open pop-up**, each with role, name, value, state and a stable short id; visible text of the main region; recent network errors and console errors; optionally a screenshot with numbered marks (vision is optional and off by default to control cost/privacy). This generalises the existing set-of-marks code to iframes.
- **Tools** the model may call (each maps to an existing step action, so it is recorded as a real step): `navigate`, `click`, `type`, `select`, `check`, `press`, `wait_until`, `scroll`, `assert` (runs the deterministic assertion engine), `query_db` (read-only `SELECT`), `set_variable`/`note`, and `finish(verdict, summary)`.
- **Stop conditions**: objective reached; `max_steps`; token/cost budget; wall-clock limit; repeated no-progress; a blocked or unsafe action.
- **Waiting**: the observer waits for "idle" using an app profile (§5.8) and network quiet before each observation, so the model is not asked to reason about half-loaded screens.

### 5.4 How the agent decides test data

Priority order (each choice is written into the result, so a run can be reproduced and audited):

1. **Explicit data** from the test case, suite variables, captures from earlier steps.
2. **Constraints read from the UI itself** — input type, `maxlength`, `pattern`, `required`, `min/max`, dropdown options, date formats — so generated values are valid.
3. **Generators** — the existing `faker` sources and unique tokens (`{worker_id}`, `{iteration}`, a run id) so concurrent runs do not collide.
4. **Database lookups** (`query_db`, read-only) for data that must already exist (a real customer, an open order) when a connection is configured.
5. **Negative/edge data** only when the objective asks for it (for example "try to add a duplicate").

Rules: real personal data is never invented from memory; **secrets are referenced as `{variables}` and their values are never sent to the model** (the tool substitutes them at execution time); the set of values used is listed in the report under "Data decisions".

### 5.5 How the agent decides what to validate — and why a pass can be trusted

Validation comes in layers, strongest first:

| Layer | Source | Executed by |
|---|---|---|
| **Stated outcomes** | `expect` / the objective ("appears in the list") | Turned into concrete `assert` calls → the deterministic assertion engine |
| **Persistence** | The data really exists afterwards | `sql_query` / `query_db` when a connection is configured |
| **Implicit health checks** | No error banner/toast, no 4xx/5xx on the app's own calls, no new console errors, URL is a sensible next state | Built-in checks run after each action |
| **Model judgement** | Fuzzy cases (layout looks wrong, message makes sense) | Model, clearly labelled *AI judgement* and never enough on its own for a pass |

**Verdict rules** (the report's "corroborated verdict" principle):
- `pass` only if **every stated outcome has a passing deterministic assertion** and no health check failed.
- `fail` if an assertion failed, with expected/actual and the screenshot.
- `inconclusive` if the agent could not complete or could not verify an outcome (kept separate from pass and fail so it is never mistaken for either).
- `error` for runtime problems (cannot reach app, model unavailable, budget exhausted).

### 5.6 Outputs: trace, generated script, cached replay, heals as diffs

- **Reasoning trace** in the normal report: for each step the observation summary, the model's stated reason, the tool call, the result and a screenshot (Midscene-style).
- **Script export**: the actions and assertions that worked are emitted as an ordinary testbot JSON suite (targets that resolved, data as variables, the executed assertions). Save it → it runs in `script` mode with **no model cost and deterministic behaviour**. This is the NL → code half of the report's two families.
- **Replay cache**: agent mode first tries the saved script for the same objective (keyed by objective text, app/base URL and a hash of the screen structure); if a step fails, the agent takes over for that step only.
- **Heals are diffs**: a proposed repair is stored next to the test and shown in the manager as before/after; it is applied only when a person approves. A run that needed healing is marked *healed — review* (never a plain green).

### 5.7 Safety and enterprise concerns

| Concern | Control |
|---|---|
| Agent wanders off-site | **Domain allowlist** from `base_url`; navigation elsewhere is refused. |
| Destructive actions (delete, pay, send, approve) | Guard: blocked unless the objective explicitly allows it or `TESTBOT_AGENT_ALLOW_DESTRUCTIVE=1`; optional `approve` mode pausing for a human. |
| Data changes via SQL | `query_db` is read-only (`SELECT` only — the same checker the manager uses); no `sql_exec` tool in agent mode. |
| **Prompt injection** (text on a page telling the agent to do something) | Page content is passed as data in a clearly delimited block; tools are fixed and allowlisted; no secrets in context; the domain allowlist and destructive guard apply whatever the page says. |
| Secrets | Variables flagged secret are substituted at execution, redacted in traces and screenshots of password fields. |
| Data leaving the network | Clear list of what is sent (observation text, optional screenshot, objective, variable *names*); vision off by default; provider can be an **internal gateway or self-hosted OpenAI-compatible endpoint** (the report notes fully private stacks are possible with self-hosted vision models); company root certificate and proxy already supported. |
| Cost runaway | Per-test and per-run caps on steps, tokens and time; usage recorded in the report. |
| Audit | Every model call summarised in the trace; nothing applied to a saved script without approval. |

### 5.8 Apriso fit: an "app profile"

Apriso screens are awkward for a generic agent (iframe `.apr-fullscreen-tab`, pop-ups `.apr-popup`, spinners `.apr-ctspinner`, grids `.DynamicGrid` with a dummy first row,
form classes `.fc_<Name>`, buttons by `data-key`). The converter's control map already encodes this. An **app profile** (YAML, same spirit as `apriso_control_map.yaml`) would tell the agent and the observer:
which frame to look in, how to tell the screen is idle, how to read grids and forms, and naming conventions. Without it the agent must rediscover these on every step, which costs tokens and reliability;
with it the observation is short and the targets are the ones we already know work. This is also where "what is a sensible validation on this screen" hints (for example "a saved line appears in the grid and in WIP_LINE") can live.

### 5.9 Manager integration

Natural-language editor with "run with agent"; the AI assistant's *Generate* becomes *Generate and run* (agent run → script proposal); a **Proposals** inbox for generated scripts and heals with before/after; results viewer shows the reasoning trace, data decisions, verdict layer per assertion and token/cost usage; schedules can set the mode per item.

### 5.10 Settings (all optional; defaults keep today's behaviour)

| Variable | Default | Meaning |
|---|---|---|
| `TESTBOT_MODE` | `script` | `script` / `agentic` / `auto` |
| `TESTBOT_AGENT_PROVIDER`, `TESTBOT_AGENT_MODEL` | provider of the AI assistant | model used by the agent |
| `TESTBOT_AGENT_MAX_STEPS` | 40 | step cap per test |
| `TESTBOT_AGENT_MAX_TOKENS`, `TESTBOT_AGENT_TIMEOUT_S` | (to be set after measuring) | budget caps |
| `TESTBOT_AGENT_VISION` | `off` | `off` / `on` / `auto` (screenshot only when the text observation is not enough) |
| `TESTBOT_AGENT_HEAL` | `suggest` in `auto` | `off` / `suggest` |
| `TESTBOT_AGENT_ALLOW_DESTRUCTIVE` | `0` | allow delete/pay/send-type actions |
| `TESTBOT_AGENT_PROFILE` | none | path of the app profile |
| existing | | `TESTBOT_SSL_CA_BUNDLE_FILE`, `TESTBOT_PROXY`, `*_BASE_URL`, API keys |

---

## 6. Risks and how the design answers them

| Risk (from the report and from first principles) | Mitigation |
|---|---|
| **False pass** — the agent declares success wrongly | Pass requires deterministic assertions for each stated outcome; AI judgement alone never passes; `inconclusive` state. |
| **Non-determinism** — different path each run | Record and export the path as a script; run regressions in script mode; agent mode reports consistency over repeated runs (§8). |
| **Silent healing hides bugs** | Heals are proposals shown as diffs; run marked *healed — review*. |
| **Cost and speed** | Compact text observation, vision off by default, caps, cached replay, scripts for regression. Rough expectation (an estimate to be measured, not a figure from the report): a 15–25-step test uses tens of thousands to a few hundred thousand tokens and runs in minutes rather than seconds. |
| **Wrong data** (invalid, colliding, real PII) | Constraints read from the UI, unique tokens, no invented personal data, secrets never shown to the model, data listed in the report. |
| **Model quality on Apriso screens** | App profile; pilot on the Production Line Setup screen first; measure before widening. |
| **Platform risk** | Own the loop and the output; provider-agnostic; vendors optional. |
| **Overreach** — agent does damage | Allowlist, destructive guard, read-only DB, approval mode. |

---

## 7. What would not change

The existing script mode, recorder, converter, load mode, scheduler, manager and reports keep working as they do. Agent mode is additive and off unless
`TESTBOT_MODE` (or `--mode`/`mode`) asks for it.

---

## 8. How we would know it works: an evaluation plan

The report's warning about immature evaluation applies to any build we do, so the first deliverable includes a harness:

1. **Task set** — ~15 natural-language cases: the demo login site, a table-ordering/colour page, and 5–8 Apriso scenarios (for example the Production Line Setup flow we already scripted), each with a known-good outcome.
2. **Reliability** — run each N times (for example 5); report success rate and path consistency.
3. **False-pass rate** — run the same cases against **deliberately broken** variants (wrong colour, missing row, failed save) and count how often the agent passes them. Target: zero false passes on the seeded defects before any regression use.
4. **Cost and time** — tokens, model calls and seconds per test; compare with the script equivalent.
5. **Script export quality** — does the exported script pass on its own, and on re-run?
6. **Heal review** — seeded UI changes (renamed label, moved button): does the agent propose a correct diff, and never apply one silently?

Proposed acceptance for wider use (to be agreed): ≥ 90 % task success, **0** false passes on seeded defects, exported scripts pass ≥ 95 % on first replay.

---

## 9. Phased plan (rough engineering estimates)

| Phase | Content | Estimate |
|---|---|---|
| 1. Core | Mode switch; NL case fields; agent loop with provider tool-calling; frame/pop-up-aware observer; tool set mapped to existing actions; budgets and domain allowlist; trace in reports | 2–3 weeks |
| 2. Trust | Assertion-backed verdicts (pass/fail/inconclusive); data-decisions record; secrets handling; destructive guard; read-only DB tool | 1–2 weeks |
| 3. Scripts | Export to testbot script; replay cache; `auto` mode with heal-as-diff proposals | 1–2 weeks |
| 4. Apriso | App profile, pilot on real scenarios, tuning, evaluation harness results | 1–2 weeks (overlaps) |
| 5. Manager | NL editor, Proposals inbox, trace viewer, schedule-level mode | 1–2 weeks |

These are estimates, not commitments; phase 1 + 4 (a pilot on the Production Line Setup screen) would tell us quickly whether the approach is good enough on Apriso before investing in the rest.

---

## 10. Open questions

1. **Which model(s) are you allowed to use?** An approved provider, an internal gateway, or a self-hosted model changes the vision/privacy options.
2. **May page content and screenshots of the application leave the network?** If not, agent mode needs a self-hosted model and/or text-only observation.
3. **How destructive may tests be** on the target systems (create data only? delete? approve?), and is a test system available for the agent to learn on?
4. **Is a pass from an agent acceptable for release decisions, or only for exploration and script authoring?** (This decides how strict the verdict rules and the review step must be.)
5. **Should natural-language cases live in the same files as scripts**, or in separate requirement-style documents that generate scripts?

---

## Appendix: tools from the report that matter here

| Tool | Why it is relevant to testbot |
|---|---|
| TestMu AI — Kane CLI | Closest to the goal (NL objective → real browser → evidence-backed verdict → Playwright export); ideas to copy: corroborated verdicts, cached replay, export. Vendor account required. |
| Stagehand | `act / extract / observe / agent` primitives and auto-caching; model for step-level natural language (`agent` action). TypeScript. |
| Midscene.js | Pure-vision NL steps; HTML reports with reasoning traces; works with self-hosted models — model for the trace report and for private deployments. |
| Playwright Test Agents / playwright-mcp | Planner/generator/healer concept for code in a repo; model for heal proposals. |
| Momentic, Functionize, mabl, Autify, testRigor | Commercial platforms; relevant mainly as cautionary examples (per-step cost, self-heal false positives, tests living in the platform). |
| Octomind, ZeroStep | Dead in 2026 — the reason to own the loop and the output. |
