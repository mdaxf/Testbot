from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_SQL_PLACEHOLDER_RE = re.compile(r"\{(raw:)?([a-zA-Z_][a-zA-Z0-9_]*)\}")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
_IDENT_RE = re.compile(r"^[A-Za-z0-9_ .$#@-]*$")


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

    def _value(self, name: str) -> Any:
        if name not in self._values:
            raise MissingVariableError(f"'{{{name}}}' referenced but not set. Known variables: {sorted(self._values)}")
        return self._values[name]

    def resolve_sql(self, sql: str) -> tuple[str, list[Any]]:
        """{name} substitution for SQL, with the values sent as bound parameters (`?`) instead of pasted into the text,
        so a value such as O'Brien (or hostile page text) can never change the statement.

        Backward compatible with the documented test-case format:
          * a string literal that contains placeholders ('{OrderNo}', 'ORD-{n}', N'%{name}%') becomes ONE parameter
            holding the literal's resolved text;
          * a bare placeholder (WHERE Id = {id}) becomes a parameter, except that a plain number is written inline
            (so TOP {n} and similar keep working);
          * inside [brackets] / "double quotes" (identifiers) the value is written inline, and only if it is a
            plain name;
          * {raw:name} writes the value inline unchanged -- an explicit opt-out for table/column names built at run
            time; never use it with values that come from the application under test.
        Returns (sql_with_question_marks, params)."""
        out: list[str] = []
        params: list[Any] = []

        def fill_text(text: str) -> str:
            return _SQL_PLACEHOLDER_RE.sub(lambda m: str(self._value(m.group(2))), text)

        i, n = 0, len(sql)
        while i < n:
            ch = sql[i]
            if ch == "'" or (ch in "Nn" and sql.startswith("'", i + 1) and (i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_"))):
                start = i + 1 if ch == "'" else i + 2
                j = start
                while j < n:
                    if sql[j] == "'":
                        if j + 1 < n and sql[j + 1] == "'":
                            j += 2
                            continue
                        break
                    j += 1
                literal = sql[i:j + 1]
                inner = sql[start:j]
                if j < n and _SQL_PLACEHOLDER_RE.search(inner):
                    out.append("?")
                    params.append(fill_text(inner.replace("''", "'")))
                else:
                    out.append(literal)
                i = j + 1
            elif sql.startswith("--", i) or sql.startswith("/*", i):          # comments are copied as they are
                end = sql.find("\n", i) if sql[i] == "-" else sql.find("*/", i + 2)
                end = n if end < 0 else end + (1 if sql[i] == "-" else 2)
                out.append(sql[i:end])
                i = end
            elif ch in "[\"":
                close = "]" if ch == "[" else '"'
                j = sql.find(close, i + 1)
                j = n - 1 if j < 0 else j
                inner = sql[i + 1:j]

                def ident(m: re.Match[str]) -> str:
                    value = str(self._value(m.group(2)))
                    if m.group(1) or _IDENT_RE.match(value):
                        return value
                    raise ValueError(f"'{{{m.group(2)}}}' is used as a SQL name but its value is not a plain name: {value[:40]!r}")

                out.append(ch + _SQL_PLACEHOLDER_RE.sub(ident, inner) + sql[j:j + 1])
                i = j + 1
            elif ch == "{" and (m := _SQL_PLACEHOLDER_RE.match(sql, i)):
                value = self._value(m.group(2))
                if m.group(1):
                    out.append(str(value))
                elif isinstance(value, (int, float)) and not isinstance(value, bool) or (isinstance(value, str) and _NUMBER_RE.match(value)):
                    out.append(f"({value})" if str(value).startswith("-") else str(value))
                else:
                    out.append("?")
                    params.append(value)
                i = m.end()
            else:
                out.append(ch)
                i += 1
        return "".join(out), params

    def resolve(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.resolve_string(value)
        if isinstance(value, dict):
            return {k: self.resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        return value
