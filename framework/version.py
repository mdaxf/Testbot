"""The ONE place for the product version and the IACF ownership notice.
Everything reads from here: the .exe file properties (specs), `--version`, the manager footer/About, the recorder title,
the HTML report footer, the Python wheel and the watermark on screenshots. To release a new version change __version__ only."""
__version__ = "0.1.1.1"

COMPANY = "IACF"
YEAR = "2026"
PRODUCT = "testbot"
COPYRIGHT = f"© {YEAR} {COMPANY} — All rights reserved"
WATERMARK = f"© {COMPANY} · {PRODUCT} {__version__}"     # the small label stamped on screenshots


def version_tuple() -> tuple[int, int, int, int]:
    parts = [int(p) for p in __version__.split(".")[:4]]
    return tuple(parts + [0] * (4 - len(parts)))  # type: ignore[return-value]


def banner(program: str) -> str:
    return f"{program} {__version__} ({COPYRIGHT})"
