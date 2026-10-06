from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

TargetStrategy = Literal[
    "role", "label", "text", "placeholder", "testid", "css", "xpath", "url", "ai",
    "row_containing", "table_cell",
]
ActionType = Literal[
    "navigate", "click", "type", "select", "hover", "press", "scan", "scroll",
    "upload", "check", "agent", "wait", "wait_until", "rest_call", "bus_subscribe", "bus_wait", "bus_publish", "screenshot", "sql_query", "sql_exec", "set_var", "assert",
]
ExpectedType = Literal[
    "none", "url_equals", "url_contains", "text_equals", "text_contains",
    "visible", "hidden", "value_equals", "sql_result_equals", "count_equals",
    "css_equals", "has_class", "list_matches",
]
CaptureFrom = Literal["element_text", "element_value", "sql_column", "url"]
VariableSourceType = Literal["sql", "faker", "constant"]


class Viewport(BaseModel):
    width: int
    height: int


class Target(BaseModel):
    strategy: TargetStrategy
    value: str
    name: Optional[str] = None  # accessible name, used when strategy == "role"
    nth: Optional[int] = None  # 1-based: picks the Nth match generically ("the 1st item in the list")
    row: Optional[int] = None  # 1-based, used when strategy == "table_cell"
    col: Optional[int] = None  # 1-based, used when strategy == "table_cell"
    scope: Optional[str] = None  # CSS selector narrowing the search root -- disambiguates
    # e.g. two tables with the same row text, or two forms with the same button label
    frame: Optional[str] = None  # CSS selector of an <iframe> to search inside (Apriso screens live in one)
    scope_if_present: Optional[str] = None  # CSS selector used as the search root ONLY while it currently
    # matches something -- e.g. ".apr-popup": search inside an open popup, else the whole page/frame


class Expected(BaseModel):
    type: ExpectedType = "none"
    value: Optional[Any] = None
    target: Optional[Target] = None
    column: Optional[str] = None  # used when type == "sql_result_equals"
    property: Optional[str] = None  # css_equals: the CSS property to compare (default background-color)
    rows: Optional[str] = None  # list_matches: CSS selector (inside the target) for the rows; default = table body rows / children
    item: Optional[str] = None  # list_matches: CSS selector inside a row for the element whose text is read (default: the row)
    style_item: Optional[str] = None  # list_matches: element inside a row whose colours/classes are read (default: item)


class Capture(BaseModel):
    var: str
    from_: CaptureFrom = Field(alias="from")
    target: Optional[Target] = None
    column: Optional[str] = None  # used when from_ == "sql_column"

    model_config = {"populate_by_name": True}


class VariableSource(BaseModel):
    source: VariableSourceType
    connection: str = "default"       # used when source == "sql"
    query: Optional[str] = None       # used when source == "sql"
    column: Optional[str] = None      # used when source == "sql"
    type: Optional[str] = None        # used when source == "faker" (a Faker method name)
    pattern: Optional[str] = None     # used when source == "faker" and type == "bothify"
    value: Optional[Any] = None       # used when source == "constant"
    extra: dict[str, Any] = Field(default_factory=dict)


class TestStep(BaseModel):
    step_no: int
    description: str
    action: ActionType
    target: Optional[Target] = None
    input: Optional[str] = None
    query: Optional[str] = None       # used when action in (sql_query, sql_exec)
    connection: str = "default"
    expected: Optional[Expected] = None
    capture: Optional[Capture] = None
    timeout_ms: int = 10_000
    config: Optional[dict[str, Any]] = None  # inline settings for rest_call / bus_* actions (see README)
    delay_after_ms: Optional[int] = None  # overrides the suite's default_step_delay_ms for this step only


Mode = Literal["script", "agentic", "auto"]


class TestCase(BaseModel):
    id: str
    title: str
    area_path: Optional[str] = None
    priority: Optional[int] = None
    revision: Optional[int] = None         # which revision of this case this is (written by the manager; see framework/revisions.py)
    status: Optional[str] = None           # default | draft | superseded -- only the default is run; a missing value counts as default
    ai_recommendation: Optional[dict[str, Any]] = None     # steps the agent suggests (a note for review; never executed, not part of a revision)
    tags: list[str] = Field(default_factory=list)          # labels to select by: testbot suite --tag smoke
    # Cases that must run first (e.g. a login case). Only used when a selection is active: they are put right before this case and run in the
    # same browser (see framework/runner/selection.py). Running a whole file is unchanged: cases run in file order.
    depends_on: list[str] = Field(default_factory=list)
    preconditions: Optional[str] = None
    device: Optional[str] = None  # overrides the suite's own device for this case only
    viewport: Optional[Viewport] = None  # overrides the suite's own viewport; ignored if this case's device is set
    variables: dict[str, VariableSource] = Field(default_factory=dict)
    steps: list[TestStep] = Field(default_factory=list)
    # Natural-language test (agentic mode): state the goal; the agent decides the data and what to validate.
    objective: Optional[str] = None       # e.g. "Add a production line and confirm it appears in the list"
    data_hints: Optional[str] = None      # optional guidance about test data
    expect: list[str] = Field(default_factory=list)       # optional stated outcomes; each needs a passing check for a pass
    constraints: list[str] = Field(default_factory=list)  # things the agent must not do
    start_url: Optional[str] = None
    mode: Optional[Mode] = None           # overrides the suite's mode for this case only
    agent: Optional[dict[str, Any]] = None  # per-case agent settings (max_steps, vision, allow_destructive ...)


class TestSuite(BaseModel):
    suite_id: str
    suite_name: str
    revision: Optional[str] = None         # the suite revision on disk (r12); written by the manager
    status: Optional[str] = None           # default | draft | superseded; a missing value counts as default
    # Settings can live in the suite itself; `environment` (a block in config/environments.yaml)
    # is now just an optional fallback. Precedence: suite value > environments.yaml value.
    environment: Optional[str] = None
    mode: Optional[Mode] = None      # script (default) | agentic | auto -- TESTBOT_MODE and --mode override this
    agent: Optional[dict[str, Any]] = None   # agent settings for every case (max_steps, vision, allow_destructive ...)
    base_url: Optional[str] = None  # seeds {base_url}; overrides the environment's base_url
    connections: dict[str, str] = Field(default_factory=dict)  # SQL name -> ODBC string; merged over the environment's
    variables: dict[str, VariableSource] = Field(default_factory=dict)  # shared by every case; a case's own variable of the same name wins
    on_case_fail: Literal["continue", "stop"] = "continue"
    default_step_delay_ms: int = 0  # applied after every step's action, before its result is checked
    device: Optional[str] = None  # a Playwright device name, e.g. "iPhone 13" -- viewport+UA+touch+scale bundled
    viewport: Optional[Viewport] = None  # custom size only, used when `device` isn't set; ignored if `device` is set
    # Load-test settings (all optional; CLI flags override). workers=1 with no iterations/duration = a normal run.
    workers: Optional[int] = None      # parallel worker processes, each running the whole thing independently (default 1)
    iterations: Optional[int] = None   # times each worker repeats the run (default 1, or unlimited if duration_s is set)
    duration_s: Optional[float] = None # each worker keeps starting new iterations until this many seconds have elapsed
    ramp_up_s: Optional[float] = None  # workers are started evenly spread over this many seconds (default 0 = all at once)
    cases: list[TestCase]

    @model_validator(mode="after")
    def _merge_suite_variables(self) -> "TestSuite":
        for case in self.cases:
            case.variables = {**self.variables, **case.variables}
        self.variables = {}  # already distributed; keeps a re-validated/copied suite from re-merging
        return self

    @property
    def environment_label(self) -> str:
        return self.environment or "inline"


class StepResult(BaseModel):
    step_no: int
    description: str
    action: str  # str, not ActionType: synthetic steps (e.g. "seed_variables") aren't a real TestStep action
    data_used: Optional[str] = None  # resolved target/input/query actually sent, after {param} substitution
    status: Literal["pass", "fail", "error", "skipped", "inconclusive"]
    actual: Optional[Any] = None
    expected: Optional[Any] = None
    error: Optional[str] = None
    screenshot_path: Optional[str] = None
    duration_ms: int = 0


class CaseResult(BaseModel):
    case_id: str
    title: str
    status: Literal["pass", "fail", "error", "inconclusive"]
    steps: list[StepResult] = Field(default_factory=list)
    duration_ms: int = 0
    revision: Optional[int] = None       # the revision of this case that ran
    started_at: Optional[str] = None     # UTC, ISO 8601: when this case started ...
    finished_at: Optional[str] = None    # ... and finished (the "final test date/time" of the case)


class SuiteResult(BaseModel):
    suite_id: str
    environment: str
    started_at: str
    finished_at: Optional[str] = None
    cases: list[CaseResult] = Field(default_factory=list)
    revision: Optional[str] = None          # the suite revision that ran
    cases_in_file: Optional[int] = None     # set when only some of the file's cases were selected ...
    selected: Optional[list[str]] = None    # ... and which ones, in the order they ran

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.status == "pass")

    @property
    def failed(self) -> int:
        return sum(1 for c in self.cases if c.status != "pass")


class SessionFile(BaseModel):
    path: str
    # When true, this file's cases continue the SAME browser context (cookies, storage,
    # whatever page it's on) and the SAME variable store left by the previous file's last
    # case, instead of starting fresh. Also makes THIS file's own cases chain continuously
    # (no per-case reset), since "shared with previous" only makes sense as a continuous run.
    shares_state_with_previous: bool = False
    # Run only these cases of the file, in this order (None = every case, in file order). The same id may be listed twice.
    cases: Optional[list[str]] = None
    tags: Optional[list[str]] = None      # run the cases that carry any of these tags (with `cases`: those of them that do)
    # Overrides the session's `on_fail` for this file.
    on_fail: Optional[Literal["stop_session", "stop_file", "continue"]] = None


class SessionPlan(BaseModel):
    session_id: str
    session_name: str
    environment: Optional[str] = None  # optional fallback for base_url + SQL connections; each file's own suite values win
    files: list[SessionFile]
    # What a failed case does: stop_session (default: the file's cases finish per its own on_case_fail, then the session stops before the
    # next file) | stop_file (stop THIS file's remaining cases at the first failure, go on with the next file) | continue (never stop early)
    on_fail: Literal["stop_session", "stop_file", "continue"] = "stop_session"
    # Email when the session completes (SMTP settings come from the session's environment: framework/emailer.py). Keys: on (never|always|failure),
    # to (list: replaces the environment's recipients), attach_report (bool).
    notify: Optional[dict[str, Any]] = None
    # Load-test settings (all optional; CLI flags override). workers=1 with no iterations/duration = a normal run.
    workers: Optional[int] = None      # parallel worker processes, each running the whole thing independently (default 1)
    iterations: Optional[int] = None   # times each worker repeats the run (default 1, or unlimited if duration_s is set)
    duration_s: Optional[float] = None # each worker keeps starting new iterations until this many seconds have elapsed
    ramp_up_s: Optional[float] = None  # workers are started evenly spread over this many seconds (default 0 = all at once)


class SessionResult(BaseModel):
    session_id: str
    session_name: str
    started_at: str
    finished_at: Optional[str] = None
    suites: list[SuiteResult] = Field(default_factory=list)
    stopped_early: bool = False
    stopped_after_file: Optional[str] = None

    @property
    def total_cases(self) -> int:
        return sum(len(s.cases) for s in self.suites)

    @property
    def passed_cases(self) -> int:
        return sum(s.passed for s in self.suites)
