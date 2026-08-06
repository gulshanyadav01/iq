"""Shared low-level helpers for carrier-scoped xlsx parsers.

Carrier-agnostic: ``chola_parsers.py`` and ``icici_parsers.py`` both build on
these rather than each re-implementing workbook-opening / cell-cleaning /
zone-slug normalization.
"""

from __future__ import annotations

import re
from typing import Any

import openpyxl


def open_workbook(wb_or_path) -> tuple[openpyxl.Workbook, bool]:
    """Return (workbook, opened_here). Accepts a Workbook, a Path, or a str."""
    if isinstance(wb_or_path, openpyxl.Workbook):
        return wb_or_path, False
    wb = openpyxl.load_workbook(wb_or_path, data_only=True)
    return wb, True


def clean_cell(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def zone_slug(raw: str) -> str:
    """Zone slug: upper -> strip -> collapse-ws -> lower -> spaces-to-hyphens.

    ``"MUMBAI "`` -> ``"mumbai"``; ``"CENTRAL MH"`` -> ``"central-mh"``;
    ``"UP EAST"`` -> ``"up-east"``; ``"UP-EAST"`` -> ``"up-east"``.
    """
    s = re.sub(r"\s+", " ", raw.upper().strip())
    return s.lower().replace(" ", "-")
