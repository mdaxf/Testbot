from __future__ import annotations

import json

from pathlib import Path
from typing import Any

import pandas as pd

from framework.models import Capture, Expected, Target, TestCase, TestStep, TestSuite, VariableSource, Viewport


def _nz(row: pd.Series, col: str, default: Any = None) -> Any:
    value = row.get(col, default)
    if value is None:
        return default
    if isinstance(value, float) and pd.isna(value):
        return default
    return value


def _target_or_none(row: pd.Series, prefix: str) -> Target | None:
    strategy = _nz(row, f"{prefix}Strategy")
    if not strategy:
        return None
    def _int(col: str) -> int | None:
        value = _nz(row, f"{prefix}{col}")
        return int(value) if value is not None else None

    return Target(
        strategy=strategy,
        value=str(_nz(row, f"{prefix}Value", "")),
        name=_nz(row, f"{prefix}Name"),
        nth=_int("Nth"),
        row=_int("Row"),
        col=_int("Col"),
        scope=_nz(row, f"{prefix}Scope"),
        frame=_nz(row, f"{prefix}Frame"),
        scope_if_present=_nz(row, f"{prefix}ScopeIfPresent"),
    )


def _read_case_sheet(xls: pd.ExcelFile, sheet_name: str) -> tuple[dict[str, Any], pd.DataFrame]:
    """A case tab is a small Key | Value metadata block, then a blank row, then the
    Steps table starting at the row whose column A cell reads exactly 'StepNo'."""
    raw = xls.parse(sheet_name, header=None)

    header_row_idx = None
    for i in range(len(raw)):
        if str(raw.iat[i, 0]).strip() == "StepNo":
            header_row_idx = i
            break
    if header_row_idx is None:
        raise ValueError(f"Sheet '{sheet_name}': no Steps table found (no cell in column A reads 'StepNo')")

    meta: dict[str, Any] = {}
    for i in range(header_row_idx):
        key = raw.iat[i, 0]
        if pd.isna(key) or not str(key).strip():
            continue
        value = raw.iat[i, 1] if raw.shape[1] > 1 else None
        meta[str(key).strip()] = None if (value is None or (isinstance(value, float) and pd.isna(value))) else value

    steps_df = xls.parse(sheet_name, header=header_row_idx)
    return meta, steps_df


def _build_case(
    case_id: str, meta: dict[str, Any], steps_df: pd.DataFrame, common_variables: dict[str, VariableSource]
) -> TestCase:
    case_viewport_width = meta.get("ViewportWidth")
    case_viewport_height = meta.get("ViewportHeight")
    case_viewport = (
        Viewport(width=int(case_viewport_width), height=int(case_viewport_height))
        if case_viewport_width and case_viewport_height
        else None
    )

    case = TestCase(
        id=case_id,
        title=str(meta.get("Title", case_id)),
        area_path=meta.get("AreaPath"),
        priority=meta.get("Priority"),
        preconditions=meta.get("Preconditions"),
        device=meta.get("Device"),  # overrides the Suite sheet's Device for this case only
        viewport=case_viewport,
        variables=dict(common_variables),  # shared test data, available before any per-case step runs
        steps=[],
        objective=meta.get("Objective"),
        data_hints=meta.get("DataHints"),
        expect=[ln.strip() for ln in str(meta.get("Expect") or "").splitlines() if ln.strip()],
        constraints=[ln.strip() for ln in str(meta.get("Constraints") or "").splitlines() if ln.strip()],
        start_url=meta.get("StartUrl"),
        mode=meta.get("Mode") or None,
    )

    for _, row in steps_df.sort_values("StepNo").iterrows():
        if pd.isna(row.get("StepNo")):
            continue  # trailing blank row below the table

        expected = None
        if _nz(row, "ExpectedType"):
            expected = Expected(
                type=row["ExpectedType"],
                value=_nz(row, "ExpectedValue"),
                target=_target_or_none(row, "ExpectedTarget"),
                column=_nz(row, "ExpectedColumn"),
                property=_nz(row, "ExpectedProperty"),
                rows=_nz(row, "ExpectedRows"),
                item=_nz(row, "ExpectedItem"),
                style_item=_nz(row, "ExpectedStyleItem"),
            )
            if expected.type == "list_matches" and isinstance(expected.value, str):
                expected.value = json.loads(expected.value)  # Excel cell holds the JSON list of row specs

        capture = None
        if _nz(row, "CaptureVar"):
            capture = Capture(
                var=row["CaptureVar"],
                **{"from": row["CaptureFrom"]},
                target=_target_or_none(row, "CaptureTarget"),
                column=_nz(row, "CaptureColumn"),
            )

        case.steps.append(
            TestStep(
                step_no=int(row["StepNo"]),
                description=str(_nz(row, "Description", "")),
                action=row["Action"],
                target=_target_or_none(row, "Target"),
                input=_nz(row, "Input"),
                query=_nz(row, "Query"),
                connection=_nz(row, "Connection", "default"),
                expected=expected,
                capture=capture,
                delay_after_ms=_nz(row, "DelayAfterMs"),
                config=json.loads(_nz(row, "Config")) if _nz(row, "Config") else None,
                **({"timeout_ms": _nz(row, "TimeoutMs")} if _nz(row, "TimeoutMs") else {}),
            )
        )

    return case


def _load_common_variables(xls: pd.ExcelFile) -> dict[str, VariableSource]:
    if "CommonData" not in xls.sheet_names:
        return {}
    variables: dict[str, VariableSource] = {}
    for _, row in xls.parse("CommonData").iterrows():
        if pd.isna(row.get("VarName")):
            continue
        variables[row["VarName"]] = VariableSource(
            source=row["Source"],
            connection=_nz(row, "Connection", "default"),
            query=_nz(row, "Query"),
            column=_nz(row, "Column"),
            type=_nz(row, "Type"),
            pattern=_nz(row, "Pattern"),
            value=_nz(row, "Value"),
        )
    return variables


def load_suite_from_excel(path: str | Path) -> TestSuite:
    """Workbook layout:

    - 'Suite' sheet (one row): SuiteID, SuiteName, Environment (optional fallback), BaseUrl (optional),
      Workers/Iterations/DurationSec/RampUpSec (optional load-test settings), Mode (script|agentic|auto),
      OnCaseFail, DefaultStepDelayMs,
      Device (a Playwright device name, e.g. "iPhone 13"; optional), ViewportWidth/
      ViewportHeight (custom size, used only when Device is blank; optional).
    - 'Index' sheet: Sequence, CaseID, Run (Y/N, optional, default Y). Controls which case
      tabs run and in what order -- CaseID must match a sheet name exactly.
    - 'Connections' sheet (optional): Name, ConnectionString -- SQL connections owned by this workbook
      (override same-named ones from the environment).
    - 'CommonData' sheet (optional): VarName/Source/Query/Connection/Column/Type/Pattern/Value.
      Merged into every case's own variable store, so shared setup data (credentials,
      lookups) is defined once instead of repeated per case.
    - One sheet per test case, named exactly as its CaseID: a Key | Value metadata block (natural-language cases add
      Objective / DataHints / Expect / Constraints (one per line) / StartUrl / Mode -- the Steps table may then be empty)
      (Title/AreaPath/Priority/Preconditions/Device/ViewportWidth/ViewportHeight -- the
      last three override the Suite sheet's for this case only), a blank row, then the
      Steps table starting
      at the row whose column A cell reads 'StepNo' (same step columns as before:
      Description, Action, TargetStrategy/Value/Name/Nth/Row/Col/Scope/Frame/ScopeIfPresent (the same
      extra columns exist for the ExpectedTarget* and CaptureTarget* prefixes), Input, Query, Connection,
      ExpectedType/Value/Target*/Column, CaptureVar/From/Target*/Column, DelayAfterMs).
    """
    xls = pd.ExcelFile(path)
    suite_row = xls.parse("Suite").iloc[0]
    index_df = xls.parse("Index")
    common_variables = _load_common_variables(xls)

    cases: list[TestCase] = []
    for _, row in index_df.sort_values("Sequence").iterrows():
        if str(_nz(row, "Run", "Y")).strip().upper() == "N":
            continue
        case_id = str(row["CaseID"])
        if case_id not in xls.sheet_names:
            raise ValueError(f"Index lists CaseID '{case_id}' but no sheet with that name exists in the workbook")
        meta, steps_df = _read_case_sheet(xls, case_id)
        cases.append(_build_case(case_id, meta, steps_df, common_variables))

    viewport_width = _nz(suite_row, "ViewportWidth")
    viewport_height = _nz(suite_row, "ViewportHeight")
    viewport = Viewport(width=int(viewport_width), height=int(viewport_height)) if viewport_width and viewport_height else None

    connections: dict[str, str] = {}
    if "Connections" in xls.sheet_names:
        for _, row in xls.parse("Connections").iterrows():
            if not pd.isna(row.get("Name")):
                connections[str(row["Name"])] = str(row["ConnectionString"])

    environment = _nz(suite_row, "Environment")
    return TestSuite(
        suite_id=str(suite_row["SuiteID"]),
        suite_name=str(suite_row["SuiteName"]),
        environment=str(environment) if environment else None,
        base_url=_nz(suite_row, "BaseUrl"),
        mode=_nz(suite_row, "Mode"),
        workers=int(_nz(suite_row, "Workers")) if _nz(suite_row, "Workers") else None,
        iterations=int(_nz(suite_row, "Iterations")) if _nz(suite_row, "Iterations") else None,
        duration_s=float(_nz(suite_row, "DurationSec")) if _nz(suite_row, "DurationSec") else None,
        ramp_up_s=float(_nz(suite_row, "RampUpSec")) if _nz(suite_row, "RampUpSec") else None,
        connections=connections,
        on_case_fail=str(_nz(suite_row, "OnCaseFail", "continue")),
        default_step_delay_ms=int(_nz(suite_row, "DefaultStepDelayMs", 0)),
        device=_nz(suite_row, "Device"),
        viewport=viewport,
        cases=cases,
    )
