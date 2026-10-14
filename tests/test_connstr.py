from __future__ import annotations

import pytest
import yaml

from framework import connstr
from framework.manager import dataaccess

CS = "Driver={ODBC Driver 18 for SQL Server};Server=s;UID=u;PWD=p@ss;Encrypt=yes;"


def test_mask_and_unmask():
    m = connstr.mask(CS)
    assert "p@ss" not in m and "PWD=***" in m
    assert connstr.mask("Server=s;Trusted_Connection=yes;") == "Server=s;Trusted_Connection=yes;"
    assert connstr.mask("PWD={a;b}};x};Server=s") == "PWD=***;Server=s"
    assert connstr.mask("PWD={env:TESTBOT_DB_X};") == "PWD={env:TESTBOT_DB_X};"
    assert connstr.unmask(m, CS) == CS
    edited = m.replace("Server=s", "Server=t")
    assert connstr.unmask(edited, CS) == CS.replace("Server=s", "Server=t")
    with pytest.raises(ValueError):
        connstr.unmask("Server=s;PWD=***;", None)


def test_expand_only_testbot_db_variables():
    env = {"TESTBOT_DB_PW": "a;b}", "ANTHROPIC_API_KEY": "sk"}
    assert connstr.expand("PWD={env:TESTBOT_DB_PW};", env) == "PWD={a;b}}};"
    with pytest.raises(ValueError):
        connstr.expand("PWD={env:ANTHROPIC_API_KEY};", env)
    with pytest.raises(ValueError):
        connstr.expand("PWD={env:TESTBOT_DB_MISSING};", env)


def test_environments_are_masked_and_saving_keeps_the_password(ws):
    dataaccess.write_environments(ws, {"qa": {"base_url": "x", "connections": {"default": CS}}})
    shown = dataaccess.masked_environments(dataaccess.read_environments(ws))
    assert "p@ss" not in str(shown)
    dataaccess.write_environments(ws, shown)                       # the browser sends back what it got
    assert dataaccess.read_environments(ws)["qa"]["connections"]["default"] == CS
    shown["prod"] = shown.pop("qa")                                # renamed in the UI
    dataaccess.write_environments(ws, shown)
    assert dataaccess.read_environments(ws)["prod"]["connections"]["default"] == CS
    with pytest.raises(ValueError):
        dataaccess.write_environments(ws, {"new": {"connections": {"default": "Server=other;PWD=***;"}}})


def test_find_stored_connection(ws):
    envs = {"qa": {"connections": {"default": CS}}}
    assert dataaccess.find_stored_connection(envs, connstr.mask(CS)) == CS
    assert dataaccess.find_stored_connection(envs, "Server=plain;") == "Server=plain;"
    with pytest.raises(ValueError):
        dataaccess.find_stored_connection(envs, "Server=evil;PWD=***;")


def test_sample_environments_file_has_no_plaintext_password():
    from pathlib import Path

    data = yaml.safe_load((Path(__file__).resolve().parent.parent / "config" / "environments.yaml").read_text(encoding="utf-8"))
    for env in data.values():
        for cs in (env.get("connections") or {}).values():
            assert connstr.mask(cs) == cs, cs
