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

import re

from master_data.services.mmv import mmv_slug
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


# --- Make / Model / Variant -------------------------------------------------

# The two-wheeler sheet spells fuel as a single dirty letter with a trailing
# space ("P ", "B ", "C ", "p "). Mapped onto the CV book's vocabulary
# (Petrol/Diesel/Electric/CNG/LPG) so one fuel column serves all three
# products.
_TW_FUEL = {"p": "petrol", "d": "diesel", "b": "electric", "c": "cng", "l": "lpg"}

# Committed-CSV column order.
#
# Two groups of source columns are deliberately absent. The ``*_slug`` fields
# the parser also produces are a pure function of make/model/variant via
# ``mmv_slug``, so persisting them would let the CSV drift from the code the
# matcher actually uses — the loader recomputes them on read. The numeric
# specs (CC, kW, gross vehicle weight, seating capacity, wheels, ex-showroom
# price) are dropped outright: canonicalization and validation key on make and
# model only, and carrying ~22k rows of unread numbers cost roughly a third of
# the committed file. Re-add them here and on the model if something ever
# needs to price or filter on them.
_MMV_COLUMNS = (
    "carrier",
    "product",
    "make",
    "model",
    "variant",
    "fuel",
    "sub_class",
    "vehicle_class",
)


def _header_index(header_row) -> dict[str, int]:
    """Map a normalized header label to its column index.

    Labels are matched case- and space-insensitively so the sources' mixed
    spellings (``MAKEID`` vs ``makeid``, ``MakeName``) resolve to one key, and
    columns are located by label rather than a hardcoded position — a
    reordered sheet still parses.
    """
    out: dict[str, int] = {}
    for idx, cell in enumerate(header_row or ()):
        label = clean_cell(cell).lower().replace(" ", "")
        if label and label not in out:
            out[label] = idx
    return out


# Excel stores a hard line break inside a cell as a carriage return, which
# openpyxl surfaces as the literal escape ``_x000D_`` followed by a newline.
# 33 CV manufacturer names carry one ("Asha Tilak Ventures Pvt_x000D_\nLtd").
# Left alone it is not cosmetic: the escape survives slugging as a bare
# ``x000d`` token, so the grid's ordinary spelling of that make would never
# match the master row, AND the artifact would be written back into the
# exported VehicleMake cell as the "canonical" spelling.
# Stripped in two passes, not one alternation: the escape and the newline it
# precedes are adjacent, so a single pass replacing both with a space leaves a
# doubled space in the middle of the name.
_CELL_ARTIFACT_RE = re.compile(r"_x000[Dd]_")
_WS_RE = re.compile(r"\s+")


def _cell(row, cols: dict[str, int], label: str) -> str:
    idx = cols.get(label)
    if idx is None or idx >= len(row):
        return ""
    text = _CELL_ARTIFACT_RE.sub(" ", clean_cell(row[idx]))
    return _WS_RE.sub(" ", text).strip()


def _mmv_row(product, make, model, variant, **extras) -> dict:
    row = {
        "carrier": "Chola",
        "product": product,
        "make": make,
        "model": model,
        "variant": variant,
        "make_slug": mmv_slug(make),
        "model_slug": mmv_slug(model),
        "variant_slug": mmv_slug(variant),
        "fuel": "",
        "sub_class": "",
        "vehicle_class": "",
    }
    row.update(extras)
    return row


def _dedupe_mmv(rows) -> list[dict]:
    """Collapse rows onto the natural key, keeping the FIRST occurrence.

    Neither source sheet is distinct on (make, model, variant) — the CV book
    has 21,862 rows for 18,527 combinations and the four-wheeler sheet repeats
    each of its 614 pairs about thirteen times. Colliding rows differ only in
    descriptive extras (CC, GVW, fuel, price), so first-seen wins; the loader
    reports the rest as skipped.
    """
    seen: set[tuple[str, str, str, str, str]] = set()
    out: list[dict] = []
    for row in rows:
        if not row["make_slug"] or not row["model_slug"]:
            continue  # a row naming no vehicle cannot be matched against
        key = (
            row["carrier"],
            row["product"],
            row["make_slug"],
            row["model_slug"],
            row["variant_slug"],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _parse_mmv_tw(ws) -> list[dict]:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    cols = _header_index(rows[0])
    out = []
    for row in rows[1:]:
        if row is None or all(c is None for c in row):
            continue
        fuel_raw = _cell(row, cols, "fueltype").strip().lower()
        out.append(
            _mmv_row(
                "TW",
                _cell(row, cols, "makename"),
                _cell(row, cols, "modelname"),
                _cell(row, cols, "variantname"),
                fuel=_TW_FUEL.get(fuel_raw[:1], ""),
                # Case-folded: the source writes BIKE / Bike / bike and
                # SCOOTER / Scooter / scooter for the same two segments.
                sub_class=_cell(row, cols, "segmenttype").lower(),
            )
        )
    return out


def _parse_mmv_fw(ws) -> list[dict]:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    cols = _header_index(rows[0])
    out = []
    for row in rows[1:]:
        if row is None or all(c is None for c in row):
            continue
        # No variant column on this sheet — Private Car seeds at make/model
        # grain, which is all the canonicalization and validation need.
        out.append(
            _mmv_row(
                "Car",
                _cell(row, cols, "makename"),
                _cell(row, cols, "modelname"),
                "",
            )
        )
    return out


def _parse_mmv_cv(ws) -> list[dict]:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    cols = _header_index(rows[0])
    out = []
    for row in rows[1:]:
        if row is None or all(c is None for c in row):
            continue
        out.append(
            _mmv_row(
                "CV",
                _cell(row, cols, "manufacturername"),
                _cell(row, cols, "modelname"),
                _cell(row, cols, "variantname"),
                fuel=_cell(row, cols, "fuel").lower(),
                # Kept VERBATIM, including the source's own typos
                # ("Excavaters", "Tranist Mixer") — the same rule
                # CarrierProductHierarchyMaster.vehicle_subclass follows, so a
                # grid spelling the same typo still matches.
                sub_class=_cell(row, cols, "subclassname"),
                vehicle_class=_cell(row, cols, "vehicleclass"),
            )
        )
    return out


def parse_chola_mmv(wb_or_path) -> list[dict]:
    """Parse a Chola MMV workbook into ``CarrierVehicleMMVMaster`` row dicts.

    Dispatches on sheet name so ONE entry point accepts either book: the
    two-and-four-wheeler workbook carries ``TW`` and ``FW`` sheets, the
    commercial-vehicle workbook a single generically-named sheet. A sheet
    matching neither pattern is parsed with the CV reader only when it is the
    workbook's sole sheet; otherwise it is ignored rather than guessed at.

    Rows are deduped on the natural key (see ``_dedupe_mmv``) so the caller
    never has to.
    """
    wb, opened = open_workbook(wb_or_path)
    try:
        rows: list[dict] = []
        names = list(wb.sheetnames)
        for name in names:
            key = name.strip().lower()
            if key == "tw":
                rows.extend(_parse_mmv_tw(wb[name]))
            elif key == "fw":
                rows.extend(_parse_mmv_fw(wb[name]))
            elif len(names) == 1:
                rows.extend(_parse_mmv_cv(wb[name]))
    finally:
        if opened:
            wb.close()

    return _dedupe_mmv(rows)
