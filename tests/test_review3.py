import pytest

from framework.manager.dataaccess import check_select_only
from framework.manager.server import host_is_allowed
from framework.runner.orchestrator import evidence_filename
from framework.variables.context import VariableContext


def test_numeric_looking_identifiers_stay_bound_parameters():
    sql, params = VariableContext({"order": "0001234", "n": "10"}).resolve_sql("SELECT TOP {n} * FROM t WHERE OrderNo = {order}")
    assert sql == "SELECT TOP 10 * FROM t WHERE OrderNo = ?"
    assert params == ["0001234"]


@pytest.mark.parametrize("query", ["SELECT t.Save, t.[Set] FROM t", "SELECT Qty AS Return FROM t", "SELECT [Transaction] FROM t"])
def test_columns_named_like_keywords_are_allowed(query):
    assert check_select_only(query) == query


@pytest.mark.parametrize("query", ["SELECT 1 SET @x = 1", "SELECT 1 SHUTDOWN WITH NOWAIT", "SELECT * FROM t DROP TABLE t"])
def test_statement_keywords_are_still_refused(query):
    with pytest.raises(ValueError):
        check_select_only(query)


def test_ipv4_mapped_loopback_peer_is_accepted():
    allowed = {"localhost:8080"}
    assert host_is_allowed("localhost:8080", "::ffff:127.0.0.1", allowed)
    assert not host_is_allowed("localhost:8080", "10.0.0.5", allowed)


def test_case_ids_that_sanitise_alike_get_different_evidence_files():
    assert evidence_filename("TC-5~2", 1, "fail") != evidence_filename("TC-5_2", 1, "fail")
    assert evidence_filename("TC-5", 1, "fail") == "TC-5_step1_fail.png"
