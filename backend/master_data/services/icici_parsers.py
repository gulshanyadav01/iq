"""Shared xlsx parser for ICICI's carrier-scoped RTO-grid master.

Used by BOTH the committed-CSV generation (one-time, run manually against the
source workbook to produce ``data/icici_rto_grid.csv``) and the carrier-scoped
upload API (``views/upload.py``), so xlsx parsing is a single tested code
path rather than a throwaway script duplicated at the upload boundary.

ICICI's source (``RTO_CODE, RTO_STATE, RTO_CITY, RTO category, GRID MAPPING,
Remarks, GRID MAPPING working``) is structurally simpler than Chola's: one
row per RTO_CODE with its own ``GRID MAPPING`` label directly (no side-block
columns) — a state name ("ANDHRA PRADESH"), a city/vertical grouping
("HYDERABAD"), or a zone ("NCR", "UP-EAST", "UP-WEST"). Columns are located
by header label, not a hardcoded index, so a reordered sheet still parses.

Sheet2 of the source (city-name -> GRID MAPPING overrides for RTOs without an
allotted code yet) is NOT parsed here — it has no RTO_CODE, so it doesn't fit
the ``(carrier, zone, rto_code)`` shape ``CarrierRTOZoneMaster`` expects.
"""

from __future__ import annotations

from master_data.services.xlsx_parsing import clean_cell, open_workbook, zone_slug

_RTO_CODE_HEADER = "RTO_CODE"
_GRID_MAPPING_HEADER = "GRID MAPPING"


def parse_icici_rto_grid(wb_or_path) -> list[tuple[str, str, str]]:
    """Parse the ICICI RTO Master workbook into ``(carrier, zone, rto_code)``
    rows, reading only the first worksheet ("Sheet1" in the source)."""
    wb, opened = open_workbook(wb_or_path)
    try:
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        if opened:
            wb.close()

    if not rows:
        return []

    header = [clean_cell(c).upper() for c in rows[0]]
    try:
        code_idx = header.index(_RTO_CODE_HEADER)
        grid_idx = header.index(_GRID_MAPPING_HEADER)
    except ValueError:
        return []  # header row missing an expected column — nothing to parse

    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for row in rows[1:]:
        if row is None or all(c is None for c in row):
            continue
        code_cell = clean_cell(row[code_idx]) if code_idx < len(row) else ""
        if code_cell.upper() == _RTO_CODE_HEADER:
            continue  # repeated header row
        grid_cell = clean_cell(row[grid_idx]) if grid_idx < len(row) else ""
        if not code_cell or not grid_cell:
            continue
        key = ("ICICI", zone_slug(grid_cell), code_cell.upper())
        if key not in seen:
            seen.add(key)
            out.append(key)

    return out
