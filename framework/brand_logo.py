"""The IACF logo (white, transparent background), hardcoded so it ships inside every program and cannot be swapped by replacing a file.
Used as the watermark on screenshots and in the manager. Drawn on the IACF blue pill (LOGO_BG) because the logo itself is white."""
from __future__ import annotations

LOGO_BG = "rgba(0,83,134,.85)"
LOGO_W, LOGO_H = 65, 44
LOGO_PNG_B64 = (
    "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"
)
LOGO_DATA_URI = "data:image/png;base64," + LOGO_PNG_B64
