from __future__ import annotations

import json
from pathlib import Path

import yaml

from framework.models import SessionPlan


def load_session_plan(path: str | Path) -> SessionPlan:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) if path.suffix.lower() in (".yaml", ".yml") else json.loads(text)
    return SessionPlan.model_validate(data)
