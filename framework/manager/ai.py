"""AI helpers for the manager: generate a test case from a description, optimise an existing one, convert text/other
formats. Nothing is sent to any service unless the user presses an AI button, the result is always validated with the
runner's own rules (one automatic repair round if it is not valid), and it is only ever PROPOSED -- never saved by itself.

Providers/keys are the same as the AI element finder: anthropic, openai, azure_openai, gemini; the key comes from an
environment variable (never from a file)."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Optional

from framework import tlsconfig
from framework.manager import testcases
from framework.manager.catalog import ACTIONS, EXPECTED, STRATEGIES
from framework.manager.workspace import Workspace

MAX_INPUT_CHARS = 60_000

PROVIDERS = {
    "anthropic": {"env": ["ANTHROPIC_API_KEY"], "model": "claude-sonnet-5-5"},
    "openai": {"env": ["OPENAI_API_KEY"], "model": "gpt-4o"},
    "azure_openai": {"env": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"], "model": ""},
    "gemini": {"env": ["GEMINI_API_KEY"], "model": "gemini-2.0-flash"},
}


class AIError(Exception):
    pass


# ------------------------------------------------------------------ provider calls (text in, text out)

def _settings(ws: Workspace) -> tuple[str, str]:
    cfg = ws.data.get("ai", {})
    provider = (cfg.get("provider") or os.environ.get("AI_VISION_PROVIDER") or "anthropic").strip().lower()
    if provider not in PROVIDERS:
        raise AIError(f"unknown AI provider '{provider}' (use one of {', '.join(PROVIDERS)})")
    model = cfg.get("model") or os.environ.get("AI_TEXT_MODEL") or PROVIDERS[provider]["model"]
    return provider, model


def status(ws: Workspace) -> dict[str, Any]:
    try:
        provider, model = _settings(ws)
    except AIError as exc:
        return {"configured": False, "error": str(exc), "providers": list(PROVIDERS)}
    missing = [v for v in PROVIDERS[provider]["env"] if not os.environ.get(v)]
    return {"configured": not missing, "provider": provider, "model": model, "missing_env": missing, "providers": list(PROVIDERS)}


def _require(name: str, provider: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise AIError(f"{name} is not set -- required for the '{provider}' AI provider. Set it in the environment and restart the manager.")
    return value


def tls_settings(ws: Workspace) -> tlsconfig.TlsSettings:
    return tlsconfig.TlsSettings.from_dict(ws.data.get("ai", {}))


def _call_provider(provider: str, model: str, system: str, user: str, tls: tlsconfig.TlsSettings) -> str:
    if provider == "anthropic":
        from anthropic import Anthropic

        client = Anthropic(api_key=_require("ANTHROPIC_API_KEY", provider), http_client=tlsconfig.http_client(tls, sdk="anthropic"))
        resp = client.messages.create(model=model, max_tokens=16000, system=system, messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    if provider in ("openai", "azure_openai"):
        if provider == "openai":
            from openai import OpenAI

            client = OpenAI(api_key=_require("OPENAI_API_KEY", provider), http_client=tlsconfig.http_client(tls, sdk="openai"))
        else:
            from openai import AzureOpenAI

            client = AzureOpenAI(api_key=_require("AZURE_OPENAI_API_KEY", provider), azure_endpoint=_require("AZURE_OPENAI_ENDPOINT", provider),
                                 api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
                                 http_client=tlsconfig.http_client(tls, sdk="openai"))
            model = _require("AZURE_OPENAI_DEPLOYMENT", provider)
        resp = client.chat.completions.create(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return resp.choices[0].message.content or ""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_require("GEMINI_API_KEY", provider), http_options=types.HttpOptions(client_args=tlsconfig.client_args(tls)))
    resp = client.models.generate_content(model=model, contents=user, config=types.GenerateContentConfig(system_instruction=system))
    return resp.text or ""


_backend: Optional[Callable[[str, str], str]] = None   # tests replace the model call with a fake


def set_backend(fn: Optional[Callable[[str, str], str]]) -> None:
    global _backend
    _backend = fn


def _complete(ws: Workspace, system: str, user: str) -> tuple[str, str]:
    if _backend is not None:
        return _backend(system, user), "test-backend"
    provider, model = _settings(ws)
    try:
        return _call_provider(provider, model, system, user, tls_settings(ws)), f"{provider}:{model}"
    except AIError:
        raise
    except tlsconfig.TlsConfigError as exc:
        raise AIError(f"certificate setting problem: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - network / quota / auth problems from the SDK
        raise AIError("the AI service call failed. " + tlsconfig.explain_error(exc)) from exc


ENDPOINTS = {"anthropic": "https://api.anthropic.com/", "openai": "https://api.openai.com/", "gemini": "https://generativelanguage.googleapis.com/"}


def test_connection(ws: Workspace, overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Can this computer open a trusted HTTPS connection to the AI service with the given (or saved) certificate/proxy settings?"""
    overrides = overrides or {}
    provider, _ = _settings(ws)
    saved = dict(ws.data.get("ai", {}))
    saved.update({k: v for k, v in overrides.items() if k in ("ssl_ca_bundle_file", "ssl_use_os_truststore", "ca_bundle", "use_system_certs", "proxy")})
    tls = tlsconfig.TlsSettings.from_dict(saved)
    base_env = {"anthropic": "ANTHROPIC_BASE_URL", "openai": "OPENAI_BASE_URL"}.get(provider)
    url = overrides.get("url") or (os.environ.get("AZURE_OPENAI_ENDPOINT") if provider == "azure_openai" else None) \
        or (os.environ.get(base_env) if base_env else None) or ENDPOINTS.get(provider)
    if not url:
        return {"ok": False, "message": "no endpoint to test (set AZURE_OPENAI_ENDPOINT, or give a URL)"}
    return tlsconfig.test_connection(tls, url)


# ------------------------------------------------------------------ prompts

def _reference() -> str:
    acts = "\n".join(f"- {k}: {v['help']} (target: {v['target']}{'; input: ' + v['input'] if v.get('input') else ''})" for k, v in ACTIONS.items())
    strats = "\n".join(f"- {k}: {v}" for k, v in STRATEGIES.items())
    exps = "\n".join(f"- {k}: {v['help']}" for k, v in EXPECTED.items())
    return f"""ACTIONS:\n{acts}\n\nTARGET STRATEGIES (a target is {{"strategy": ..., "value": ..., "name"?, "nth"?, "scope"?, "frame"?}}):\n{strats}\n
EXPECTED CHECKS (step.expected = {{"type": ..., "value"?, "target"?}}):\n{exps}"""


SYSTEM = """You write automated web UI test cases for the tool 'testbot'. You reply with ONE JSON object and nothing else:
{"suite": <testbot suite>, "notes": ["short bullet notes about assumptions or changes"]}

A testbot suite is:
{"suite_id": "ID", "suite_name": "...", "base_url": "http://...", "variables": {"name": {"source": "constant"|"faker"|"sql", ...}},
 "cases": [{"id": "TC-001", "title": "...", "variables": {}, "steps": [{"step_no": 1, "description": "...", "action": "<action>",
   "target": {...}, "input": "...", "expected": {...}, "capture": {"var": "X", "from": "element_text", "target": {...}}, "timeout_ms": 10000}]}]}

RULES
- Use ONLY the actions, target strategies and check types listed below. Number steps 1, 2, 3...
- Prefer robust targets: role+name, label, placeholder, testid, text; use css only when needed. Never invent selectors that
  are not supported by the information given -- if unsure, use a descriptive `label`/`text` target and say so in notes.
- Navigation uses {"strategy": "url", "value": "{base_url}/path"}. Reference variables as {name}; built-ins: {base_url}, {worker_id}, {iteration}.
- Replace fixed waits with `wait_until` (input hidden / visible / has_value / value:X / text:X / text_contains:X).
- Add a meaningful `expected` check to steps whose outcome matters (URL, text, visibility). Put real passwords in variables, never in steps.
- Every target-needing action must have a target; sql steps need `query`; rest/bus steps need `config`.

""" + "{REFERENCE}"


def _system() -> str:
    return SYSTEM.replace("{REFERENCE}", _reference())


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise AIError("the AI reply did not contain JSON")
    try:
        return json.loads(text[start:end + 1])
    except ValueError as exc:
        raise AIError(f"the AI reply was not valid JSON: {exc}") from exc


def _run(ws: Workspace, user: str) -> dict[str, Any]:
    reply, model = _complete(ws, _system(), user)
    repaired = False
    for attempt in range(2):
        try:
            data = _extract_json(reply)
            suite = data.get("suite", data)
            if not isinstance(suite, dict) or not isinstance(suite.get("cases"), list):
                raise AIError("the reply had no suite with a 'cases' list")
            testcases.renumber(suite)
            problems = [p for p in testcases.validate(suite)]
            errors = [p for p in problems if p["level"] == "error"]
            if errors and attempt == 0:
                raise AIError("validation errors: " + "; ".join(f"case {e.get('case')} step {e.get('step')}: {e['message']}" for e in errors[:12]))
            return {"suite": suite, "notes": data.get("notes", []) if isinstance(data, dict) else [], "problems": problems,
                    "model": model, "repaired": repaired}
        except AIError as exc:
            if attempt == 1:
                raise
            repaired = True
            reply, _ = _complete(ws, _system(), user + f"\n\nYour previous reply had a problem: {exc}\nReply again with the corrected JSON object only.")
    raise AIError("unreachable")


def _clip(text: str) -> str:
    return text if len(text) <= MAX_INPUT_CHARS else text[:MAX_INPUT_CHARS] + "\n...(truncated)"


# ------------------------------------------------------------------ the three tasks

def generate(ws: Workspace, description: str, base_url: str = "", page_html: str = "", existing_variables: Optional[dict] = None) -> dict[str, Any]:
    if not description.strip():
        raise AIError("describe what the test should do")
    parts = [f"TASK: create a test suite for this requirement:\n{_clip(description)}"]
    if base_url:
        parts.append(f"The application's base_url is {base_url}.")
    if existing_variables:
        parts.append("Variables that already exist and may be used: " + ", ".join(existing_variables))
    if page_html.strip():
        parts.append("HTML of the relevant page(s) -- take element labels/ids/roles from it:\n" + _clip(page_html))
    return _run(ws, "\n\n".join(parts))


def optimize_case(ws: Workspace, suite: dict[str, Any], goals: str = "", case_id: Optional[str] = None) -> dict[str, Any]:
    """Optimise ONE test case of a suite (the model sees the suite's settings and only that case, so a long file is never cut short or
    summarised) and return the whole suite with just that case replaced. Variables the model adds at suite level are merged in."""
    cases = suite.get("cases") or []
    if not cases:
        raise AIError("the suite has no test case")
    idx = next((i for i, c in enumerate(cases) if c.get("id") == case_id), 0 if case_id is None and len(cases) == 1 else None)
    if idx is None:
        raise AIError(f"test case '{case_id}' is not in this file" if case_id else "choose which test case to optimise")
    case = cases[idx]
    goal = goals.strip() or "make it more robust and readable"
    mini = {**{k: v for k, v in suite.items() if k != "cases"}, "cases": [case]}
    user = ("TASK: optimise this existing testbot test case WITHOUT changing what it tests. Goals: " + goal + ".\n"
            f"The suite below holds ONE test case, '{case.get('id')}' ({len(case.get('steps') or [])} steps); it is one of {len(cases)} in the file. "
            "Return the same suite with exactly that one case in `cases`, keeping its id, and every step of it (do not drop, merge or summarise steps unless one is a duplicate).\n"
            "Typical improvements: replace `wait` with `wait_until`, use more robust targets, add missing `expected` checks, give steps clear "
            "descriptions, remove duplicate steps, move repeated literals into variables. List every change in notes.\n\nSUITE:\n"
            + _clip(json.dumps(mini, indent=1)))
    res = _run(ws, user)
    got = res["suite"].get("cases") or []
    new_case = next((c for c in got if c.get("id") == case.get("id")), got[0] if len(got) == 1 else None)
    if new_case is None:
        raise AIError("the AI did not return the test case")
    new_case["id"] = case["id"]
    merged = json.loads(json.dumps(suite))
    merged["cases"][idx] = new_case
    notes = [f"{case['id']}: {n}" for n in res.get("notes", [])]
    for name, definition in (res["suite"].get("variables") or {}).items():       # a literal moved into a variable
        have = (merged.get("variables") or {}).get(name)
        if have is None:
            merged.setdefault("variables", {})[name] = definition
            notes.append(f"{case['id']}: added suite variable '{name}'")
        elif have != definition:
            notes.append(f"{case['id']}: the AI proposed a different value for the existing variable '{name}' -- kept the original")
    testcases.renumber(merged)
    return {"suite": merged, "notes": notes, "problems": testcases.validate(merged), "model": res["model"], "repaired": res["repaired"], "case": case["id"]}


def optimize(ws: Workspace, suite: dict[str, Any], goals: str = "", case_id: Optional[str] = None) -> dict[str, Any]:
    """One case (case_id) or every case of the file, one after the other. A case that cannot be optimised is kept as it was and reported."""
    cases = suite.get("cases") or []
    if case_id:
        return optimize_case(ws, suite, goals, case_id)
    current, notes, model, repaired = suite, [], "", False
    for case in cases:
        try:
            r = optimize_case(ws, current, goals, case.get("id"))
        except AIError as exc:
            notes.append(f"{case.get('id')}: NOT optimised, kept as it was ({exc})")
            continue
        current, model, repaired = r["suite"], r["model"], repaired or r["repaired"]
        notes += r["notes"]
    return {"suite": current, "notes": notes, "problems": testcases.validate(current), "model": model, "repaired": repaired}


def convert(ws: Workspace, content: str, hint: str = "", base_url: str = "") -> dict[str, Any]:
    if not content.strip():
        raise AIError("paste the content to convert")
    user = ("TASK: convert the following test description/script into a testbot suite. Keep the original order and intent."
            + (f" Source format / notes: {hint}." if hint else "") + (f" base_url is {base_url}." if base_url else "")
            + "\n\nSOURCE:\n" + _clip(content))
    return _run(ws, user)
