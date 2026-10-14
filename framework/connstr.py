"""ODBC connection strings: keep passwords out of files and out of the browser.

  * ``PWD={env:TESTBOT_DB_PASSWORD_QA}`` in a connection string is replaced, when the connection is opened, by the value of that
    environment variable (or .env entry), so environments.yaml never holds the password. Only variables whose name starts with
    TESTBOT_DB_ can be used (a connection string must not be able to read e.g. an AI key).
  * ``mask()`` hides the password before a connection string is sent to the browser; ``unmask()`` puts the stored one back when the
    masked text comes back unchanged (saving the Environments page, the Test button)."""
from __future__ import annotations

import os
import re
from typing import Mapping, Optional

ENV_PREFIX = "TESTBOT_DB_"
MASK = "***"
_ENV_REF = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
_PWD = re.compile(r"(?i)(?<![A-Za-z0-9_])(pwd|password)=(\{(?:[^}]|\}\})*\}|[^;]*)")


def expand(cs: str, env: Optional[Mapping[str, str]] = None) -> str:
    """Replace each {env:NAME} with the value of that variable (ODBC-quoted when needed)."""
    env = os.environ if env is None else env

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if not name.startswith(ENV_PREFIX):
            raise ValueError(f"connection string: {{env:{name}}} is not allowed -- only variables starting with {ENV_PREFIX}")
        if name not in env:
            raise ValueError(f"connection string: the environment variable {name} is not set")
        value = env[name]
        if any(c in value for c in ";{}") or value != value.strip():
            value = "{" + value.replace("}", "}}") + "}"
        return value

    return _ENV_REF.sub(sub, cs or "")


def _is_masked_value(value: str) -> bool:
    return value == MASK


def mask(cs: str) -> str:
    """The connection string with its password replaced by ***. An {env:NAME} reference is not a secret and is kept."""
    def sub(m: re.Match[str]) -> str:
        return m.group(0) if _ENV_REF.fullmatch(m.group(2)) or not m.group(2) else f"{m.group(1)}={MASK}"

    return _PWD.sub(sub, cs or "")


def is_masked(cs: str) -> bool:
    return any(_is_masked_value(m.group(2)) for m in _PWD.finditer(cs or ""))


def unmask(new: str, old: Optional[str]) -> str:
    """`new` as typed/sent back by the browser, with a masked password (***) replaced by the password of the stored string `old`."""
    if not is_masked(new):
        return new
    if old and mask(old) == new:
        return old
    stored = next((m.group(2) for m in _PWD.finditer(old or "") if not _is_masked_value(m.group(2))), None)
    if stored is None:
        raise ValueError("the connection string has a hidden password (***) but no stored password to keep: type the password again")
    return _PWD.sub(lambda m: f"{m.group(1)}={stored}" if _is_masked_value(m.group(2)) else m.group(0), new)
