"""Build-time helper: the Windows "file properties" (Details tab) of each .exe -- company, copyright, file/product version --
generated from framework/version.py so every program carries the same IACF notice and number. Used by the PyInstaller specs."""
from __future__ import annotations

import os

from framework.version import COMPANY, COPYRIGHT, PRODUCT, __version__, version_tuple


def write_version_file(path: str, internal_name: str, description: str, original_filename: str) -> str:
    v = version_tuple()
    text = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', {COMPANY!r}),
      StringStruct('FileDescription', {description!r}),
      StringStruct('FileVersion', {__version__!r}),
      StringStruct('InternalName', {internal_name!r}),
      StringStruct('LegalCopyright', {COPYRIGHT!r}),
      StringStruct('OriginalFilename', {original_filename!r}),
      StringStruct('ProductName', {PRODUCT!r}),
      StringStruct('ProductVersion', {__version__!r})])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
