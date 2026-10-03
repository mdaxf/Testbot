"""Stamp the IACF mark onto documentation screenshots (docs/images/*.png), no image library needed (a headless browser composites it).

    python scripts/stamp_images.py [folder] [--cover-legacy]        (default: docs/images)
    --cover-legacy: one-off for images that carry the old text-only mark (re-stamps everything, hiding the old mark first)

Safe to re-run: a manifest (.stamped.json, file name -> checksum of the stamped result) records what was stamped, so a file is only
stamped again after it has been replaced by a new, unstamped screenshot."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from framework.brand_logo import LOGO_BG, LOGO_DATA_URI, LOGO_H, LOGO_W  # noqa: E402

PAGE = """<!doctype html><html><body style="margin:0;padding:0;background:#fff">
<div style="position:relative;width:{w}px;height:{h}px;overflow:hidden">
<img src="{src}" style="display:block;width:{w}px;height:{h}px">
{cover}<div style="position:absolute;right:8px;bottom:6px;padding:4px 6px;border-radius:4px;line-height:0;background:{bg}"><img id="logo" src="{logo}" style="display:block;width:{lw}px;height:{lh}px"></div>
</div></body></html>"""


# one-off: images stamped by the first (text) version of the mark -- hide it with the flat area just to its left
COVER = ('<div style="position:absolute;right:0;bottom:0;width:180px;height:30px;background:url({src}) '
         '{x}px {y}px no-repeat"></div>')


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path.name} is not a PNG")
    return struct.unpack(">II", head[16:24])


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(folder: str = "docs/images", cover_legacy: bool = False) -> int:
    root = Path(folder)
    manifest_path = root / ".stamped.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    if cover_legacy:
        manifest = {}
    todo = [p for p in sorted(root.glob("*.png")) if manifest.get(p.name) != sha(p)]
    if not todo:
        print("all images already stamped")
        return 0
    with sync_playwright() as pw, tempfile.TemporaryDirectory() as tmp:
        browser = pw.chromium.launch()
        for png in todo:
            w, h = png_size(png)
            html = Path(tmp) / "stamp.html"
            html.write_text(PAGE.format(cover=COVER.format(src=png.resolve().as_uri(), x=-(w - 360), y=-(h - 30)) if cover_legacy else "", w=w, h=h, src=png.resolve().as_uri(), bg=LOGO_BG, logo=LOGO_DATA_URI, lw=LOGO_W * 6 // 10, lh=LOGO_H * 6 // 10), encoding="utf-8")
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            page.goto(html.as_uri())
            page.wait_for_function("Array.from(document.images).every(i => i.complete)")
            data = page.screenshot(clip={"x": 0, "y": 0, "width": w, "height": h})
            page.close()
            png.write_bytes(data)
            manifest[png.name] = sha(png)
            print(f"stamped {png.name} ({w}x{h})")
        browser.close()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raise SystemExit(main(*args[:1], cover_legacy="--cover-legacy" in sys.argv))
