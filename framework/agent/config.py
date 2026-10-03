"""Agent settings and the script/agentic/auto mode switch.

Mode precedence: --mode (command line) > TESTBOT_MODE (environment) > the case's `mode` > the suite's `mode` > "script".
  script   only scripted steps run (today's behaviour); a natural-language-only case is reported as inconclusive, never guessed
  agentic  cases with an `objective` are executed by the agent; cases that only have steps still run as scripts
  auto     a case with steps runs as a script; a case without steps (natural language only) is executed by the agent"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

MODES = ("script", "agentic", "auto")


def _flag(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def env_mode() -> Optional[str]:
    m = (os.environ.get("TESTBOT_MODE") or "").strip().lower()
    if m and m not in MODES:
        raise ValueError(f"TESTBOT_MODE must be one of {', '.join(MODES)} (got '{m}')")
    return m or None


def resolve_mode(cli: Optional[str], suite_mode: Optional[str], case_mode: Optional[str]) -> str:
    return cli or env_mode() or case_mode or suite_mode or "script"


@dataclass
class Profile:
    """What an application looks like to an agent: where the screen lives, how to tell it is busy, how grids behave."""
    name: str = "generic"
    frame: str = ""                # CSS of the iframe that holds the application screen (Apriso: .apr-fullscreen-tab)
    popup: str = ""                # CSS of pop-up containers; their elements are listed first (Apriso: .apr-popup)
    spinner: str = ""              # CSS of a "busy" indicator to wait out before looking at the page
    allowed_hosts: list[str] = field(default_factory=list)
    notes: str = ""                # free text given to the model: conventions of this application

    @classmethod
    def load(cls, path: Optional[str]) -> "Profile":
        if not path:
            return cls()
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(name=str(data.get("name", Path(path).stem)), frame=data.get("frame", ""), popup=data.get("popup", ""),
                   spinner=data.get("spinner", ""), allowed_hosts=list(data.get("allowed_hosts", [])), notes=str(data.get("notes", "")).strip())


@dataclass
class AgentConfig:
    provider: str = "anthropic"
    model: str = ""
    max_steps: int = 40            # tool calls that act on the page
    max_tokens: int = 400_000      # total model tokens (input + output) for one test
    timeout_s: int = 600           # wall-clock limit for one test
    vision: str = "auto"           # off | auto (screenshot at start, after a failure, or on request) | always
    allow_destructive: bool = False
    profile: str = ""              # path of an app profile (YAML)
    allowed_hosts: list[str] = field(default_factory=list)
    heal: str = "suggest"

    @classmethod
    def from_env(cls, overrides: Optional[dict[str, Any]] = None) -> "AgentConfig":
        e = os.environ
        c = cls(
            provider=(e.get("TESTBOT_AGENT_PROVIDER") or e.get("AI_VISION_PROVIDER") or "anthropic").strip().lower(),
            model=(e.get("TESTBOT_AGENT_MODEL") or e.get("AI_TEXT_MODEL") or "").strip(),
            max_steps=int(e.get("TESTBOT_AGENT_MAX_STEPS") or 40),
            max_tokens=int(e.get("TESTBOT_AGENT_MAX_TOKENS") or 400_000),
            timeout_s=int(e.get("TESTBOT_AGENT_TIMEOUT_S") or 600),
            vision=(e.get("TESTBOT_AGENT_VISION") or "auto").strip().lower(),
            allow_destructive=_flag(e.get("TESTBOT_AGENT_ALLOW_DESTRUCTIVE")),
            profile=(e.get("TESTBOT_AGENT_PROFILE") or "").strip(),
            allowed_hosts=[h.strip() for h in (e.get("TESTBOT_AGENT_ALLOWED_HOSTS") or "").split(",") if h.strip()],
            heal=(e.get("TESTBOT_AGENT_HEAL") or "suggest").strip().lower(),
        )
        for key, value in (overrides or {}).items():   # suite / case `agent` settings
            if hasattr(c, key) and value is not None:
                setattr(c, key, type(getattr(c, key))(value) if not isinstance(getattr(c, key), (list, bool)) else value)
        if c.vision not in ("off", "auto", "always"):
            raise ValueError("TESTBOT_AGENT_VISION must be off, auto or always")
        return c
