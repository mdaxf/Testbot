import os, sys
sys.path.insert(0, os.getcwd())          # the specs are run from the project folder
from framework.versioninfo import write_version_file   # file properties (company/copyright/version) come from framework/version.py
_VERSION_FILE = write_version_file(os.path.join('build', 'version_info_testbot-manager.txt'), 'testbot-manager', 'testbot manager - test case management UI and scheduler', 'testbot-manager.exe')

# PyInstaller spec for testbot-manager.exe: the browser UI + scheduler. It does NOT include Playwright/Chromium
# (it never drives a browser itself -- it starts testbot.exe for that), so it is far smaller than testbot.exe.
#
#   pyinstaller manager.spec

from PyInstaller.utils.hooks import collect_all

datas = [("framework/manager/static", "framework/manager/static")]
binaries = []
hiddenimports = []

# AI provider SDKs are imported lazily (only when an AI button is pressed), so collect them explicitly
for pkg in ("pyodbc", "anthropic", "openai", "google.genai"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

hiddenimports += ["pandas", "openpyxl", "yaml", "certifi", "httpx", "httpx2", "framework.converters.apriso_bpa"]

a = Analysis(
    ["scripts/manager.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["playwright", "tkinter", "faker"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="testbot-manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,   # shows the address to open and the scheduler's log; the UI itself opens in your browser
    disable_windowed_traceback=False,
    argv_emulation=False,
    onefile=True,
    version=_VERSION_FILE,
)
