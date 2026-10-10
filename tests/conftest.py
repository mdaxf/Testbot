from __future__ import annotations

import pytest

from framework.manager.workspace import Workspace


@pytest.fixture
def ws(tmp_path) -> Workspace:
    w = Workspace(tmp_path / "testbot-workspace.json")
    w.ensure_dirs()
    return w
