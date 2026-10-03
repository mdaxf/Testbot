"""Convert an Apriso "AutomaticTest" JSON scenario (TestCase / Screen / Elements[Input, Action,
Result], run by the C# Selenium framework) into a testbot suite.

Control types -> CSS selectors and behaviour come from config/apriso_control_map.yaml, so the
mapping can be corrected or extended without touching this code."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from framework.models import TestSuite


class _Builder:
    def __init__(self, cmap: dict[str, Any]):
        self.cmap = cmap
        self.steps: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.in_screen = False
        seq = cmap["sequence"]
        self.seq_var, self.seq_query, self.seq_bumper = seq["var"], seq["query"], seq["bumped_by"]
        self.seq_stale = True
        self.uses_sql = False

    # ---------------------------------------------------------------- helpers
    def add(self, description: str, action: str, **fields: Any) -> None:
        self.steps.append({"step_no": len(self.steps) + 1, "description": description, "action": action, **fields})

    def target(self, css: str, *, popup: bool = False, frame: bool = True) -> dict[str, Any]:
        t: dict[str, Any] = {"strategy": "css", "value": css}
        if self.in_screen and frame and self.cmap.get("frame"):
            t["frame"] = self.cmap["frame"]
        if popup:
            t["scope_if_present"] = self.cmap["popup_scope"]
        if frame and self.cmap.get("first_match"):
            t["nth"] = 1
        return t

    def spinner_wait(self, *, frame: bool = True, why: str = "") -> None:
        self.add(f"Wait for the Apriso spinner to clear{why}", "wait_until",
                 target=self.target(self.cmap["spinner"], frame=frame), input="hidden",
                 timeout_ms=self.cmap["spinner_timeout_ms"])

    def subst(self, text: Any, description: str, *, sql: bool = False) -> tuple[str, bool]:
        """Apply the JSON's token conventions: '#' -> the sequence variable. Returns (text, uses_seq)."""
        text = "" if text is None else str(text)
        if "@" in text and not sql:  # in SQL, '@name' is just a T-SQL parameter
            self.warnings.append(f"'{description}': '@' variable reference in {text!r} is not converted (set it up by hand)")
        uses = "#" in text
        return text.replace("#", "{" + self.seq_var + "}"), uses

    def ensure_seq(self) -> None:
        if self.seq_stale:
            self.add("Read the automated-test sequence number (the JSON's '#')", "sql_query", query=self.seq_query,
                     capture={"var": self.seq_var, "from": "sql_column"})
            self.seq_stale = False
            self.uses_sql = True

    @staticmethod
    def css_quote(text: str) -> str:
        return f'"{text}"' if "'" in text else f"'{text}'"

    def build_css(self, template: str, identifier: str, value: str) -> str:
        ident = identifier.strip()
        css = template
        if "{row}" in css or "{field}" in css:
            parts = [p.strip() for p in ident.split(",")]
            row, field = (parts[0], parts[1]) if len(parts) == 2 else (None, parts[0])
            css = css.replace("{row}", f":nth-child({int(row) + 1})" if row else ":last-child")
            css = css.replace("{field}", self.css_quote(field))
        if "{n}" in css:
            css = css.replace(":nth-child({n})", f":nth-child({int(value) + 1})") if value.strip() else css.replace(":nth-child({n})", ":last-child")
        return css.replace("{id}", self.css_quote(ident)).replace("{raw}", ident)

    # ---------------------------------------------------------------- one control
    def emit(self, section: str, block: dict[str, Any], description: str) -> None:
        ctype = block.get("Type", "")
        control = self.cmap["controls"].get(ctype)
        if control is None or control["role"] != section:
            self.warnings.append(f"'{description}': {section} type '{ctype}' is not in the control map -- step skipped")
            return
        is_sql = control["op"] in ("sql_exec", "sql_query")
        identifier, id_seq = self.subst(block.get("Identifier"), description, sql=is_sql)
        value, val_seq = self.subst(block.get("Value"), description)
        if id_seq or val_seq:
            self.ensure_seq()
        label = f"{description} [{ctype} {identifier.strip()[:60]}]" if identifier.strip() else f"{description} [{ctype}]"
        op = control["op"]

        if op == "sql_exec":
            self.uses_sql = True
            self.add(label, "sql_exec", query=identifier)
            if self.seq_bumper in identifier:
                self.seq_stale = True
            return
        if op == "sql_query":
            self.uses_sql = True
            # poll: the app may still be committing when the C# runner's fixed pauses would have covered it
            self.add(label, "sql_query", query=identifier, expected={"type": "sql_result_equals", "value": value},
                     config={"poll": True}, timeout_ms=15000)
            return

        css = self.build_css(control["css"], identifier, value)
        tgt = self.target(css, popup=control.get("popup_aware", False), frame=control.get("frame", True))
        if op == "click" or op == "select_row":
            self.add(label, "click", target=tgt)
        elif op == "hover":
            self.add(label, "hover", target=tgt)
        elif op == "type":
            self.add(label, "type", target=tgt, input=value)
        elif op == "type_enter":
            self.add(label, "type", target=tgt, input=value)
            self.add(f"{label} -- press Enter", "press", target=tgt, input="Enter")
        elif op == "select":
            self.add(label, "select", target=tgt, input=value)
        elif op == "check":
            self.add(label, "check", target=tgt, input=value.lower() or "true")
        else:
            self.warnings.append(f"'{description}': unknown op '{op}' for {ctype}")
            return
        if control.get("spinner"):
            self.spinner_wait()

    # ---------------------------------------------------------------- login + screen
    def prelude(self, screen: Optional[str]) -> None:
        login = self.cmap["login"]
        self.add("Open the Apriso login page", "navigate", target={"strategy": "url", "value": "{login_url}"})
        if login.get("select_method"):
            self.add("Choose 'Standard Login' on the start page (logon.html)", "click",
                     target={"strategy": "css", "value": login["select_method"]})
        self.add("Wait for the login form", "wait_until", target={"strategy": "css", "value": login["form"]},
                 input="visible", timeout_ms=10000)
        self.add("Enter the user name", "type", target={"strategy": "css", "value": login["user_field"]}, input="{login_name}")
        self.add("Enter the password", "type", target={"strategy": "css", "value": login["password_field"]}, input="{login_password}")
        self.add("Click Log in", "click", target={"strategy": "css", "value": login["submit"]})
        self.add("Wait for the login to complete (login form gone)", "wait_until",
                 target={"strategy": "css", "value": login["user_field"]}, input="hidden", timeout_ms=30000)
        if login.get("home_url_var"):
            self.add("Open the Apriso portal home", "navigate", target={"strategy": "url", "value": "{" + login["home_url_var"] + "}"})
        if screen:
            self.add("Wait for the portal search box", "wait_until", target={"strategy": "css", "value": login["search_box"]},
                     input="visible", timeout_ms=30000)
            self.add("Let the portal finish loading its screen list", "wait", input="3000")
            self.add(f"Search for the screen '{screen}'", "type", target={"strategy": "css", "value": login["search_box"]}, input=screen)
            self.add("Wait for the search result", "wait_until", target={"strategy": "css", "value": login["search_item"]},
                     input="visible", timeout_ms=30000)
            self.add(f"Open the screen '{screen}'", "click", target={"strategy": "css", "value": login["first_item"]})
            self.add("Wait for the screen to load", "wait_until", target={"strategy": "css", "value": self.cmap["frame"]},
                     input="visible", timeout_ms=self.cmap["spinner_timeout_ms"])
            self.in_screen = True
            self.spinner_wait(why=" after opening the screen")


def _epilogue(b: "_Builder") -> None:
    login = b.cmap["login"]
    if not login.get("logout_menu"):
        return
    b.in_screen = False  # the header lives in the top document, not the screen iframe
    # the full-screen screen iframe overlays the header, so click via the DOM instead of by coordinates
    b.add("Open the user menu", "click", target={"strategy": "css", "value": login["logout_menu"]}, config={"js_click": True})
    b.add("Log out (frees the Apriso licence slot)", "click", target={"strategy": "css", "value": login["logout_item"]}, config={"js_click": True})
    b.add("Wait for the logout to complete", "wait_until", target={"strategy": "css", "value": login["logout_menu"]},
          input="hidden", timeout_ms=30000)


def convert(source: dict[str, Any], cmap: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    b = _Builder(cmap)
    b.prelude(source.get("Screen"))
    for element in source.get("Elements", []):
        desc = re.sub(r"\s+", " ", html.unescape(str(element.get("Description", "")))).strip()
        prefix = f"[{element.get('TestStep')}] {desc}"
        for section in ("Input", "Action", "Result"):
            block = element.get(section)
            if block:
                b.emit(section.lower(), block, prefix)

    _epilogue(b)

    number = source.get("TestCase", 1)
    name = str(source.get("Description", f"Apriso scenario {number}"))
    suite: dict[str, Any] = {
        "suite_id": f"APRISO-{number}", "suite_name": name, "on_case_fail": "continue",
        "default_step_delay_ms": cmap["default_step_delay_ms"],
        "variables": {
            "login_url": {"source": "constant", "value": "http://CHANGE_ME/apriso/portal"},
            **({cmap["login"]["home_url_var"]: {"source": "constant", "value": "http://CHANGE_ME/apriso/apriso"}}
               if cmap["login"].get("home_url_var") else {}),
            "login_name": {"source": "constant", "value": source.get("LoginName", "CHANGE_ME")},
            "login_password": {"source": "constant", "value": "CHANGE_ME"},
        },
        "cases": [{
            "id": f"TC-{int(number):03d}" if str(number).isdigit() else str(number), "title": name,
            "area_path": source.get("Screen"),
            "preconditions": ("Set login_url (and portal_home_url) and login_password in the suite variables."
                              + (" SQL steps need connection 'default' (suite `connections` or --env)." if b.uses_sql else "")),
            "steps": b.steps,
        }],
    }
    TestSuite.model_validate(suite)  # guarantees the runner can load what we wrote
    return suite, b.warnings


def convert_file(src: Path, dst: Path, map_path: Path) -> list[str]:
    cmap = yaml.safe_load(map_path.read_text(encoding="utf-8"))
    source = json.loads(src.read_text(encoding="utf-8-sig"))
    suite, warnings = convert(source, cmap)
    dst.write_text(json.dumps(suite, indent=2, ensure_ascii=False), encoding="utf-8")
    return warnings
