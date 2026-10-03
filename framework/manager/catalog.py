"""The lists the editor offers in its dropdowns. The value lists come straight from the runner's own model
definitions, so a new action or check appears in the UI automatically; the help texts add the guidance."""
from __future__ import annotations

from typing import Any, get_args

from framework.models import ActionType, CaptureFrom, ExpectedType, TargetStrategy, VariableSourceType

# needs_target: "required" | "optional" | "none";  input: hint for the Input field (None = not used)
ACTIONS: dict[str, dict[str, Any]] = {
    "navigate": {"target": "optional", "input": "address (if no target)", "help": "Open a page. Use target strategy 'url' with {base_url}/path."},
    "click": {"target": "required", "input": None, "help": "Click the element. config {\"js_click\": true} clicks via script when something overlays it."},
    "type": {"target": "required", "input": "text to type", "help": "Clear the field and type the text."},
    "select": {"target": "required", "input": "option value or visible text", "help": "Choose an entry in a dropdown (or type into a type-ahead input)."},
    "check": {"target": "required", "input": "true / false", "help": "Tick or untick a checkbox (target = its label) only if not already in that state."},
    "hover": {"target": "required", "input": None, "help": "Move the mouse over the element."},
    "press": {"target": "required", "input": "key, e.g. Enter, Tab, Escape", "help": "Press a key in the element."},
    "scan": {"target": "none", "input": "text", "help": "Type into whatever has focus, like a barcode scanner."},
    "scroll": {"target": "optional", "input": "down:500 / up:500 / top / bottom (optional)", "help": "Scroll an element into view, or scroll by an amount."},
    "upload": {"target": "required", "input": "file path", "help": "Attach a file to a file input."},
    "wait": {"target": "none", "input": "milliseconds", "help": "Wait a fixed time. Prefer wait_until."},
    "wait_until": {"target": "required", "input": "hidden | visible | has_value | value:X | text:X | text_contains:X",
                   "help": "Wait until the element reaches the condition, up to Timeout."},
    "screenshot": {"target": "none", "input": "file name", "help": "Save an extra screenshot."},
    "agent": {"target": "none", "input": "the instruction in plain English", "config": True,
              "help": "Let the AI agent do this part (needs the agent set up). config: {\"expect\": [\"outcome to prove\"], \"max_steps\": 15}. The step passes only if its outcomes are proven by checks."},
    "sql_query": {"target": "none", "input": None, "query": True, "help": "Run a SELECT; the first row is kept for checks/capture. config {\"poll\": true} retries until it matches."},
    "sql_exec": {"target": "none", "input": None, "query": True, "help": "Run INSERT/UPDATE/DELETE/procedure. Changes real data."},
    "set_var": {"target": "none", "input": "value", "capture": True, "help": "Set a variable (name in the Capture block)."},
    "assert": {"target": "none", "input": None, "help": "Only evaluates the step's Expected check."},
    "rest_call": {"target": "none", "input": None, "config": True, "help": "Call a web API (config: url, method, headers, body, auth, expect_status, expect_json, capture)."},
    "bus_subscribe": {"target": "none", "input": None, "config": True, "help": "Start listening on MQTT / AMQP / Kafka."},
    "bus_wait": {"target": "none", "input": None, "config": True, "help": "Wait for a matching bus message."},
    "bus_publish": {"target": "none", "input": None, "config": True, "help": "Publish a bus message."},
}

CONFIG_TEMPLATES: dict[str, Any] = {
    "rest_call": {"url": "{base_url}/api/items", "method": "GET", "expect_status": 200, "capture": {}},
    "bus_subscribe": {"broker": "mqtt", "host": "localhost", "topic": "plant/#", "name": "default"},
    "bus_wait": {"name": "default", "match": {}, "capture": {}},
    "bus_publish": {"broker": "mqtt", "host": "localhost", "topic": "plant/line1", "payload": {}},
    "click": {"js_click": True},
    "sql_query": {"poll": True},
    "agent": {"expect": [], "max_steps": 15},
}

STRATEGIES: dict[str, str] = {
    "role": "Kind of element (button, link, textbox...) plus its visible Name",
    "label": "Text of the field's label",
    "placeholder": "Grey hint text inside a field",
    "text": "Text shown on the page",
    "testid": "The element's data-testid",
    "css": "CSS selector",
    "xpath": "XPath",
    "url": "Address (navigate only)",
    "ai": "Plain-English description (uses the AI element finder)",
    "row_containing": "Text somewhere in a table row",
    "table_cell": "CSS of the table + Row and Col (1-based)",
}

EXPECTED: dict[str, dict[str, Any]] = {
    "none": {"target": False, "value": False, "help": "No check."},
    "url_equals": {"target": False, "value": True, "help": "Page address equals the value."},
    "url_contains": {"target": False, "value": True, "help": "Page address contains the value."},
    "text_equals": {"target": True, "value": True, "help": "Element text equals the value."},
    "text_contains": {"target": True, "value": True, "help": "Element text contains the value."},
    "visible": {"target": True, "value": False, "help": "Element is shown."},
    "hidden": {"target": True, "value": False, "help": "Element is not shown."},
    "value_equals": {"target": True, "value": True, "help": "Field value equals the value."},
    "sql_result_equals": {"target": False, "value": True, "column": True, "help": "Result of the last sql_query equals the value."},
    "count_equals": {"target": True, "value": True, "help": "Number of matching elements equals the value."},
    "css_equals": {"target": True, "value": True, "property": True, "help": "Displayed style (default background-color) equals the value; colours in any format."},
    "has_class": {"target": True, "value": True, "help": "Element has this class (or all classes of a list)."},
    "list_matches": {"target": True, "value": True, "list": True, "rows": True,
                     "help": "Top N rows of a table/list match in order. Value = list of {text, background, class, ...}."},
}

CAPTURE_FROM: dict[str, dict[str, Any]] = {
    "element_text": {"target": True, "help": "Text of the element."},
    "element_value": {"target": True, "help": "Value of the field."},
    "url": {"target": False, "help": "Current page address."},
    "sql_column": {"target": False, "column": True, "help": "A column of the last sql_query row."},
}

VARIABLE_SOURCES: dict[str, dict[str, Any]] = {
    "constant": {"fields": ["value"], "help": "A fixed value."},
    "faker": {"fields": ["type", "pattern"], "help": "A generated value (Faker method, e.g. email; bothify with a pattern)."},
    "sql": {"fields": ["query", "connection", "column"], "help": "First value returned by a query."},
}

FAKER_TYPES = ["first_name", "last_name", "name", "email", "user_name", "password", "phone_number", "address", "city", "company",
               "date", "date_time", "sentence", "word", "text", "uuid4", "random_int", "bothify", "numerify", "lexify"]

KEYS = ["Enter", "Tab", "Escape", "Backspace", "Delete", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End", "PageDown", "PageUp", "Control+a", "F5"]

DEVICES = ["Desktop Chrome", "iPhone 13", "iPhone 14 Pro", "Pixel 7", "iPad Mini", "iPad Pro 11", "Galaxy S9+"]

BUILTIN_VARIABLES = ["base_url", "worker_id", "iteration"]


def catalog() -> dict[str, Any]:
    actions = {}
    for name in get_args(ActionType):
        actions[name] = ACTIONS.get(name, {"target": "optional", "input": "input", "help": ""})
    return {
        "actions": actions,
        "strategies": {k: STRATEGIES.get(k, "") for k in get_args(TargetStrategy)},
        "expected": {k: EXPECTED.get(k, {"target": True, "value": True, "help": ""}) for k in get_args(ExpectedType)},
        "capture_from": {k: CAPTURE_FROM.get(k, {"help": ""}) for k in get_args(CaptureFrom)},
        "variable_sources": {k: VARIABLE_SOURCES.get(k, {"fields": [], "help": ""}) for k in get_args(VariableSourceType)},
        "config_templates": CONFIG_TEMPLATES,
        "faker_types": FAKER_TYPES,
        "keys": KEYS,
        "devices": DEVICES,
        "on_case_fail": ["continue", "stop"],
        "modes": ["script", "agentic", "auto"],
        "builtin_variables": BUILTIN_VARIABLES,
        "wait_conditions": ["hidden", "visible", "has_value", "value:", "text:", "text_contains:"],
    }
