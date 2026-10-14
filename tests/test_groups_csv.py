from __future__ import annotations

import csv
import io
import json

from framework.manager.groups import _excel_cell, export_csv


def test_excel_cell_prefixes_formulas():
    assert _excel_cell("=1+1") == "'=1+1"
    assert _excel_cell("\t+cmd") == "'\t+cmd"
    assert _excel_cell("-2") == "'-2"
    assert _excel_cell("@SUM") == "'@SUM"
    assert _excel_cell("plain") == "plain"
    assert _excel_cell(3) == 3


def test_group_export_prefixes_formula_cells(ws):
    (ws.dir("test_cases") / "s.json").write_text(json.dumps({
        "suite_id": "s", "suite_name": "s",
        "cases": [{"id": "TC-1", "title": "=1+1", "steps": []}],
    }), encoding="utf-8")
    (ws.dir("groups") / "g1.json").write_text(json.dumps({
        "id": "g1", "name": "@cycle",
        "members": [{"type": "suite", "path": "s.json"}],
    }), encoding="utf-8")
    rows = list(csv.reader(io.StringIO(export_csv(ws, "g1"))))
    assert rows[1][0] == "'@cycle"
    assert rows[1][3] == "'=1+1"
