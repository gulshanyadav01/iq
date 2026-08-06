"""
Tests for the ICICI RTO-grid xlsx parser (``icici_parsers.py``).

Builds small in-memory workbooks with openpyxl rather than depending on the
committed source file, so these tests exercise the parsing RULES (header
located by label, repeated-header skip, zone-slug normalization) independently
of the specific ICICI data.

Run from the backend container:
    python manage.py test master_data.tests.test_icici_parsers -v 2
"""

import unittest

import openpyxl

from master_data.services.icici_parsers import parse_icici_rto_grid


def _wb(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    return wb


class ParseIciciRtoGridTests(unittest.TestCase):
    def test_basic_mapping(self):
        wb = _wb(
            [
                (
                    "RTO_CODE",
                    "RTO_STATE",
                    "RTO_CITY",
                    "RTO category",
                    "GRID MAPPING",
                    "Remarks",
                    "GRID MAPPING working",
                ),
                ("AP01", "TELANGANA", "ADILABAD", "GEOGRAPHY", "TELANGANA", None, None),
                ("AP09", "TELANGANA", "HYDERABAD", "VERTICAL", "HYDERABAD", None, None),
            ]
        )
        rows = parse_icici_rto_grid(wb)
        self.assertIn(("ICICI", "telangana", "AP01"), rows)
        self.assertIn(("ICICI", "hyderabad", "AP09"), rows)

    def test_columns_located_by_header_label_not_position(self):
        # GRID MAPPING before RTO_CODE, plus an extra leading column — parser
        # must not assume fixed column indices.
        wb = _wb(
            [
                ("Extra", "GRID MAPPING", "RTO_CODE"),
                ("x", "NCR", "HR26"),
            ]
        )
        rows = parse_icici_rto_grid(wb)
        self.assertEqual(rows, [("ICICI", "ncr", "HR26")])

    def test_grid_mapping_working_column_not_confused_with_grid_mapping(self):
        wb = _wb(
            [
                (
                    "RTO_CODE",
                    "RTO_STATE",
                    "RTO_CITY",
                    "RTO category",
                    "GRID MAPPING",
                    "Remarks",
                    "GRID MAPPING working",
                ),
                (
                    "AP01",
                    "TELANGANA",
                    "ADILABAD",
                    "GEOGRAPHY",
                    "TELANGANA",
                    None,
                    "SOMETHING ELSE",
                ),
            ]
        )
        rows = parse_icici_rto_grid(wb)
        self.assertEqual(rows, [("ICICI", "telangana", "AP01")])

    def test_repeated_header_row_skipped(self):
        wb = _wb(
            [
                ("RTO_CODE", "RTO_STATE", "RTO_CITY", "RTO category", "GRID MAPPING"),
                ("AP01", "TELANGANA", "ADILABAD", "GEOGRAPHY", "TELANGANA"),
                ("RTO_CODE", "RTO_STATE", "RTO_CITY", "RTO category", "GRID MAPPING"),
                ("TN01", "TAMIL NADU", "CHENNAI", "GEOGRAPHY", "TAMIL NADU"),
            ]
        )
        rows = parse_icici_rto_grid(wb)
        codes = {code for _, _, code in rows}
        self.assertEqual(codes, {"AP01", "TN01"})

    def test_hyphenated_zone_label_normalizes(self):
        wb = _wb(
            [
                ("RTO_CODE", "GRID MAPPING"),
                ("UP28", "UP-EAST"),
                ("UP99", "UP-WEST"),
            ]
        )
        rows = parse_icici_rto_grid(wb)
        zones = {zone for _, zone, _ in rows}
        self.assertEqual(zones, {"up-east", "up-west"})

    def test_duplicate_code_same_zone_deduped(self):
        wb = _wb(
            [
                ("RTO_CODE", "RTO_CITY", "GRID MAPPING"),
                ("AP05", "AMALAPURAM", "ANDHRA PRADESH"),
                ("AP05", "EAST GODAVARI", "ANDHRA PRADESH"),
            ]
        )
        rows = parse_icici_rto_grid(wb)
        self.assertEqual(rows, [("ICICI", "andhra-pradesh", "AP05")])

    def test_missing_expected_header_returns_empty(self):
        wb = _wb([("Foo", "Bar")])
        self.assertEqual(parse_icici_rto_grid(wb), [])

    def test_row_missing_code_or_grid_mapping_dropped(self):
        wb = _wb(
            [
                ("RTO_CODE", "GRID MAPPING"),
                (None, "TELANGANA"),
                ("AP01", None),
                ("AP02", "ANDHRA PRADESH"),
            ]
        )
        rows = parse_icici_rto_grid(wb)
        self.assertEqual(rows, [("ICICI", "andhra-pradesh", "AP02")])

    def test_empty_sheet_returns_empty(self):
        wb = openpyxl.Workbook()
        self.assertEqual(parse_icici_rto_grid(wb), [])
