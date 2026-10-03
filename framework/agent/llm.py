"""Model access for the agent: one neutral conversation format, adapters for Anthropic and OpenAI/Azure OpenAI tool calling.
Connections use the company root-certificate / proxy settings (framework/tlsconfig.py)."""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from framework import tlsconfig
from framework.agent.config import AgentConfig

Message = dict[str, Any]
# {"role": "user", "parts": [{"text": str} | {"image": bytes}]}
# {"role": "assistant", "text": str, "calls": [{"id", "name", "args"}]}
# {"role": "tool", "results": [{"id", "name", "text", "image": bytes | None}]}


class AgentError(Exception):
    pass


@dataclass
class Turn:
    text: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0


class LLM(Protocol):
    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> Turn: ...


def _obj(props: dict[str, Any], required: Optional[list[str]] = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


S = {"type": "string"}
TOOLS: list[dict[str, Any]] = [
    {"name": "navigate", "description": "Open an address. Only the sites allowed for this test are permitted.", "parameters": _obj({"url": S}, ["url"])},
    {"name": "click", "description": "Click a control from the latest list of controls.", "parameters": _obj({"element": {"type": "string", "description": "id like e12"}}, ["element"])},
    {"name": "type", "description": "Type text into a field (replaces its content). Use {variable} references for anything from the variable list; passwords MUST be a {variable}.",
     "parameters": _obj({"element": S, "text": S, "data_source": {"type": "string", "enum": ["given", "generated", "db", "derived", "variable"], "description": "where this value came from, for the audit trail"}}, ["element", "text"])},
    {"name": "select", "description": "Choose an option in a dropdown (or a type-ahead field) by its visible text.", "parameters": _obj({"element": S, "option": S, "data_source": S}, ["element", "option"])},
    {"name": "check", "description": "Tick or untick a checkbox.", "parameters": _obj({"element": S, "checked": {"type": "boolean"}}, ["element"])},
    {"name": "press", "description": "Press a key (Enter, Tab, Escape...), in a control if given.", "parameters": _obj({"key": S, "element": S}, ["key"])},
    {"name": "wait_for_text", "description": "Wait until some text is shown (or gone) on the page.", "parameters": _obj({"text": S, "visible": {"type": "boolean"}, "timeout_s": {"type": "number"}}, ["text"])},
    {"name": "scroll", "description": "Scroll the page down or up.", "parameters": _obj({"direction": {"type": "string", "enum": ["down", "up"]}})},
    {"name": "look", "description": "Get a screenshot of the current screen (when allowed).", "parameters": _obj({})},
    {"name": "set_variable", "description": "Remember a value you generated (for example a unique name) so you can reuse it as {name}.", "parameters": _obj({"name": S, "value": S, "data_source": S}, ["name", "value"])},
    {"name": "note", "description": "Record a short observation or a data decision for the report.", "parameters": _obj({"text": S}, ["text"])},
    {"name": "query_db", "description": "Run a read-only SELECT on the test database (only if a connection is configured).", "parameters": _obj({"query": S, "connection": S}, ["query"])},
    {"name": "assert", "description": ("Check something. testbot runs the check itself; a test can only pass if its expected outcomes are backed by passing checks. "
        "kind: text_present|text_absent (text) · url_contains|url_equals (value) · element_text (element + equals|contains) · element_value (element, equals) · "
        "element_visible|element_hidden (element) · element_checked (element, checked) · css (element, property, equals = e.g. a colour) · "
        "table_rows (element = a table id like t1, rows = [{text, text_contains, background, color, class, not_class}], optional item/style_item/rows_selector) · "
        "sql (query, equals, optional column/connection). covers = the numbers of the expected outcomes this check proves."),
     "parameters": _obj({"kind": S, "text": S, "value": S, "element": S, "equals": {}, "contains": S, "checked": {"type": "boolean"}, "property": S,
                         "rows": {"type": "array", "items": {"type": "object"}}, "rows_selector": S, "item": S, "style_item": S, "query": S, "column": S, "connection": S,
                         "covers": {"type": "array", "items": {"type": "integer"}}}, ["kind"])},
    {"name": "finish", "description": "End the test with your verdict. Call this once, when the objective is done or cannot be done.",
     "parameters": _obj({"verdict": {"type": "string", "enum": ["pass", "fail", "inconclusive"]}, "summary": S,
                         "outcomes": {"type": "array", "items": {"type": "object", "properties": {"statement": S, "status": {"type": "string", "enum": ["met", "not_met", "unverified"]},
                                                                                              "evidence": {"type": "array", "items": S}}}}}, ["verdict", "summary"])},
]


# ------------------------------------------------------------------ Anthropic
class AnthropicLLM:
    def __init__(self, cfg: AgentConfig, base_url: Optional[str] = None):
        from anthropic import Anthropic

        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise AgentError("ANTHROPIC_API_KEY is not set -- required for the agent (provider 'anthropic')")
        self.model = cfg.model or "claude-sonnet-5-5"
        self.client = Anthropic(api_key=key, http_client=tlsconfig.http_client(sdk="anthropic"), **({"base_url": base_url} if base_url else {}))

    @staticmethod
    def _img(b: bytes) -> dict[str, Any]:
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(b).decode("ascii")}}

    def _convert(self, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": [({"type": "text", "text": p["text"]} if "text" in p else self._img(p["image"])) for p in m["parts"]]})
            elif m["role"] == "assistant":
                blocks: list[dict[str, Any]] = [{"type": "text", "text": m["text"]}] if m.get("text") else []
                blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]} for c in m.get("calls", [])]
                out.append({"role": "assistant", "content": blocks})
            else:
                blocks = []
                for r in m["results"]:
                    content: list[dict[str, Any]] = [{"type": "text", "text": r["text"]}]
                    if r.get("image"):
                        content.append(self._img(r["image"]))
                    blocks.append({"type": "tool_result", "tool_use_id": r["id"], "content": content})
                out.append({"role": "user", "content": blocks})
        return out

    def complete(self, system, messages, tools) -> Turn:
        resp = self.client.messages.create(
            model=self.model, max_tokens=4096, system=system, messages=self._convert(messages),
            tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools])
        turn = Turn(tokens_in=getattr(resp.usage, "input_tokens", 0) or 0, tokens_out=getattr(resp.usage, "output_tokens", 0) or 0)
        for block in resp.content:
            if block.type == "text":
                turn.text += block.text
            elif block.type == "tool_use":
                turn.calls.append({"id": block.id, "name": block.name, "args": dict(block.input or {})})
        return turn


# ------------------------------------------------------------------ OpenAI / Azure OpenAI
class OpenAILLM:
    def __init__(self, cfg: AgentConfig, azure: bool = False, base_url: Optional[str] = None):
        if azure:
            from openai import AzureOpenAI

            for var in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"):
                if not os.environ.get(var):
                    raise AgentError(f"{var} is not set -- required for the agent (provider 'azure_openai')")
            self.client = AzureOpenAI(api_key=os.environ["AZURE_OPENAI_API_KEY"], azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                                      api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"), http_client=tlsconfig.http_client(sdk="openai"))
            self.model = os.environ["AZURE_OPENAI_DEPLOYMENT"]
        else:
            from openai import OpenAI

            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise AgentError("OPENAI_API_KEY is not set -- required for the agent (provider 'openai')")
            self.client = OpenAI(api_key=key, http_client=tlsconfig.http_client(sdk="openai"), **({"base_url": base_url} if base_url else {}))
            self.model = cfg.model or "gpt-4o"

    @staticmethod
    def _img(b: bytes) -> dict[str, Any]:
        return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b).decode("ascii")}}

    def _convert(self, system: str, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": [({"type": "text", "text": p["text"]} if "text" in p else self._img(p["image"])) for p in m["parts"]]})
            elif m["role"] == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("text") or None}
                if m.get("calls"):
                    msg["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["args"])}} for c in m["calls"]]
                out.append(msg)
            else:
                images: list[dict[str, Any]] = []
                for r in m["results"]:
                    out.append({"role": "tool", "tool_call_id": r["id"], "content": r["text"]})
                    if r.get("image"):
                        images.append(self._img(r["image"]))
                if images:                      # tool messages are text-only: show the screenshot as a user message
                    out.append({"role": "user", "content": [{"type": "text", "text": "Screenshot of the current screen:"}] + images})
        return out

    def complete(self, system, messages, tools) -> Turn:
        resp = self.client.chat.completions.create(
            model=self.model, messages=self._convert(system, messages),
            tools=[{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in tools])
        msg = resp.choices[0].message
        turn = Turn(text=msg.content or "", tokens_in=getattr(resp.usage, "prompt_tokens", 0) or 0, tokens_out=getattr(resp.usage, "completion_tokens", 0) or 0)
        for c in msg.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")
            except ValueError:
                args = {}
            turn.calls.append({"id": c.id, "name": c.function.name, "args": args})
        return turn


_factory: Optional[Callable[[AgentConfig], LLM]] = None


def set_llm_factory(fn: Optional[Callable[[AgentConfig], LLM]]) -> None:
    """Tests (and alternative engines) replace how the model is reached."""
    global _factory
    _factory = fn


def make_llm(cfg: AgentConfig) -> LLM:
    if _factory is not None:
        return _factory(cfg)
    if cfg.provider == "anthropic":
        return AnthropicLLM(cfg, os.environ.get("ANTHROPIC_BASE_URL"))
    if cfg.provider == "openai":
        return OpenAILLM(cfg, base_url=os.environ.get("OPENAI_BASE_URL"))
    if cfg.provider == "azure_openai":
        return OpenAILLM(cfg, azure=True)
    raise AgentError(f"agent mode supports the providers anthropic, openai and azure_openai (got '{cfg.provider}')")
