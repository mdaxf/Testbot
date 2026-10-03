from __future__ import annotations

from playwright.sync_api import Locator, Page

from framework.models import Target


def build_semantic_locator(page: Page, target: Target) -> Locator:
    """Priority mirrors how a person reads a page: role/name, then label, then
    placeholder/text, then a test-id, then CSS/XPath as an explicit escape hatch.

    `target.nth` (1-based) picks a specific match out of several otherwise-identical
    elements generically -- "the 1st item in the list" -- without needing a unique
    label. Works for click/type/capture/assert alike, since they all resolve a Target
    the same way.

    `target.scope` (a CSS selector) narrows the search root before applying the
    strategy -- disambiguates e.g. two tables with identical-looking rows, or two
    forms with the same button label. `target.frame` first enters an <iframe>, and
    `target.scope_if_present` narrows to a container only while it exists (open popup).
    """
    strategy = target.strategy
    value = target.value
    root = page.frame_locator(target.frame) if target.frame else page
    if target.scope_if_present and root.locator(target.scope_if_present).count() > 0:
        root = root.locator(target.scope_if_present)
    if target.scope:
        root = root.locator(target.scope)

    if strategy == "role":
        locator = root.get_by_role(value, name=target.name) if target.name else root.get_by_role(value)
    elif strategy == "label":
        locator = root.get_by_label(value)
    elif strategy == "placeholder":
        locator = root.get_by_placeholder(value)
    elif strategy == "text":
        locator = root.get_by_text(value)
    elif strategy == "testid":
        locator = root.get_by_test_id(value)
    elif strategy == "css":
        locator = root.locator(value)
    elif strategy == "xpath":
        locator = root.locator(f"xpath={value}")
    elif strategy == "row_containing":
        # value = text the row must contain -- "select the row for John Smith" without
        # knowing which column it's in or writing a hand-rolled XPath ancestor lookup.
        # Scope to a specific table when the page has more than one, or rows with
        # matching text in different tables will make this ambiguous.
        locator = root.locator("tr").filter(has_text=value)
    elif strategy == "table_cell":
        # value = a locator for the table itself (e.g. "#orders" or "table"); row/col are
        # 1-based. Scoped to "tbody tr" so header rows (in a <thead>) don't shift indices --
        # every HTML table has an implicit <tbody> even when not written explicitly.
        if target.row is None or target.col is None:
            raise ValueError("strategy 'table_cell' requires target.row and target.col (1-based)")
        row = root.locator(value).locator("tbody tr").nth(target.row - 1)
        locator = row.locator("td, th").nth(target.col - 1)
    else:
        raise ValueError(f"'{strategy}' is not a semantic locator strategy")

    if target.nth is not None:
        if target.nth < 1:
            raise ValueError(f"Target.nth must be a positive integer (1 = first item), got {target.nth}")
        locator = locator.nth(target.nth - 1)

    return locator
