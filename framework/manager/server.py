"""The manager's local web server: a small JSON API plus the static browser UI.

Security (the API can write files and start test runs, so it is not left open):
  * listens on 127.0.0.1 only by default;
  * every request needs the per-launch access token (given once in the start URL, kept in a SameSite=Strict cookie);
  * the Host header must be the server's own address (blocks DNS-rebinding);
  * every file path is resolved inside the workspace folders and refused if it escapes."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import secrets
import threading
import traceback
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, unquote, urlparse

from framework import revisions
from framework.manager import ai, chat, dataaccess, groups, reviews, importer, results, schedules, scheduler, testcases
from framework.manager.catalog import catalog
from framework.manager.workspace import KINDS, Workspace, WorkspaceError

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = 60 * 1024 * 1024
# Result files (HTML reports especially) hold text from the application under test. They are served from this origin, so they
# must never run script with access to the API: a sandboxed document without allow-scripts. allow-same-origin keeps relative
# links/images to the run's own screenshots working; inline styles are allowed, nothing else is loaded from elsewhere.
RESULT_FILE_CSP = ("sandbox allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-downloads; default-src 'none'; img-src 'self' data:; "
                   "style-src 'self' 'unsafe-inline'; media-src 'self'; script-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; "
                   "frame-ancestors 'self'")
from framework import logs
from framework.brand_logo import LOGO_BG, LOGO_DATA_URI
from framework.version import COPYRIGHT, PRODUCT, __version__ as VERSION  # noqa: E402


log = logs.get("manager")


class ApiError(Exception):
    def __init__(self, status: int, message: str, extra: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.status = status
        self.extra = extra or {}


def _int(value: Any, name: str) -> int:
    """A whole number from the request (revision numbers), or a 400."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ApiError(400, f"'{name}' must be a whole number") from None


def adhoc_id(prefix: str, path: str, keep: int) -> str:
    """A run id for an ad-hoc run: readable (prefix + start of the name) and unique per full path (short hash), max 60 characters."""
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]   # noqa: S324 - an id, not security
    return f"{prefix}{re.sub(r'[^A-Za-z0-9_.-]', '_', path)[:keep]}-{digest}"


# ------------------------------------------------------------------ the API (plain functions: (app, query, body) -> data)

class App:
    def __init__(self, ws: Workspace):
        self.ws = ws
        self.runs: dict[str, dict[str, Any]] = {}     # runs started from this UI: key -> {"record": ..., "thread": ...}
        self.runs_lock = threading.Lock()

    # -- state / settings
    def state(self, q, b):
        ws = self.ws
        dirs = {k: {"path": str(ws.dir(k)), "exists": ws.dir(k).exists()} for k in KINDS}
        runner = ws.runner_argv()
        return {"version": VERSION, "copyright": COPYRIGHT, "product": PRODUCT, "logo": LOGO_DATA_URI, "logo_bg": LOGO_BG, "workspace_file": str(ws.file), "settings": ws.data, "dirs": dirs,
                "runner": runner, "ai": ai.status(ws), "scheduler": {**scheduler.heartbeat(ws), "installed": scheduler.installed()},
                "odbc_drivers": dataaccess.odbc_drivers()}

    def put_workspace(self, q, b):
        from framework import emailer

        problems = emailer.check_block(b.get("email"), require_host=False) if b.get("email") else []
        if problems:
            raise ApiError(400, "Email settings: " + "; ".join(problems))
        if "email" in b:
            self.ws.data["email"] = {}              # the email block is replaced as a whole (a removed field must stay removed)
        self.ws.save(b)
        self.ws.ensure_dirs()
        return self.state(q, b)

    # -- test cases
    def tests(self, q, b):
        return {"tests": testcases.list_tests(self.ws)}

    def get_test(self, q, b):
        try:
            return testcases.read_test(self.ws, q["path"])
        except WorkspaceError as exc:
            raise ApiError(404, str(exc))

    def extract_cases(self, q, b):
        try:
            return testcases.extract_cases(self.ws, b["path"], b.get("cases") or [], b["new_path"])
        except FileExistsError as exc:
            raise ApiError(409, str(exc))
        except (ValueError, WorkspaceError) as exc:
            raise ApiError(400, str(exc))

    # -- revisions (history of a suite and of its test cases)
    def _rev(self, fn, *args, **kw):
        try:
            return fn(self.ws.dir("test_cases"), *args, **kw)
        except revisions.RevisionError as exc:
            raise ApiError(400, str(exc))
        except (OSError, ValueError) as exc:
            raise ApiError(404, str(exc))

    def revisions_get(self, q, b):
        return self._rev(revisions.summary, q["path"])

    def revision_case(self, q, b):
        return {"case": self._rev(revisions.case_content, q["path"], q["case"], _int(q["rev"], "rev"))}

    def revision_manifest(self, q, b):
        return {"suite": self._rev(revisions.manifest_suite, q["path"], q["id"])}

    def revision_settings(self, q, b):
        return {"settings": self._rev(revisions.settings_content, q["path"], q["id"])}

    def revision_diff(self, q, b):
        if q.get("case"):
            a = self._rev(revisions.case_content, q["path"], q["case"], _int(q["a"], "a"))
            c = self._rev(revisions.case_content, q["path"], q["case"], _int(q["b"], "b"))
            return {"changes": revisions.diff_cases(a, c)}
        return self._rev(revisions.diff_manifests, q["path"], q["a"], q["b"])

    def revision_default(self, q, b):
        kind = b.get("kind")
        if kind == "case":
            return self._rev(revisions.make_default_case, b["path"], b["case"], _int(b["rev"], "rev"), "activated")
        if kind == "manifest":
            return self._rev(revisions.make_default_manifest, b["path"], b["id"], "activated")
        raise ApiError(400, "kind must be case or manifest")

    def revision_inplace(self, q, b):
        if b.get("kind") == "settings":
            self._rev(revisions.edit_settings_revision, b["path"], b["id"], b["content"], b.get("note", ""))
        else:
            self._rev(revisions.edit_case_revision, b["path"], b["case"], _int(b["rev"], "rev"), b["content"], b.get("note", ""))
        return {"saved": True}

    def revision_draft(self, q, b):
        return {"rev": self._rev(revisions.add_draft, b["path"], b["case"], b["content"], b.get("source", "manual"), b.get("note", ""))}

    def revision_delete(self, q, b):
        if q.get("kind") == "manifest":
            self._rev(revisions.delete_manifest, q["path"], q["id"])
        else:
            self._rev(revisions.delete_case_revision, q["path"], q["case"], _int(q["rev"], "rev"))
        return {"deleted": True}

    def recommendation(self, q, b):
        """Mark an agent recommendation applied / skipped, or remove it. (It is a note on the case, not a revision.)"""
        from framework import learn

        try:
            return {"recommendation": learn.set_status(self.ws.safe_path("test_cases", b["path"]), b["case"], b.get("action", ""))}
        except (ValueError, OSError) as exc:
            raise ApiError(400, str(exc))

    def put_test(self, q, b):
        try:
            return testcases.save_test(self.ws, b["path"], b["suite"], b.get("base_mtime"), overwrite=b.get("overwrite", True),
                                       source=b.get("source") or "manual", note=b.get("note") or "")
        except testcases.ConflictError as exc:
            raise ApiError(409, str(exc))
        except FileExistsError as exc:
            raise ApiError(409, str(exc))

    def create_test(self, q, b):
        try:
            return testcases.create_test(self.ws, b["path"], b.get("suite_id") or "SUITE-1", b.get("name") or "New suite", b.get("base_url", ""))
        except FileExistsError as exc:
            raise ApiError(409, str(exc))

    def delete_test(self, q, b):
        testcases.delete_test(self.ws, q["path"])
        return {"deleted": q["path"]}

    def duplicate_test(self, q, b):
        try:
            return testcases.duplicate_test(self.ws, b["path"], b["new_path"])
        except FileExistsError as exc:
            raise ApiError(409, str(exc))

    def validate(self, q, b):
        return {"problems": testcases.validate(b["suite"])}

    # -- import
    def import_inspect(self, q, b):
        return importer.inspect(self.ws, b)

    def import_convert(self, q, b):
        return importer.convert(self.ws, b)

    # -- variables / SQL / environments
    def environments(self, q, b):
        return {"environments": dataaccess.masked_environments(dataaccess.read_environments(self.ws))}   # passwords never go to the browser

    def put_environments(self, q, b):
        dataaccess.write_environments(self.ws, b["environments"])
        return {"saved": True}

    def email_test(self, q, b):
        """Send ONE test message to the address the user typed, using the environment's email settings (the unsaved ones from the page, if sent).
        The SMTP server must be the SAVED one: a request can never point the login (and the password variable) at another host."""
        from framework import emailer

        env = b.get("environment") or ""
        saved_env = (dataaccess.read_environments(self.ws).get(env, {}) or {}).get("email")
        if b.get("scope") == "settings":                    # the defaults typed on the Settings page, as they are
            block = b.get("email")
            saved = self.ws.data.get("email") or {}
        else:                                              # an environment's block (unsaved one from the page, if sent) over the Settings defaults
            block = emailer.merge(b.get("email") or saved_env, self.ws.data.get("email"))
            saved = emailer.merge(saved_env, self.ws.data.get("email")) or {}
        block = dict(block or {})
        saved_host = str(saved.get("smtp_host") or "").strip()
        if not saved_host:
            raise ApiError(400, "save the SMTP server first, then send the test message")
        if str(block.get("smtp_host") or "").strip().lower() != saved_host.lower():
            raise ApiError(400, f"the SMTP server on the page ({block.get('smtp_host') or 'none'}) is not the saved one ({saved_host}): save the settings first")
        block["smtp_host"] = saved_host
        to = emailer._addresses(b.get("to"))
        if not to:
            raise ApiError(400, "type the address to send the test message to")
        try:
            s = emailer.settings_for(block, {"on": "always", "to": to}, env)
            emailer.send(s, emailer.build_test_message(s, env))
        except emailer.EmailError as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "message": f"A test message was sent to {', '.join(to)}."}

    def sql_test(self, q, b):
        cs = b.get("connection_string", "")
        envs = dataaccess.read_environments(self.ws)
        if not cs and b.get("env"):
            cs = (envs.get(b["env"], {}).get("connections", {}) or {}).get(b.get("connection", "default"), "")
        try:
            cs = dataaccess.find_stored_connection(envs, cs, b.get("env") or "", b.get("connection") or "default")   # the page only has *** for the password
            return dataaccess.run_select(cs, b.get("query", ""))
        except ValueError as exc:
            raise ApiError(400, str(exc))
        except Exception as exc:  # noqa: BLE001 - driver/connection errors are shown to the user
            raise ApiError(400, f"{type(exc).__name__}: {str(exc)[:400]}")

    # -- sessions
    def sessions(self, q, b):
        return {"sessions": schedules.list_sessions(self.ws)}

    def get_session(self, q, b):
        return {"plan": schedules.read_session(self.ws, q["path"])}

    def put_session(self, q, b):
        try:
            return {"path": schedules.save_session(self.ws, b["path"], b["plan"])}
        except ValueError as exc:
            raise ApiError(400, str(exc))

    def delete_session(self, q, b):
        schedules.delete_session(self.ws, q["path"])
        return {"deleted": q["path"]}

    # -- schedules
    def get_schedules(self, q, b):
        out = []
        for s in schedules.list_schedules(self.ws):
            out.append({**s, "status": schedules.schedule_status(self.ws, s)})
        return {"schedules": out, "scheduler": {**scheduler.heartbeat(self.ws), "installed": scheduler.installed()}}

    def put_schedule(self, q, b):
        try:
            schedules.save_schedule(self.ws, b["schedule"])
        except ValueError as exc:
            raise ApiError(400, str(exc))
        return {"problems": schedules.check_schedule(self.ws, b["schedule"])}

    def delete_schedule(self, q, b):
        schedules.delete_schedule(self.ws, q["id"])
        return {"deleted": q["id"]}

    def schedule_history(self, q, b):
        return {"history": list(reversed(schedules.read_state(self.ws, q["id"]).get("history", [])))}

    def run_schedule_now(self, q, b):
        sched = schedules.load_schedule(self.ws, b["id"])
        return self._start_run(sched, "manual")

    def cancel_run(self, q, b):
        return {"cancelled": schedules.cancel_schedule(b["id"])}

    def scheduler_install(self, q, b):
        try:
            return {"message": scheduler.install(self.ws, b.get("at", "logon"))}
        except RuntimeError as exc:
            raise ApiError(400, str(exc))

    def scheduler_uninstall(self, q, b):
        try:
            return {"message": scheduler.uninstall()}
        except RuntimeError as exc:
            raise ApiError(400, str(exc))

    # -- ad-hoc runs (start a test from the UI)
    def run_now(self, q, b):
        if str(b.get("type", "")).startswith("group-"):   # a test group: every case ("group-all") or those not passed yet ("group-remaining")
            mode = b["type"].split("-", 1)[1]
            try:
                groups.run_plan(self.ws, b["path"], mode)      # fails early (closed / empty / nothing left) with a clear message
            except (ValueError, WorkspaceError) as exc:
                raise ApiError(400, str(exc))
            sched = {"id": adhoc_id(f"adhoc-group-{mode}-", b["path"], 60 - len(f"adhoc-group-{mode}-") - 9), "name": f"Group {b['path']}: {mode}", "results_subdir": "manual/group-" + b["path"],
                     "items": [{"type": "group", "path": b["path"], "mode": mode, "options": b.get("options") or {}}]}
            return self._start_run(sched, "manual")
        item = {"type": b.get("type", "suite"), "path": b["path"], "env": b.get("env") or "", "options": b.get("options") or {}}
        if b.get("cases") and item["type"] == "suite":
            if not isinstance(b["cases"], list):
                raise ApiError(400, "'cases' must be a list of test case ids")
            item["cases"] = [str(c) for c in b["cases"]]
        if b.get("tags") and item["type"] == "suite":
            if not isinstance(b["tags"], list):
                raise ApiError(400, "'tags' must be a list of tags")
            item["tags"] = [str(t) for t in b["tags"]]
        sid = adhoc_id("adhoc-", b["path"], 40)                 # the hash of the full path keeps a/login.json and b/login.json apart
        sched = {"id": sid, "name": f"Run {b['path']}", "items": [item], "results_subdir": "manual/" + sid}
        return self._start_run(sched, "manual")

    def _start_run(self, sched: dict[str, Any], trigger: str) -> dict[str, Any]:
        if self.ws.runner_argv() is None:
            raise ApiError(400, "testbot.exe was not found. Set 'Runner command' in Settings.")
        key = sched["id"]
        with self.runs_lock:
            cur = self.runs.get(key)
            if cur and cur["thread"].is_alive():
                raise ApiError(409, "that run is already in progress")

            def progress(record):
                with self.runs_lock:
                    self.runs[key]["record"] = dict(record)

            def work():
                rec = schedules.run_schedule(self.ws, sched, trigger, progress=progress)
                with self.runs_lock:
                    self.runs[key]["record"] = rec

            self.runs[key] = {"record": {"status": "starting", "schedule": key}, "thread": threading.Thread(target=work, daemon=True)}
            self.runs[key]["thread"].start()
        return {"run": key}

    def run_status(self, q, b):
        with self.runs_lock:
            cur = self.runs.get(q["id"])
            if not cur:
                raise ApiError(404, "unknown run")
            record = dict(cur["record"])
            alive = cur["thread"].is_alive()
        log = ""
        if record.get("dir"):
            lp = self.ws.safe_path("results", record["dir"]) / "run.log"
            if lp.exists():
                log = lp.read_text(encoding="utf-8", errors="replace")[-6000:]
        return {"record": record, "running": alive, "log": log}

    # -- results
    def get_results(self, q, b):
        return {"runs": results.list_runs(self.ws)}

    def get_result(self, q, b):
        try:
            return results.read_run(self.ws, q["path"])
        except WorkspaceError as exc:
            raise ApiError(404, str(exc))

    def get_result_log(self, q, b):
        try:
            return results.read_log(self.ws, q["path"])
        except WorkspaceError as exc:
            raise ApiError(404, str(exc))

    def results_view(self, q, b):
        view = q.get("view", "case")
        if view not in ("case", "suite", "session"):
            raise ApiError(400, "view must be case, suite or session")
        return {"rows": {"case": results.by_case, "suite": results.by_suite, "session": results.by_session}[view](self.ws)}

    def results_case(self, q, b):
        try:
            d = results.case_detail(self.ws, q["suite"], q["case"])
        except WorkspaceError as exc:
            raise ApiError(404, str(exc))
        d["groups"] = groups.groups_of(self.ws, q["suite"], q["case"])
        return d

    def results_suite(self, q, b):
        try:
            d = results.suite_detail(self.ws, q["suite"])
        except WorkspaceError as exc:
            raise ApiError(404, str(exc))
        d["groups"] = groups.groups_of(self.ws, q["suite"])
        return d

    def results_analysis(self, q, b):
        if q.get("kind") not in ("suite", "session"):
            raise ApiError(400, "kind must be suite or session")
        return groups.analysis(self.ws, q["kind"], q["id"], int(q.get("days") or 30))

    # -- test groups
    def groups_list(self, q, b):
        return {"groups": groups.list_groups(self.ws)}

    def _group_or_404(self, gid):
        try:
            return groups.read_group(self.ws, gid)
        except WorkspaceError as exc:
            raise ApiError(404, str(exc))

    def group_get(self, q, b):
        return {"group": self._group_or_404(q["id"])}

    def group_track(self, q, b):
        g = self._group_or_404(q["id"])
        return {"group": {k: v for k, v in g.items() if k not in ("snapshot", "manual")}, "track": groups.track(self.ws, g), "manual": g.get("manual", [])}

    def group_preview(self, q, b):
        res = groups.resolve(self.ws, b["group"])
        return {"count": len(res["cases"]), "overlap": res["overlap"], "members": [{k: v for k, v in m.items() if k != "keys"} for m in res["members"]],
                "cases": [{"suite_id": c["suite_id"], "case_id": c["case_id"], "title": c["title"]} for c in res["cases"]][:500]}

    def group_put(self, q, b):
        try:
            return {"group": groups.save_group(self.ws, b["group"], create=bool(b.get("create")))}
        except FileExistsError as exc:
            raise ApiError(409, str(exc))
        except (ValueError, WorkspaceError) as exc:
            raise ApiError(400, str(exc))

    def group_delete(self, q, b):
        groups.delete_group(self.ws, q["id"])
        return {"deleted": q["id"]}

    def group_duplicate(self, q, b):
        try:
            return {"group": groups.duplicate_group(self.ws, b["id"], b["new_id"])}
        except FileExistsError as exc:
            raise ApiError(409, str(exc))
        except (ValueError, WorkspaceError) as exc:
            raise ApiError(400, str(exc))

    def group_manual(self, q, b):
        try:
            return groups.add_manual(self.ws, b["id"], b["suite_id"], b["case_id"], b.get("status", ""), b.get("by", ""), b.get("comment", ""))
        except (ValueError, WorkspaceError) as exc:
            raise ApiError(400, str(exc))

    def group_close(self, q, b):
        try:
            g = groups.set_closed(self.ws, b["id"], bool(b.get("closed", True)))
        except WorkspaceError as exc:
            raise ApiError(404, str(exc))
        return {"group": {k: v for k, v in g.items() if k not in ("snapshot", "manual")}}

    def group_export(self, q, b):
        gid, fmt = q["id"], q.get("format", "csv")
        self._group_or_404(gid)
        if fmt == "html":
            return {"filename": f"{gid}-report.html", "content": groups.export_html(self.ws, gid)}
        return {"filename": f"{gid}-results.csv", "content": "\ufeff" + groups.export_csv(self.ws, gid)}

    def results_session(self, q, b):
        try:
            return results.session_detail(self.ws, q["session"])
        except WorkspaceError as exc:
            raise ApiError(404, str(exc))

    def result_case_run(self, q, b):
        try:
            return results.case_run(self.ws, q["path"], q["suite"], q["case"], int(q.get("occ") or 1), int(q["entry"]) if q.get("entry") else None)
        except WorkspaceError as exc:
            raise ApiError(404, str(exc))

    def result_iterations(self, q, b):
        return {"iterations": results.worker_results(self.ws, q["dir"])}

    # -- AI
    def ai_status(self, q, b):
        return ai.status(self.ws)

    def _ai(self, fn: Callable[[], dict[str, Any]]):
        try:
            return fn()
        except ai.AIError as exc:
            raise ApiError(400, str(exc))

    def ai_test(self, q, b):
        return ai.test_connection(self.ws, b)

    def ai_generate(self, q, b):
        return self._ai(lambda: ai.generate(self.ws, b.get("description", ""), b.get("base_url", ""), b.get("page_html", ""), b.get("variables")))

    def reviews_get(self, q, b):
        return reviews.pending(self.ws)

    # -- chat with the assistant about one suite
    def chat_get(self, q, b):
        return chat.load(self.ws, q["path"])

    def chat_send(self, q, b):
        try:
            return self._ai(lambda: chat.send(self.ws, b["path"], b.get("message", ""), b.get("case") or None, b.get("suite")))
        except WorkspaceError as exc:
            raise ApiError(404, str(exc))

    def chat_draft(self, q, b):
        try:
            return {"drafts": chat.apply_as_drafts(self.ws, b["path"], b["id"], b.get("note", ""))}
        except (WorkspaceError, revisions.RevisionError) as exc:
            raise ApiError(400, str(exc))

    def chat_mark(self, q, b):
        chat.mark(self.ws, b["path"], b["id"], b.get("status", "dismissed"))
        return {"ok": True}

    def chat_clear(self, q, b):
        chat.clear(self.ws, q["path"])
        return {"cleared": True}

    def ai_optimize(self, q, b):
        return self._ai(lambda: ai.optimize(self.ws, b["suite"], b.get("goals", ""), b.get("case_id") or None))

    def ai_convert(self, q, b):
        return self._ai(lambda: ai.convert(self.ws, b.get("content", ""), b.get("hint", ""), b.get("base_url", "")))


def routes(app: App) -> list[tuple[str, str, Callable]]:
    return [
        ("GET", "/api/state", app.state), ("PUT", "/api/workspace", app.put_workspace), ("GET", "/api/catalog", lambda q, b: catalog()),
        ("GET", "/api/tests", app.tests), ("GET", "/api/test", app.get_test), ("PUT", "/api/test", app.put_test), ("POST", "/api/recommendation", app.recommendation),
        ("POST", "/api/test", app.create_test), ("DELETE", "/api/test", app.delete_test), ("POST", "/api/test/duplicate", app.duplicate_test), ("POST", "/api/test/extract", app.extract_cases),
        ("POST", "/api/validate", app.validate),
        ("POST", "/api/import/inspect", app.import_inspect), ("POST", "/api/import/convert", app.import_convert),
        ("GET", "/api/environments", app.environments), ("PUT", "/api/environments", app.put_environments), ("POST", "/api/sql/test", app.sql_test), ("POST", "/api/email/test", app.email_test),
        ("GET", "/api/sessions", app.sessions), ("GET", "/api/session", app.get_session), ("PUT", "/api/session", app.put_session),
        ("DELETE", "/api/session", app.delete_session),
        ("GET", "/api/schedules", app.get_schedules), ("PUT", "/api/schedule", app.put_schedule), ("DELETE", "/api/schedule", app.delete_schedule),
        ("GET", "/api/schedule/history", app.schedule_history), ("POST", "/api/schedule/run", app.run_schedule_now),
        ("POST", "/api/schedule/cancel", app.cancel_run),
        ("POST", "/api/scheduler/install", app.scheduler_install), ("POST", "/api/scheduler/uninstall", app.scheduler_uninstall),
        ("POST", "/api/run", app.run_now), ("GET", "/api/run/status", app.run_status),
        ("GET", "/api/results", app.get_results), ("GET", "/api/result", app.get_result), ("GET", "/api/result/iterations", app.result_iterations), ("GET", "/api/result/log", app.get_result_log), ("GET", "/api/results/view", app.results_view), ("GET", "/api/results/case", app.results_case),
        ("GET", "/api/results/suite", app.results_suite),
        ("GET", "/api/revisions", app.revisions_get), ("GET", "/api/revision/case", app.revision_case), ("GET", "/api/revision/manifest", app.revision_manifest),
        ("GET", "/api/revision/settings", app.revision_settings), ("GET", "/api/revision/diff", app.revision_diff), ("POST", "/api/revision/default", app.revision_default),
        ("PUT", "/api/revision/inplace", app.revision_inplace), ("POST", "/api/revision/draft", app.revision_draft), ("DELETE", "/api/revision", app.revision_delete), ("GET", "/api/results/analysis", app.results_analysis),
        ("GET", "/api/groups", app.groups_list), ("GET", "/api/group", app.group_get), ("GET", "/api/group/track", app.group_track), ("POST", "/api/group/preview", app.group_preview),
        ("PUT", "/api/group", app.group_put), ("DELETE", "/api/group", app.group_delete), ("POST", "/api/group/duplicate", app.group_duplicate), ("POST", "/api/group/manual", app.group_manual),
        ("POST", "/api/group/close", app.group_close), ("GET", "/api/group/export", app.group_export), ("GET", "/api/results/session", app.results_session), ("GET", "/api/result/case", app.result_case_run),
        ("GET", "/api/ai/status", app.ai_status), ("POST", "/api/ai/test", app.ai_test), ("POST", "/api/ai/generate", app.ai_generate),
        ("POST", "/api/ai/optimize", app.ai_optimize), ("GET", "/api/reviews", app.reviews_get), ("GET", "/api/chat", app.chat_get), ("POST", "/api/chat", app.chat_send), ("POST", "/api/chat/draft", app.chat_draft),
        ("POST", "/api/chat/mark", app.chat_mark), ("DELETE", "/api/chat", app.chat_clear), ("POST", "/api/ai/convert", app.ai_convert),
    ]


# ------------------------------------------------------------------ HTTP plumbing

def make_handler(app: App, token: str, allowed_hosts: set[str]):
    table = {(m, p): fn for m, p, fn in routes(app)}

    class Handler(BaseHTTPRequestHandler):
        server_version = "testbot-manager"

        def log_message(self, fmt, *args):  # the access log goes to the manager log at DEBUG only
            log.debug(fmt, *args)

        # ---- helpers
        def _send(self, status: int, body: bytes, content_type: str, extra: Optional[dict[str, str]] = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: Any):
            self._send(status, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "").lower() in allowed_hosts

        def _authed(self) -> bool:
            cookie = SimpleCookie(self.headers.get("Cookie") or "")
            supplied = cookie["tb_token"].value if "tb_token" in cookie else self.headers.get("X-Testbot-Token", "")
            return secrets.compare_digest(supplied, token)

        def _serve_static(self, name: str):
            path = (STATIC_DIR / name).resolve()
            if STATIC_DIR not in path.parents or not path.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            self._send(200, path.read_bytes(), ctype)

        # ---- dispatch
        def _handle(self, method: str):
            if not self._host_ok():
                return self._send(403, b"bad host", "text/plain")
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/" and query.get("token"):                       # first visit: trade the token for a cookie
                if secrets.compare_digest(query["token"], token):
                    return self._send(302, b"", "text/plain", {"Location": "/", "Set-Cookie": f"tb_token={token}; Path=/; HttpOnly; SameSite=Strict"})
                return self._send(403, b"bad token", "text/plain")
            if not self._authed():
                return self._send(403, "Not authorised. Open the address printed by the testbot manager (it contains a one-time token).".encode(), "text/plain")
            if method == "GET" and url.path == "/":
                return self._serve_static("index.html")
            if method == "GET" and url.path.startswith("/static/"):
                return self._serve_static(url.path[len("/static/"):])
            if method == "GET" and url.path.startswith("/files/results/"):
                try:
                    path, ctype = results.file_for_serving(app.ws, unquote(url.path[len("/files/results/"):]))
                except WorkspaceError:
                    return self._send(404, b"not found", "text/plain")
                return self._send(200, path.read_bytes(), ctype, {"Content-Security-Policy": RESULT_FILE_CSP})
            fn = table.get((method, url.path))
            if fn is None:
                return self._json(404, {"error": f"no such endpoint {method} {url.path}"})
            body: Any = {}
            if method in ("POST", "PUT", "DELETE"):
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._json(413, {"error": "request too large"})
                raw = self.rfile.read(length) if length else b""
                if raw:
                    try:
                        body = json.loads(raw.decode("utf-8"))
                    except ValueError:
                        return self._json(400, {"error": "the request body is not valid JSON"})
            try:
                return self._json(200, fn(query, body))
            except ApiError as exc:
                return self._json(exc.status, {"error": str(exc), **exc.extra})
            except WorkspaceError as exc:
                return self._json(400, {"error": str(exc)})
            except KeyError as exc:
                return self._json(400, {"error": f"missing field {exc}"})
            except (ValueError, TypeError) as exc:
                return self._json(400, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - never crash the server on one bad request
                log.error("%s %s failed: %s", method, self.path.split("?")[0], traceback.format_exc())
                return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

    return Handler


def create_server(ws: Workspace, host: Optional[str] = None, port: Optional[int] = None, token: Optional[str] = None):
    """Returns (server, url_with_token). The caller runs server.serve_forever()."""
    host = host or ws.data["server"]["host"]
    port = port if port is not None else int(ws.data["server"]["port"])
    token = token or secrets.token_urlsafe(24)
    ws.ensure_dirs()
    app = App(ws)
    httpd = ThreadingHTTPServer((host, port), BaseHTTPRequestHandler)   # handler class is set once the real port is known
    actual_port = httpd.server_address[1]
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    allowed = {f"{h}:{actual_port}" for h in {host, shown, "127.0.0.1", "localhost"}}
    httpd.RequestHandlerClass = make_handler(app, token, allowed)
    httpd.app = app  # type: ignore[attr-defined]
    return httpd, f"http://{shown}:{actual_port}/?token={token}"


def serve(ws: Workspace, host: Optional[str] = None, port: Optional[int] = None, open_browser: bool = True) -> None:
    httpd, url = create_server(ws, host, port)
    log.info("manager started: %s (workspace %s)", url.split("?")[0], ws.file)
    print(f"testbot manager running.  Workspace: {ws.file}\nOpen: {url}\n(Press Ctrl+C to stop.)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        log.info("manager stopped")
