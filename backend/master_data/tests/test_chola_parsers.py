"""
Tests for the shared Chola xlsx parsers (``chola_parsers.py``).

Builds small in-memory workbooks with openpyxl rather than depending on the
committed source files, so these tests exercise the parsing RULES (repeated
headers, side-block columns located by label, merged-cell-style carry-forward,
zone-slug normalization) independently of the specific Chola data.

Run from the backend container:
    python manage.py test master_data.tests.test_chola_parsers -v 2
"""

import unittest

import openpyxl

from master_data.services.chola_parsers import (
    parse_chola_product_hierarchy,
    parse_chola_rto_zone,
)


def _wb(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    return wb


class ParseCholaRtoZoneTests(unittest.TestCase):
    def test_main_block_zone_mapping(self):
        wb = _wb(
            [
                ("RTO_CODE", "STATE", "UW ZONE", None, "UP EAST", "NCR"),
                ("MH01", "MAHARASHTRA", "MUMBAI", None, None, None),
                ("MH06", "MAHARASHTRA", "CENTRAL MH", None, None, None),
            ]
        )
        rows = parse_chola_rto_zone(wb)
        self.assertIn(("Chola", "mumbai", "MH01"), rows)
        self.assertIn(("Chola", "central-mh", "MH06"), rows)

    def test_repeated_header_row_skipped(self):
        wb = _wb(
            [
                ("RTO_CODE", "STATE", "UW ZONE", None, None, None),
                ("MH01", "MAHARASHTRA", "MUMBAI", None, None, None),
                ("RTO_CODE", "STATE", "UW ZONE", None, None, None),
                ("TN01", "TAMILNADU", "CHENNAI", None, None, None),
            ]
        )
        rows = parse_chola_rto_zone(wb)
        codes = {code for _, _, code in rows}
        self.assertEqual(codes, {"MH01", "TN01"})
        # The repeated header row itself must not be parsed as an RTO row.
        zones = {zone for _, zone, _ in rows}
        self.assertNotIn("rto-code", zones)

    def test_up_east_and_ncr_side_blocks_located_by_header_label(self):
        wb = _wb(
            [
                ("RTO_CODE", "STATE", "UW ZONE", None, "UP EAST", "NCR"),
                ("MH01", "MAHARASHTRA", "MUMBAI", None, "UP28", "HR26"),
                ("MH02", "MAHARASHTRA", "MUMBAI", None, "UP32", "UP14"),
                ("MH03", "MAHARASHTRA", "MUMBAI", None, None, None),
            ]
        )
        rows = parse_chola_rto_zone(wb)
        up_east_codes = {code for _, zone, code in rows if zone == "up-east"}
        ncr_codes = {code for _, zone, code in rows if zone == "ncr"}
        self.assertEqual(up_east_codes, {"UP28", "UP32"})
        self.assertEqual(ncr_codes, {"HR26", "UP14"})

    def test_ncr_zone_spans_multiple_states_worth_of_codes(self):
        # NCR mixes HR-prefixed and UP-prefixed codes — both must be captured
        # under the same "ncr" zone so the resolver can aggregate both states.
        wb = _wb(
            [
                ("RTO_CODE", "STATE", "UW ZONE", None, "UP EAST", "NCR"),
                ("MH01", "MAHARASHTRA", "MUMBAI", None, "UP28", "HR26"),
                ("MH02", "MAHARASHTRA", "MUMBAI", None, None, "UP14"),
            ]
        )
        rows = parse_chola_rto_zone(wb)
        ncr_codes = {code for _, zone, code in rows if zone == "ncr"}
        self.assertEqual(ncr_codes, {"HR26", "UP14"})

    def test_zone_slug_normalization(self):
        wb = _wb(
            [
                ("RTO_CODE", "STATE", "UW ZONE", None, None, None),
                ("MH12", "MAHARASHTRA", "PUNE", None, None, None),
                ("MH06", "MAHARASHTRA", "CENTRAL MH", None, None, None),
            ]
        )
        rows = parse_chola_rto_zone(wb)
        zones = {zone for _, zone, _ in rows}
        self.assertEqual(zones, {"pune", "central-mh"})

    def test_empty_sheet_returns_empty(self):
        wb = openpyxl.Workbook()
        self.assertEqual(parse_chola_rto_zone(wb), [])


class ParseCholaProductHierarchyTests(unittest.TestCase):
    def test_no_carry_forward_across_products_without_explicit_subproduct(self):
        wb = _wb(
            [
                ("Product", "SubProduct", "VehicleSubClass"),
                ("CV", "GCV", "Truck"),
                (None, None, "Tipper"),
                ("TW", None, "Bike"),  # new product, no sub-product -> resets
                (None, None, "Scooter"),
            ]
        )
        rows = parse_chola_product_hierarchy(wb)
        self.assertIn(("Chola", "CV", "GCV", "Truck"), rows)
        self.assertIn(("Chola", "CV", "GCV", "Tipper"), rows)
        self.assertIn(("Chola", "TW", "", "Bike"), rows)
        self.assertIn(("Chola", "TW", "", "Scooter"), rows)

    def test_subproduct_carry_forward_within_same_product(self):
        wb = _wb(
            [
                ("Product", "SubProduct", "VehicleSubClass"),
                ("CV", "MISC-D", "Crane"),
                (None, None, "Loader"),
                (None, None, "Harvester"),
            ]
        )
        rows = parse_chola_product_hierarchy(wb)
        subproducts = {sp for _, _, sp, _ in rows}
        self.assertEqual(subproducts, {"MISC-D"})
        self.assertEqual(len(rows), 3)

    def test_explicit_product_repeat_with_new_subproduct(self):
        wb = _wb(
            [
                ("Product", "SubProduct", "VehicleSubClass"),
                ("CV", "GCV", "Truck"),
                ("CV", "PCV", "Taxi"),
            ]
        )
        rows = parse_chola_product_hierarchy(wb)
        self.assertIn(("Chola", "CV", "GCV", "Truck"), rows)
        self.assertIn(("Chola", "CV", "PCV", "Taxi"), rows)

    def test_repeated_header_row_skipped(self):
        wb = _wb(
            [
                ("Product", "SubProduct", "VehicleSubClass"),
                ("Car", None, "PrivateCar"),
                ("Product", "SubProduct", "VehicleSubClass"),
                ("TW", None, "Bike"),
            ]
        )
        rows = parse_chola_product_hierarchy(wb)
        self.assertEqual(len(rows), 2)

    def test_row_before_any_product_is_dropped(self):
        wb = _wb(
            [
                ("Product", "SubProduct", "VehicleSubClass"),
                (None, None, "Orphan"),
                ("Car", None, "PrivateCar"),
            ]
        )
        rows = parse_chola_product_hierarchy(wb)
        subclasses = {sc for _, _, _, sc in rows}
        self.assertNotIn("Orphan", subclasses)
        self.assertIn("PrivateCar", subclasses)
