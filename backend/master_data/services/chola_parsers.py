"""Shared xlsx parsers for Chola's carrier-scoped masters.

Used by BOTH the committed-CSV generation (one-time, run manually against the
source workbook to produce ``data/chola_rto_zone.csv`` /
``data/chola_product_hierarchy.csv``) and the carrier-scoped upload API
(``views/upload.py``), so xlsx parsing is a single tested code path rather
than a throwaway script duplicated at the upload boundary.

Both parsers accept either an already-open ``openpyxl.Workbook`` or a path,
and read only the first worksheet (both source files are single-sheet).
"""

from __future__ import annotations

from master_data.services.xlsx_parsing import clean_cell, open_workbook, zone_slug


def parse_chola_rto_zone(wb_or_path) -> list[tuple[str, str, str]]:
    """Parse the Chola RTO Master workbook into ``(carrier, zone, rto_code)`` rows.

    The sheet has a main RTO_CODE/STATE/UW-ZONE block (one row per RTO,
    repeated-header-row tolerant — the source restarts the header for the
    Tamil Nadu block) plus two side blocks in later columns (``UP EAST`` /
    ``NCR``) that list RTO codes belonging to those zones independently of
    the row's own RTO_CODE/STATE/ZONE triple. Side-block columns are located
    by header label, not a hardcoded column letter, so a reordered sheet
    still parses correctly.
    """
    wb, opened = open_workbook(wb_or_path)
    try:
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        if opened:
            wb.close()

    if not rows:
        return []

    header = rows[0]
    side_cols: dict[int, str] = {}
    for idx, cell in enumerate(header):
        label = clean_cell(cell).upper()
        if label in ("UP EAST", "UPEAST", "UP-EAST"):
            side_cols[idx] = "up-east"
        elif label == "NCR":
            side_cols[idx] = "ncr"

    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(slug: str, code: str) -> None:
        key = ("Chola", slug, code.upper())
        if key not in seen:
            seen.add(key)
            out.append(key)

    for row in rows[1:]:
        if row is None or all(c is None for c in row):
            continue
        code_cell = clean_cell(row[0]) if len(row) > 0 else ""
        if code_cell.upper() == "RTO_CODE":
            continue  # repeated header row (e.g. the Tamil Nadu block)
        zone_cell = clean_cell(row[2]) if len(row) > 2 else ""
        if code_cell and zone_cell:
            _add(zone_slug(zone_cell), code_cell)
        for idx, slug in side_cols.items():
            if idx >= len(row):
                continue
            val = clean_cell(row[idx])
            if val:
                _add(slug, val)

    return out


def parse_chola_product_hierarchy(
    wb_or_path,
) -> list[tuple[str, str, str, str]]:
    """Parse the Chola Product/SubProduct/VehicleSubClass workbook into
    ``(carrier, product, sub_product, vehicle_subclass)`` rows.

    Product and SubProduct cells carry forward from the last explicit value
    (merged-cell-style layout — most rows only set VehicleSubClass). A new
    explicit Product value resets the carried-forward SubProduct unless the
    same row also sets one, so a product change without an explicit
    sub-product (e.g. ``Car``, ``TW``) does not inherit the prior product's
    sub-product.
    """
    wb, opened = open_workbook(wb_or_path)
    try:
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        if opened:
            wb.close()

    out: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    last_product = ""
    last_sub_product = ""

    for row in rows[1:] if rows else []:
        if row is None or all(c is None for c in row):
            continue
        product_cell = clean_cell(row[0]) if len(row) > 0 else ""
        sub_product_cell = clean_cell(row[1]) if len(row) > 1 else ""
        subclass_cell = clean_cell(row[2]) if len(row) > 2 else ""

        if product_cell.upper() == "PRODUCT":
            continue  # repeated header row

        if product_cell:
            last_product = product_cell
            last_sub_product = sub_product_cell
        elif sub_product_cell:
            last_sub_product = sub_product_cell

        if not subclass_cell or not last_product:
            continue

        key = ("Chola", last_product, last_sub_product, subclass_cell)
        if key not in seen:
            seen.add(key)
            out.append(key)

    return out
