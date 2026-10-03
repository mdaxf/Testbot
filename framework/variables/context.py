from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class MissingVariableError(Exception):
    pass


class VariableContext:
    """Per-test-case variable store, with {name} substitution into strings.

    Populated up front from case-level `variables` (SQL/Faker/constant) and
    progressively from step-level `capture` blocks, so a later step can
    reference a value that either a SQL lookup or the app itself produced.
    """

    def __init__(self, initial: dict[str, Any] | None = None):
        self._values: dict[str, Any] = dict(initial or {})

    def set(self, name: str, value: Any) -> None:
        self._values[name] = value

    def get(self, name: str, default: Any = ...) -> Any:
        if name in self._values:
            return self._values[name]
        if default is not ...:
            return default
        raise MissingVariableError(name)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    def resolve_string(self, text: str) -> str:
        def _sub(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in self._values:
                raise MissingVariableError(
                    f"'{{{name}}}' referenced but not set. Known variables: {sorted(self._values)}"
                )
            return str(self._values[name])

        return _PLACEHOLDER_RE.sub(_sub, text)

    def resolve(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.resolve_string(value)
        if isinstance(value, dict):
            return {k: self.resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        return value
