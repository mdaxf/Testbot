"""Email a summary when a session completes.

The SMTP settings live in the ENVIRONMENT block of config/environments.yaml (next to base_url and connections):

    uat:
      base_url: http://uat-server/apriso
      email:
        smtp_host: smtp.company.com
        smtp_port: 587
        security: starttls                        # none | starttls | ssl
        username: testbot@company.com
        password_env: TESTBOT_SMTP_PASSWORD_UAT   # the NAME of an environment variable (or a .env entry) -- never the password itself
        from: testbot@company.com
        to: [uat-leads@company.com]
        on: failure                               # never (default) | always | failure
        attach_report: false                      # attach the HTML report (default: no)

DEFAULTS for every environment can come from the manager's Settings page (passed to the runner as TESTBOT_EMAIL_HOST / _PORT / _SECURITY /
_USERNAME / _PASSWORD_ENV / _FROM / _TO / _ON / _ATTACH_REPORT, which also work in .env or the shell). An environment's own `email:` block overrides
them field by field (so the server can be set once and only the recipients differ for UAT).
A session can override `on`, `to` and `attach_report` for itself with a `notify:` block in its plan. The session's environment decides which
block is used. Nothing is sent unless `on` is `always` or `failure`. A failed send never changes the test result: it is logged (area `email`).
TLS uses the same root-certificate settings as the AI calls (TESTBOT_SSL_CA_BUNDLE_FILE, the operating-system store).
"""
from __future__ import annotations

import os
import re
import smtplib
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Optional

from framework import logs

log = logs.get("email")
SECURITY = ("none", "starttls", "ssl")
PASSWORD_ENV_PREFIX = "TESTBOT_SMTP_"          # password_env may only name these variables (never e.g. an AI key)
_PASSWORD_ENV = re.compile(r"^TESTBOT_SMTP_[A-Za-z0-9_]+$")
RULES = ("never", "always", "failure")
_ADDR = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


class EmailError(Exception):
    pass


@dataclass
class EmailSettings:
    host: str
    port: int = 587
    security: str = "starttls"
    username: str = ""
    password_env: str = ""
    sender: str = ""
    to: list[str] = field(default_factory=list)
    on: str = "never"
    attach_report: bool = False
    timeout: float = 30.0


def _addresses(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[;,\s]+", value)
    return [str(v).strip() for v in (value or []) if str(v).strip()]


_ENV_KEYS = {"smtp_host": "HOST", "smtp_port": "PORT", "security": "SECURITY", "username": "USERNAME", "password_env": "PASSWORD_ENV", "from": "FROM", "to": "TO",
             "on": "ON", "attach_report": "ATTACH_REPORT"}


def _present(v: Any) -> bool:
    return v not in (None, "", [], {})


def env_defaults(env: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """The default email settings given through TESTBOT_EMAIL_* variables (only the ones that are set)."""
    env = os.environ if env is None else env
    out: dict[str, Any] = {}
    for key, suffix in _ENV_KEYS.items():
        v = (env.get("TESTBOT_EMAIL_" + suffix) or "").strip()
        if not v:
            continue
        out[key] = (_addresses(v) if key == "to" else (v.lower() not in ("0", "false", "no", "off") if key == "attach_report" else (int(v) if key == "smtp_port" and v.isdigit() else v)))
    return out


def env_for_child(defaults: Optional[dict[str, Any]]) -> dict[str, str]:
    """The manager's Settings (email defaults) as TESTBOT_EMAIL_* variables for the runner."""
    out: dict[str, str] = {}
    for key, suffix in _ENV_KEYS.items():
        v = (defaults or {}).get(key)
        if not _present(v):
            continue
        out["TESTBOT_EMAIL_" + suffix] = ",".join(_addresses(v)) if key == "to" else (("1" if v else "0") if key == "attach_report" else str(v))
    return out


def merge(env_block: Optional[dict], defaults: Optional[dict]) -> Optional[dict]:
    """The environment's email block over the defaults, field by field (an empty value in the block does not override). None when neither says anything."""
    merged = {k: v for k, v in (defaults or {}).items() if _present(v)}
    merged.update({k: v for k, v in (env_block or {}).items() if _present(v) or isinstance(v, bool)})
    return merged or None


def check_block(block: Any, require_host: bool = True) -> list[str]:
    """Problems in an `email` block (empty list = fine). `require_host=False` for a block that only overrides some defaults
    (an environment block); the combined settings are checked with require_host=True when an email is about to be sent."""
    if block in (None, {}):
        return []
    if not isinstance(block, dict):
        return ["email must be a block of settings"]
    out: list[str] = []
    if "password" in block:
        out.append("never write the password in the file: set it in an environment variable and name the variable with `password_env`")
    if require_host and not str(block.get("smtp_host") or "").strip():
        out.append("email: smtp_host is needed (set it in Settings, or in the environment's email block)")
    try:
        if not 1 <= int(block.get("smtp_port") or 587) <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        out.append("email: smtp_port must be a number between 1 and 65535")
    if (block.get("security") or "starttls") not in SECURITY:
        out.append(f"email: security must be one of {', '.join(SECURITY)}")
    if (block.get("on") or "never") not in RULES:
        out.append(f"email: on must be one of {', '.join(RULES)}")
    if block.get("username") and not block.get("password_env"):
        out.append("email: a username needs password_env (the name of the environment variable that holds the password)")
    if block.get("password_env") and not _PASSWORD_ENV.match(str(block["password_env"])):
        out.append(f"email: password_env must be the NAME of an environment variable starting with {PASSWORD_ENV_PREFIX} (letters, digits, underscore), "
                   f"for example {PASSWORD_ENV_PREFIX}PASSWORD")
    if block.get("username") and (block.get("security") or "starttls") == "none":
        out.append("email: a username (SMTP login) needs security starttls or ssl -- the password is never sent over an unencrypted connection")
    for key in ("from",):
        if block.get(key) and not _ADDR.match(str(block[key]).strip()):
            out.append(f"email: '{block[key]}' is not an email address")
    for a in _addresses(block.get("to")):
        if not _ADDR.match(a):
            out.append(f"email: '{a}' is not an email address")
    return out


def settings_for(block: Optional[dict], notify: Optional[dict] = None, env_name: Optional[str] = None) -> Optional[EmailSettings]:
    """The effective settings for a session, or None when no email should be sent. The session's `notify` overrides on / to / attach_report."""
    notify = notify or {}
    wants = (notify.get("on") or (block or {}).get("on") or "never")
    if wants not in RULES:
        raise EmailError(f"email rule '{wants}' is not one of {', '.join(RULES)}")
    if wants == "never":
        return None
    if not block:
        raise EmailError(f"email is wanted, but there are no email settings: set the SMTP server in Settings, or in the `email` block of environment '{env_name or '(none)'}' in config/environments.yaml")
    problems = check_block(block)
    if problems:
        raise EmailError("; ".join(problems))
    to = _addresses(notify.get("to")) or _addresses(block.get("to"))
    if not to:
        raise EmailError("no recipient: set `to` in the environment's email settings (or `notify.to` in the session)")
    return EmailSettings(host=str(block["smtp_host"]).strip(), port=int(block.get("smtp_port") or 587), security=block.get("security") or "starttls",
                         username=str(block.get("username") or ""), password_env=str(block.get("password_env") or ""),
                         sender=str(block.get("from") or block.get("username") or "testbot@" + socket.gethostname()), to=to, on=wants,
                         attach_report=bool(notify["attach_report"]) if "attach_report" in notify else bool(block.get("attach_report")))


def send(s: EmailSettings, msg: EmailMessage, env: Optional[dict[str, str]] = None) -> None:
    from framework import tlsconfig

    env = os.environ if env is None else env
    password = None
    if s.username:
        if not _PASSWORD_ENV.match(s.password_env or ""):
            raise EmailError(f"password_env must name a variable starting with {PASSWORD_ENV_PREFIX}")
        if s.security not in ("starttls", "ssl"):
            raise EmailError("an SMTP login needs security starttls or ssl (the password is never sent unencrypted)")
        password = env.get(s.password_env)
        if not password:
            raise EmailError(f"the environment variable {s.password_env} is not set (it must hold the SMTP password)")
    context = tlsconfig.make_ssl_context()
    try:
        me = socket.gethostname()        # given explicitly: smtplib's own lookup of the fully-qualified name can take seconds on some networks
        server = (smtplib.SMTP_SSL(s.host, s.port, timeout=s.timeout, context=context, local_hostname=me) if s.security == "ssl"
                  else smtplib.SMTP(s.host, s.port, timeout=s.timeout, local_hostname=me))
        with server:
            server.ehlo()
            if s.security == "starttls":
                server.starttls(context=context)
                server.ehlo()
            if s.username:
                server.login(s.username, password)
            server.send_message(msg, from_addr=s.sender, to_addrs=s.to)
    except (smtplib.SMTPException, OSError) as exc:
        text = logs.redact(str(exc))
        if password:
            text = text.replace(password, "***")
        raise EmailError(f"{type(exc).__name__}: {text}") from None


# ------------------------------------------------------------------ the message

def _local(iso: Optional[str]) -> str:
    try:
        d = datetime.fromisoformat(str(iso))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(iso or "")


def _problem(case: Any) -> str:
    for st in case.steps:
        if st.status not in ("pass", "skipped"):
            detail = st.error or (f"expected {st.expected!r}, actual {st.actual!r}" if st.expected is not None or st.actual is not None else "")
            return logs.clip(logs.redact(f"step {st.step_no}: {st.description}" + (f" -- {detail}" if detail else "")), 240)
    return ""


def session_summary(plan: Any, result: Any, env_name: Optional[str]) -> dict[str, Any]:
    cases = [(s.suite_id, c) for s in result.suites for c in s.cases]
    counts = {k: sum(1 for _, c in cases if c.status == k) for k in ("pass", "fail", "error", "inconclusive")}
    if not cases:
        final = "NO CASES"
    elif counts["pass"] == len(cases):
        final = "PASS"
    else:
        final = "FAIL" if counts["error"] == 0 and counts["inconclusive"] == 0 else "ERROR"
    return {"final": final, "total": len(cases), "counts": counts, "environment": env_name or "inline",
            "problems": [{"suite": sid, "case": c.case_id, "title": c.title, "status": c.status, "problem": _problem(c)} for sid, c in cases if c.status != "pass"]}


def build_session_message(plan: Any, result: Any, s: EmailSettings, env_name: Optional[str], report: Optional[Path] = None) -> EmailMessage:
    t = session_summary(plan, result, env_name)
    subject = f"[testbot] {plan.session_id}: {t['final']} {t['counts']['pass']}/{t['total']} ({t['environment']})"
    lines = [f"Session: {plan.session_id} - {plan.session_name}", f"Final result: {t['final']}", f"Environment: {t['environment']}",
             f"Started: {_local(result.started_at)}", f"Finished: {_local(result.finished_at)}",
             f"Test cases: {t['total']} - {t['counts']['pass']} passed, {t['counts']['fail']} failed, {t['counts']['error']} error, {t['counts']['inconclusive']} inconclusive",
             f"Run on: {socket.gethostname()} (folder {os.getcwd()})"]
    if report:
        lines.append(f"Report: {report}")
    if t["problems"]:
        lines += ["", "Test cases that did not pass:"] + [f"  - {p['suite']} / {p['case']} ({p['status']}): {p['title']}" + (f"\n      {p['problem']}" if p["problem"] else "") for p in t["problems"]]
    color = {"PASS": "#1a7f37", "FAIL": "#cf222e", "ERROR": "#bc4c00"}.get(t["final"], "#57606a")
    import html as h

    rows = "".join(f"<tr><td>{h.escape(p['suite'])} / {h.escape(p['case'])}</td><td>{h.escape(p['status'])}</td><td>{h.escape(p['title'])}</td><td>{h.escape(p['problem'])}</td></tr>" for p in t["problems"])
    html = (f"<html><body style=\"font-family:Segoe UI,Arial,sans-serif\"><h2 style=\"color:{color}\">{h.escape(plan.session_id)}: {t['final']}</h2>"
            f"<p>{h.escape(plan.session_name)}<br>Environment <b>{h.escape(t['environment'])}</b><br>Started {h.escape(_local(result.started_at))} &mdash; finished <b>{h.escape(_local(result.finished_at))}</b><br>"
            f"<b>{t['total']}</b> test case(s): {t['counts']['pass']} passed, {t['counts']['fail']} failed, {t['counts']['error']} error, {t['counts']['inconclusive']} inconclusive</p>"
            + (f"<h3>Did not pass</h3><table border=\"1\" cellpadding=\"4\" cellspacing=\"0\"><tr><th>Test case</th><th>Result</th><th>Title</th><th>Problem</th></tr>{rows}</table>" if rows else "")
            + f"<p style=\"color:#57606a\">Run on {h.escape(socket.gethostname())}</p></body></html>")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, s.sender, ", ".join(s.to)
    msg.set_content("\n".join(lines))
    msg.add_alternative(html, subtype="html")
    if s.attach_report and report and Path(report).is_file():
        msg.add_attachment(Path(report).read_bytes(), maintype="text", subtype="html", filename=Path(report).name)
    return msg


def build_test_message(s: EmailSettings, env_name: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = f"[testbot] test email ({env_name})", s.sender, ", ".join(s.to)
    msg.set_content(f"This is a test message from testbot.\nEnvironment: {env_name}\nSent {_local(datetime.now(timezone.utc).isoformat())} from {socket.gethostname()}.\n"
                    "If you can read this, the email settings of that environment work.")
    return msg


def notify_session(plan: Any, result: Any, env_config: Optional[dict], env_name: Optional[str], report: Optional[Path] = None) -> Optional[str]:
    """Called when a session completes. Returns a one-line note for the console (None = nothing to say). Never raises."""
    try:
        s = settings_for(merge((env_config or {}).get("email"), env_defaults()), getattr(plan, "notify", None), env_name)
        if s is None:
            log.debug("email: not configured to send for session %s", plan.session_id)
            return None
        final = session_summary(plan, result, env_name)["final"]
        if s.on == "failure" and final == "PASS":
            log.info("email: not sent for session %s (rule 'failure' and the session passed)", plan.session_id)
            return None
        send(s, build_session_message(plan, result, s, env_name, report))
        log.info("email: sent to %s (session %s: %s)", ", ".join(s.to), plan.session_id, final)
        return f"Email sent to {', '.join(s.to)}"
    except EmailError as exc:
        log.error("email: not sent: %s", exc)
        return f"WARNING: the email was not sent: {exc}"
    except Exception as exc:  # noqa: BLE001 - email must never break a test run
        log.error("email: not sent: %s", logs.redact(exc))
        return f"WARNING: the email was not sent: {exc}"
