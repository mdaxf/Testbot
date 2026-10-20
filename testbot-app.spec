import os, sys
sys.path.insert(0, os.getcwd())          # run from the project folder:  pyinstaller testbot-app.spec
from framework.versioninfo import write_version_file   # file properties (company/copyright/version) come from framework/version.py

# ONE application folder (onedir) instead of three self-extracting single-file exes.
#   testbot/testbot.exe, testbot-manager.exe, testbot-recorder.exe   -- thin launchers (a few MB each)
#   testbot/_internal/                                               -- Python, libraries and Chromium, shared by all three
# Nothing is unpacked to %TEMP% at start-up, which is what restricted networks / endpoint protection tend to block in onefile exes.
#
# Prerequisite (same venv): PLAYWRIGHT_BROWSERS_PATH=0 python -m playwright install chromium

from PyInstaller.utils.hooks import collect_all


def collect(pkgs):
    d, b, h = [], [], []
    for pkg in pkgs:
        pd, pb, ph = collect_all(pkg)
        d += pd; b += pb; h += ph
    return d, b, h


def analysis(script, pkgs, extra_datas=(), extra_hidden=(), excludes=()):
    d, b, h = collect(pkgs)
    return Analysis([script], pathex=["."], binaries=b, datas=d + list(extra_datas), hiddenimports=h + list(extra_hidden),
                    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=list(excludes), noarchive=False)


RUNNER_PKGS = ("playwright", "pyodbc", "google.genai", "openai", "anthropic", "paho", "pika", "kafka")
RUNNER_HIDDEN = ["pandas", "openpyxl", "yaml", "faker"]

a_run = analysis("scripts/testbot.py", RUNNER_PKGS, extra_hidden=RUNNER_HIDDEN)
a_rec = analysis("scripts/recorder.py", RUNNER_PKGS, extra_hidden=RUNNER_HIDDEN)
a_mgr = analysis("scripts/manager.py", ("pyodbc", "anthropic", "openai", "google.genai"),
                 extra_datas=[("framework/manager/static", "framework/manager/static")],
                 extra_hidden=["pandas", "openpyxl", "yaml", "certifi", "httpx", "httpx2", "framework.converters.apriso_bpa"],
                 excludes=("playwright", "tkinter", "faker"))

exes, parts = [], []
for a, name, desc, console in (
    (a_run, "testbot", "testbot - web UI test runner", True),
    (a_mgr, "testbot-manager", "testbot manager - test case management UI and scheduler", True),
    (a_rec, "testbot-recorder", "testbot recorder - records a browser session as a test case", False),
):
    vf = write_version_file(os.path.join("build", f"version_info_{name}.txt"), name, desc, f"{name}.exe")
    exes.append(EXE(PYZ(a.pure), a.scripts, [], exclude_binaries=True, name=name, debug=False, bootloader_ignore_signals=False,
                    strip=False, upx=False, console=console, disable_windowed_traceback=False, argv_emulation=False, version=vf))
    parts += [a.binaries, a.datas]

coll = COLLECT(*exes, *parts, strip=False, upx=False, upx_exclude=[], name="testbot")


# ---- sample configuration and docs, placed NEXT TO the exes (the programs read config/ and .env beside themselves; datas would
# ---- end up inside _internal/). Existing files in the output folder are never replaced, so re-building over a working copy keeps its settings.
import shutil

_out = os.path.join(DISTPATH, "testbot")
for _src, _dst in (
    ("config", "config"),                                   # environments.yaml, apriso_control_map.yaml, app_profile_apriso.yaml
    (".env.example", ".env.example"),                       # every setting; copy to .env
    ("test_cases/examples", "test_cases/examples"),
    ("test_cases/ad_demo", "test_cases/ad_demo"),
    ("docs/USER_MANUAL.md", "USER_MANUAL.md"),
    ("docs/AGENTIC_TESTING_RESEARCH.md", "AGENTIC_TESTING_RESEARCH.md"),
    ("README.md", "README.md"),
):
    _s, _d = os.path.join(SPECPATH, _src), os.path.join(_out, _dst)
    if os.path.isdir(_s):
        shutil.copytree(_s, _d, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    elif os.path.isfile(_s):
        os.makedirs(os.path.dirname(_d), exist_ok=True)
        if not os.path.exists(_d):
            shutil.copy2(_s, _d)
