"""Guard rails for the agent: where it may go, what it may press, what it must never type literally, and its budgets.

The page content is untrusted: text on a page that tells the agent to do something does not change these rules."""
from __future__ import annotations

import re
import time
from typing import Any, Optional
from urllib.parse import urlsplit

from framework.agent.config import AgentConfig, Profile
from framework.agent.observer import DESTRUCTIVE

SECRET_NAME = re.compile(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|credential)")
_VERB = re.compile(DESTRUCTIVE)


def is_secret_name(name: str) -> bool:
    return bool(SECRET_NAME.search(name))


class Policy:
    def __init__(self, cfg: AgentConfig, profile: Profile, urls: list[str], objective: str, constraints: list[str]):
        self.cfg = cfg
        self.allowed_hosts: set[str] = set(cfg.allowed_hosts) | set(profile.allowed_hosts)
        self.allow_file = False
        for u in urls:
            parts = urlsplit(u or "")
            if parts.netloc:
                self.allowed_hosts.add(parts.netloc)
            if parts.scheme == "file":
                self.allow_file = True
        self.started = time.monotonic()
        self.actions = 0
        self.tokens = 0
        text = objective.lower()
        blocked = " ".join(c.lower() for c in constraints)
        # a destructive verb is allowed only when the objective asks for it and no constraint forbids it
        self.allowed_verbs = {v for v in set(_VERB.findall(objective.lower())) if not re.search(rf"(not|n't|never|no)\s+(\w+\s+)?{v}", blocked + " " + text)}
        self.last_actions: list[str] = []

    # ---- navigation and actions
    def check_navigation(self, url: str) -> Optional[str]:
        parts = urlsplit(url)
        if parts.scheme == "file":
            return None if self.allow_file else "navigation to local files is not allowed for this test"
        if parts.scheme not in ("http", "https"):
            return f"only http(s) addresses are allowed (got '{parts.scheme or url}')"
        if parts.netloc not in self.allowed_hosts:
            return f"'{parts.netloc}' is not an allowed site for this test (allowed: {', '.join(sorted(self.allowed_hosts)) or 'none'})"
        return None

    def check_press(self, info: dict[str, Any]) -> Optional[str]:
        if self.cfg.allow_destructive:
            return None
        label = f"{info.get('name', '')} {info.get('href', '')}"
        for verb in set(_VERB.findall(label.lower())):
            if verb not in self.allowed_verbs:
                return (f"'{info.get('name') or info.get('href')}' looks destructive ('{verb}') and the test objective does not ask for it. "
                        "State it in the objective (and not in the constraints) or set TESTBOT_AGENT_ALLOW_DESTRUCTIVE=1.")
        return None

    def check_type(self, info: dict[str, Any], text: str) -> Optional[str]:
        if info.get("type") == "password" and not re.fullmatch(r"\s*\{[A-Za-z_]\w*\}\s*", text):
            return "passwords must be given as a {variable} reference, never as literal text"
        return None

    # ---- budgets
    def budget_problem(self) -> Optional[str]:
        if self.actions >= self.cfg.max_steps:
            return f"step budget reached ({self.cfg.max_steps} actions)"
        if self.tokens >= self.cfg.max_tokens:
            return f"token budget reached ({self.tokens} of {self.cfg.max_tokens})"
        if time.monotonic() - self.started > self.cfg.timeout_s:
            return f"time limit reached ({self.cfg.timeout_s} s)"
        return None

    def note_action(self, signature: str) -> Optional[str]:
        """Detects an agent going round in circles: the same action on the same page state three times in a row."""
        self.last_actions.append(signature)
        if len(self.last_actions) >= 3 and len(set(self.last_actions[-3:])) == 1:
            return "the same action was repeated 3 times with no change -- the agent is stuck"
        return None
