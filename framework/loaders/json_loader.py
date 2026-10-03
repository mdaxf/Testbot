from __future__ import annotations

import json
from pathlib import Path

from framework.models import TestSuite


def load_suite_from_json(path: str | Path) -> TestSuite:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return TestSuite.model_validate(data)
