from __future__ import annotations

from email.message import EmailMessage

import pytest

from framework import emailer


def _block(**kw):
    return {"smtp_host": "smtp.example.com", "smtp_port": 587, "security": "starttls", "username": "u", "password_env": "TESTBOT_SMTP_PASSWORD",
            "from": "a@example.com", "to": ["b@example.com"], "on": "always", **kw}


@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "PATH", "TESTBOT_SMTP_", "testbot_smtp_x", "TESTBOT_SMTP_X-Y"])
def test_password_env_must_be_a_testbot_smtp_variable(name):
    assert any("password_env" in p for p in emailer.check_block(_block(password_env=name)))


@pytest.mark.parametrize("name", ["TESTBOT_SMTP_PASSWORD", "TESTBOT_SMTP_PASSWORD_UAT"])
def test_password_env_allowed(name):
    assert emailer.check_block(_block(password_env=name)) == []


def test_login_without_tls_is_refused_by_check():
    assert any("starttls or ssl" in p for p in emailer.check_block(_block(security="none")))
    assert emailer.check_block(_block(security="none", username="", password_env="")) == []   # no login: plain SMTP is fine


def _settings(**kw):
    base = dict(host="smtp.example.com", port=25, security="none", username="u", password_env="ANTHROPIC_API_KEY", sender="a@example.com",
                to=["b@example.com"], on="always", attach_report=False)
    base.update(kw)
    return emailer.EmailSettings(**base)


def test_send_never_reads_other_variables_or_logs_in_without_tls(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not connect")

    monkeypatch.setattr(emailer.smtplib, "SMTP", boom)
    monkeypatch.setattr(emailer.smtplib, "SMTP_SSL", boom)
    env = {"ANTHROPIC_API_KEY": "sk-secret", "TESTBOT_SMTP_PASSWORD": "pw"}
    with pytest.raises(emailer.EmailError, match="TESTBOT_SMTP_"):
        emailer.send(_settings(security="starttls"), EmailMessage(), env)
    with pytest.raises(emailer.EmailError, match="starttls or ssl"):
        emailer.send(_settings(password_env="TESTBOT_SMTP_PASSWORD"), EmailMessage(), env)
