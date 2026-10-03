import os, sys
sys.path.insert(0, os.getcwd())          # the specs are run from the project folder
from framework.versioninfo import write_version_file   # file properties (company/copyright/version) come from framework/version.py
_VERSION_FILE = write_version_file(os.path.join('build', 'version_info_testbot-recorder.txt'), 'testbot-recorder', 'testbot recorder - records a browser session as a test case', 'testbot-recorder.exe')

# PyInstaller spec for the windowed recorder app testbot-recorder.exe (same bundling as testbot.spec) -- no pip install, no
# `playwright install` needed on the machine that runs it.
#
# Prerequisite (must be done in THIS venv before running `pyinstaller testbot.spec`):
#   PLAYWRIGHT_BROWSERS_PATH=0 python -m playwright install chromium
# This installs Chromium (and the headless-shell variant it also requires) INSIDE
# site-packages/playwright/driver/package/.local-browsers/ instead of the global user
# cache, so --collect-all playwright below picks the browser binaries up as ordinary
# package data. See "Building a standalone executable" in README.md for the full story,
# including why headless-shell can't be dropped even though headless launches end up
# using chrome.exe when running from this bundled layout.

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

for pkg in ("playwright", "pyodbc", "google.genai", "openai", "anthropic", "paho", "pika", "kafka"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

hiddenimports += ["pandas", "openpyxl", "yaml", "faker"]

a = Analysis(
    ["scripts/recorder.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="testbot-recorder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    onefile=True,
    version=_VERSION_FILE,
)
