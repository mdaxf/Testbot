from __future__ import annotations

from typing import Any

from faker import Faker

_faker = Faker()

_NON_KWARG_FIELDS = {"source", "type", "connection", "query", "column", "value", "extra"}


def generate_faker_value(spec: dict[str, Any]) -> Any:
    """spec is a VariableSource.model_dump(exclude_none=True) with source == "faker"."""
    method = spec.get("type")
    if not method:
        raise ValueError("Faker variable source requires 'type' (a Faker method name, e.g. 'email', 'bothify')")

    kwargs = {k: v for k, v in spec.items() if k not in _NON_KWARG_FIELDS}
    if method == "bothify" and "pattern" in kwargs:
        kwargs["text"] = kwargs.pop("pattern")

    generator = getattr(_faker, method, None)
    if generator is None:
        raise ValueError(f"Unknown Faker generator '{method}'")
    return generator(**kwargs)
