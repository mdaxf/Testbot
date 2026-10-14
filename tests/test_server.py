from __future__ import annotations

import http.client
import json
import threading

import pytest

from framework.manager import dataaccess, server

TOKEN = "t0ken"


@pytest.fixture
def api(ws):
    httpd, _ = server.create_server(ws, "127.0.0.1", 0, token=TOKEN)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    def call(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        c.request(method, path, body=data, headers={"Host": f"127.0.0.1:{port}", "Cookie": f"tb_token={TOKEN}", "Content-Type": "application/json"})
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, dict(r.getheaders()), raw

    yield call
    httpd.shutdown()
    httpd.server_close()


def test_result_html_is_sandboxed_and_names_are_decoded(ws, api):
    d = ws.dir("results") / "manual" / "run 1"
    d.mkdir(parents=True)
    (d / "rapport-é#&+.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
    status, headers, body = api("GET", "/files/results/manual/run%201/rapport-%C3%A9%23%26%2B.html")
    assert status == 200 and body.startswith(b"<html>")
    csp = headers["Content-Security-Policy"]
    assert csp.startswith("sandbox") and "allow-scripts" not in csp and "script-src 'none'" in csp
    assert "img-src 'self'" in csp


def test_results_path_traversal_still_refused(api):
    status, _, _ = api("GET", "/files/results/..%2F..%2Ftestbot-workspace.json")
    assert status == 404


@pytest.mark.parametrize("rev", ["../../../../x", "1.json", "abc"])
def test_revision_rev_must_be_a_number(api, rev):
    status, _, body = api("GET", f"/api/revision/case?path=orders.json&case=TC-1&rev={rev.replace('/', '%2F')}")
    assert status == 400, body


def test_schedule_and_chat_ids_are_validated(ws, api):
    (ws.root / "victim.json").write_text("{}", encoding="utf-8")
    assert api("DELETE", "/api/schedule?id=../victim")[0] == 400
    assert api("GET", "/api/schedule/history?id=../victim")[0] == 400
    assert api("DELETE", "/api/chat?path=../../..")[0] == 400
    assert (ws.root / "victim.json").exists()


def test_environments_are_masked_over_the_api(ws, api):
    cs = "Driver=x;Server=s;UID=u;PWD=hunter2;"
    dataaccess.write_environments(ws, {"qa": {"connections": {"default": cs}}})
    status, _, body = api("GET", "/api/environments")
    assert status == 200 and b"hunter2" not in body
    envs = json.loads(body)["environments"]
    assert api("PUT", "/api/environments", {"environments": envs})[0] == 200
    assert dataaccess.read_environments(ws)["qa"]["connections"]["default"] == cs


def test_email_test_uses_only_the_saved_server(ws, api):
    ws.save({"email": {"smtp_host": "smtp.company.com", "security": "starttls"}})
    status, _, body = api("POST", "/api/email/test", {"scope": "settings", "to": "me@company.com",
                                                      "email": {"smtp_host": "attacker.example", "security": "none", "username": "x",
                                                                "password_env": "ANTHROPIC_API_KEY", "on": "always"}})
    assert status == 400 and b"not the saved one" in body


def test_adhoc_ids_are_unique_per_path():
    a = server.adhoc_id("adhoc-", "a/login.json", 40)
    b = server.adhoc_id("adhoc-", "b/login.json", 40)
    assert a != b and a.startswith("adhoc-") and len(a) <= 60
    g = server.adhoc_id("adhoc-group-remaining-", "x" * 200, 60 - len("adhoc-group-remaining-") - 9)
    assert len(g) <= 60
    from framework.manager.workspace import check_id

    check_id(a), check_id(b), check_id(g)
