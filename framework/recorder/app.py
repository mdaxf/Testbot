from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Optional

from framework.recorder.exporters import export_all
from framework.version import COPYRIGHT, __version__
from framework.recorder.session import BROWSERS, RecordingSession

_STRATEGIES = ["css", "text", "role", "label", "placeholder", "testid"]
_CONDITIONS = ["hidden", "visible", "has_value", "value", "text", "text_contains"]


def _default_out_dir() -> Path:
    from framework.cli import app_root

    return app_root() / "test_cases" / "recorded"


class WaitUntilDialog(tk.Toplevel):
    """Manual step: wait for an element (e.g. a 'Loading' spinner) to disappear, or a field to get a value."""

    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.title("Insert wait_until step")
        self.resizable(False, False)
        self.result: Optional[dict[str, Any]] = None
        self.strategy = tk.StringVar(value="text")
        self.value = tk.StringVar()
        self.name = tk.StringVar()
        self.condition = tk.StringVar(value="hidden")
        self.arg = tk.StringVar()
        self.timeout = tk.StringVar(value="30")
        rows = [
            ("Find element by", ttk.Combobox(self, textvariable=self.strategy, values=_STRATEGIES, state="readonly")),
            ("Value (e.g. Loading, #spinner)", ttk.Entry(self, textvariable=self.value, width=34)),
            ("Accessible name (role only)", ttk.Entry(self, textvariable=self.name, width=34)),
            ("Wait until it is", ttk.Combobox(self, textvariable=self.condition, values=_CONDITIONS, state="readonly")),
            ("Expected text/value (if needed)", ttk.Entry(self, textvariable=self.arg, width=34)),
            ("Timeout (seconds)", ttk.Entry(self, textvariable=self.timeout, width=8)),
        ]
        for i, (text, widget) in enumerate(rows):
            ttk.Label(self, text=text).grid(row=i, column=0, sticky="w", padx=8, pady=4)
            widget.grid(row=i, column=1, sticky="w", padx=8, pady=4)
        buttons = ttk.Frame(self)
        buttons.grid(row=len(rows), column=0, columnspan=2, pady=8)
        ttk.Button(buttons, text="Insert", command=self._ok).pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        self.transient(parent)
        self.grab_set()

    def _ok(self) -> None:
        if not self.value.get().strip():
            messagebox.showerror("Missing value", "Enter the element text/selector to wait on.", parent=self)
            return
        try:
            timeout_ms = int(float(self.timeout.get()) * 1000)
        except ValueError:
            messagebox.showerror("Bad timeout", "Timeout must be a number of seconds.", parent=self)
            return
        cond = self.condition.get()
        needs_arg = cond in ("value", "text", "text_contains")
        if needs_arg and not self.arg.get():
            messagebox.showerror("Missing text", f"'{cond}' needs the expected text/value.", parent=self)
            return
        target: dict[str, Any] = {"strategy": self.strategy.get(), "value": self.value.get().strip()}
        if self.strategy.get() == "role" and self.name.get().strip():
            target["name"] = self.name.get().strip()
        self.result = {
            "description": f"Wait until '{target['value']}' is {cond}" + (f" '{self.arg.get()}'" if needs_arg else ""),
            "action": "wait_until", "target": target,
            "input": f"{cond}:{self.arg.get()}" if needs_arg else cond, "timeout_ms": timeout_ms,
        }
        self.destroy()


class RecorderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"testbot recorder {__version__}  \u2014  {COPYRIGHT}")
        root.geometry("760x600")
        self.session: Optional[RecordingSession] = None
        self._seen_version = -1

        self.url = tk.StringVar(value="https://")
        self.browser = tk.StringVar(value="chromium")
        self.suite_id = tk.StringVar(value="REC-001")
        self.suite_name = tk.StringVar(value="Recorded suite")
        self.case_title = tk.StringVar(value="Recorded scenario")
        self.out_dir = tk.StringVar(value=str(_default_out_dir()))
        self.fmt_json, self.fmt_excel, self.fmt_py = tk.BooleanVar(value=True), tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Enter the start URL, pick a browser, press Start recording.")

        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Start URL").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.url, width=60).grid(row=0, column=1, sticky="we", padx=6)
        ttk.Label(top, text="Browser").grid(row=0, column=2, sticky="w")
        ttk.Combobox(top, textvariable=self.browser, values=list(BROWSERS), state="readonly", width=10).grid(row=0, column=3, padx=6)
        top.columnconfigure(1, weight=1)

        bar = ttk.Frame(root, padding=(8, 0))
        bar.pack(fill="x")
        self.start_btn = ttk.Button(bar, text="Start recording", command=self.start)
        self.stop_btn = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
        self.wait_btn = ttk.Button(bar, text="Insert wait_until…", command=self.insert_wait, state="disabled")
        for b in (self.start_btn, self.stop_btn, self.wait_btn):
            b.pack(side="left", padx=(0, 6), pady=4)
        ttk.Button(bar, text="Delete selected step", command=self.delete_selected).pack(side="left", padx=(12, 6))
        ttk.Button(bar, text="Clear all", command=self.clear).pack(side="left")

        frame = ttk.Frame(root, padding=8)
        frame.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(frame, activestyle="none", selectmode="extended")
        scroll = ttk.Scrollbar(frame, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

        save = ttk.LabelFrame(root, text="Save", padding=8)
        save.pack(fill="x", padx=8, pady=(0, 4))
        for col, (label, var, width) in enumerate(
            [("Suite ID", self.suite_id, 14), ("Suite name", self.suite_name, 24), ("Case title", self.case_title, 24)]
        ):
            ttk.Label(save, text=label).grid(row=0, column=col * 2, sticky="w")
            ttk.Entry(save, textvariable=var, width=width).grid(row=0, column=col * 2 + 1, padx=(4, 10))
        ttk.Label(save, text="Folder").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(save, textvariable=self.out_dir, width=64).grid(row=1, column=1, columnspan=4, sticky="we", padx=4)
        ttk.Button(save, text="Browse…", command=self.browse).grid(row=1, column=5)
        opts = ttk.Frame(save)
        opts.grid(row=2, column=0, columnspan=6, sticky="w")
        ttk.Checkbutton(opts, text="JSON suite", variable=self.fmt_json).pack(side="left", padx=4)
        ttk.Checkbutton(opts, text="Excel workbook", variable=self.fmt_excel).pack(side="left", padx=4)
        ttk.Checkbutton(opts, text="Playwright Python script", variable=self.fmt_py).pack(side="left", padx=4)
        ttk.Button(opts, text="Save", command=self.save).pack(side="left", padx=16)

        ttk.Label(root, textvariable=self.status, relief="sunken", anchor="w", padding=4).pack(fill="x", side="bottom")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(300, self._poll)

    # ------------------------------------------------------------------ actions
    def start(self) -> None:
        url = self.url.get().strip()
        if not url.lower().startswith(("http://", "https://", "file://")):
            messagebox.showerror("Start URL", "The start URL must begin with http://, https:// or file://")
            return
        if self.session is not None and self.session.steps:
            if not messagebox.askyesno("New recording", "Discard the steps already recorded?"):
                return
        self.status.set("Starting the browser…")
        self.root.update_idletasks()
        try:
            self.session = RecordingSession(url, self.browser.get())
            self.session.start()
        except Exception as exc:  # noqa: BLE001
            self.session = None
            text = str(exc)
            if "net::ERR" in text:
                other = "http://" if url.lower().startswith("https://") else "https://"
                messagebox.showerror(
                    "Could not open the start URL",
                    f"The browser started but could not load\n{url}\n\n{text.splitlines()[0]}\n\n"
                    f"Check the address and the scheme -- the site may only answer on {other}",
                )
                self.status.set(f"Could not open {url} -- check the address / http vs https.")
            else:
                messagebox.showerror("Could not start the browser", text)
                self.status.set("Failed to start. Is the selected browser installed?")
            return
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.wait_btn.configure(state="normal")
        self.status.set("Recording -- use the browser window normally. Close it or press Stop when done.")

    def stop(self) -> None:
        if self.session is not None:
            self.session.stop()
        self._after_stopped()

    def _after_stopped(self) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.wait_btn.configure(state="disabled")
        count = len(self.session.steps) if self.session else 0
        self.status.set(f"Stopped. {count} step(s) captured -- review, then Save.")

    def insert_wait(self) -> None:
        if self.session is None:
            return
        dialog = WaitUntilDialog(self.root)
        self.root.wait_window(dialog)
        if dialog.result:
            self.session.add_manual_step(dialog.result)

    def delete_selected(self) -> None:
        if self.session is None:
            return
        for index in sorted(self.listbox.curselection(), reverse=True):
            self.session.delete_step(index)

    def clear(self) -> None:
        if self.session is not None and messagebox.askyesno("Clear", "Delete all recorded steps?"):
            self.session.clear()

    def browse(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.out_dir.get() or None)
        if chosen:
            self.out_dir.set(chosen)

    def save(self) -> None:
        if self.session is None or not self.session.steps:
            messagebox.showinfo("Nothing to save", "Record some steps first.")
            return
        formats = {n for n, v in (("json", self.fmt_json), ("excel", self.fmt_excel), ("playwright", self.fmt_py)) if v.get()}
        if not formats:
            messagebox.showinfo("Pick a format", "Tick at least one output format.")
            return
        try:
            written = export_all(
                self.session, Path(self.out_dir.get()), suite_id=self.suite_id.get().strip() or "REC-001",
                suite_name=self.suite_name.get(), case_id="TC-001", title=self.case_title.get(), formats=formats,
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Save failed", str(exc))
            return
        self.status.set("Saved: " + ", ".join(p.name for p in written))
        messagebox.showinfo("Saved", "\n".join(str(p) for p in written))

    # ------------------------------------------------------------------ live view
    def _poll(self) -> None:
        session = self.session
        if session is not None:
            if session.version != self._seen_version:
                self._seen_version = session.version
                with session.lock:
                    lines = [
                        f"{i}. [{s['action']}] {s['description']}"
                        + (f"   -> URL contains {s['expected']['value']}" if s.get("expected") else "")
                        for i, s in enumerate(session.steps, start=1)
                    ]
                self.listbox.delete(0, "end")
                for line in lines:
                    self.listbox.insert("end", line)
                self.listbox.see("end")
            if not session.running and str(self.stop_btn["state"]) == "normal":
                if session.error:
                    messagebox.showerror("Recorder stopped", session.error)
                self._after_stopped()  # the user closed the browser window
        self.root.after(300, self._poll)

    def on_close(self) -> None:
        if self.session is not None and self.session.running:
            self.session.stop()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    RecorderApp(root)
    root.mainloop()
    return 0
