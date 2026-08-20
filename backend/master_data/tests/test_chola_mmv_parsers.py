"""
Tests for the Chola Make/Model/Variant parser (``parse_chola_mmv``) and the
slug/matcher it feeds (``master_data.services.mmv``).

Builds small in-memory workbooks rather than depending on the committed source
files, so these exercise the parsing RULES (sheet dispatch, header-label column
lookup, dirty fuel letters, mixed-case segments, dedupe on the natural key)
independently of the specific Chola data.

Run from the backend container:
    python manage.py test master_data.tests.test_chola_mmv_parsers -v 2
"""

import unittest
from unittest import mock

import openpyxl

from master_data.services.chola_parsers import parse_chola_mmv
from master_data.services.mmv import (
    match_make,
    match_model,
    mmv_slug,
)

TW_HEADER = (
    "VariantId",
    "MakeName",
    "makeid",
    "modelname",
    "ModelId",
    "variantname",
    "CubicCapacity",
    "KiloWatt",
    "fueltype",
    "segmenttype",
)
FW_HEADER = ("MAKEID", "MakeName", "modelname", "ModelId")
CV_HEADER = (
    "PBVehicleId",
    "ManufacturerName",
    "ModelName",
    "Variantname",
    "GrossVehicleWeight",
    "SeatingCapacity",
    "SubClassName",
    "VehicleClass",
    "ExShowroomPrice",
    "Fuel",
    "Wheels",
    "CubicCapacity",
)


def _two_and_four_wheeler_wb(tw_rows, fw_rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "TW"
    ws.append(TW_HEADER)
    for row in tw_rows:
        ws.append(row)
    fw = wb.create_sheet("FW")
    fw.append(FW_HEADER)
    for row in fw_rows:
        fw.append(row)
    return wb


def _cv_wb(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(CV_HEADER)
    for row in rows:
        ws.append(row)
    return wb


class ParseCholaMMVTwoAndFourWheelerTests(unittest.TestCase):
    def test_tw_and_fw_sheets_both_parse_into_their_products(self):
        wb = _two_and_four_wheeler_wb(
            tw_rows=[
                (1, "BAJAJ", 6, "AVENGER", 10, "STD (180 CC)", 180, None, "P ", "BIKE")
            ],
            fw_rows=[(1, "MARUTI", "SWIFT", 20)],
        )
        rows = parse_chola_mmv(wb)
        by_product = {r["product"]: r for r in rows}
        self.assertEqual(set(by_product), {"TW", "Car"})

        tw = by_product["TW"]
        self.assertEqual(tw["carrier"], "Chola")
        self.assertEqual(
            (tw["make"], tw["model"], tw["variant"]),
            ("BAJAJ", "AVENGER", "STD (180 CC)"),
        )
        self.assertEqual(tw["sub_class"], "bike")

        car = by_product["Car"]
        self.assertEqual((car["make"], car["model"]), ("MARUTI", "SWIFT"))
        # The four-wheeler sheet carries no variant column; Car seeds at
        # make/model grain with a blank variant.
        self.assertEqual(car["variant"], "")
        self.assertEqual(car["variant_slug"], "")

    def test_dirty_fuel_letters_normalize_onto_the_cv_vocabulary(self):
        wb = _two_and_four_wheeler_wb(
            tw_rows=[
                (1, "A", 1, "M1", 1, "V", 100, None, "P ", "BIKE"),
                (2, "B", 2, "M2", 2, "V", 100, None, "p ", "Bike"),
                (3, "C", 3, "M3", 3, "V", None, 2, "B ", "SCOOTER"),
                (4, "D", 4, "M4", 4, "V", 100, None, "C ", "scooter"),
            ],
            fw_rows=[],
        )
        rows = {r["make"]: r for r in parse_chola_mmv(wb)}
        self.assertEqual(rows["A"]["fuel"], "petrol")
        self.assertEqual(rows["B"]["fuel"], "petrol")
        self.assertEqual(rows["C"]["fuel"], "electric")
        self.assertEqual(rows["D"]["fuel"], "cng")

    def test_segment_case_is_folded(self):
        wb = _two_and_four_wheeler_wb(
            tw_rows=[
                (1, "A", 1, "M1", 1, "V", 100, None, "P ", "BIKE"),
                (2, "B", 2, "M2", 2, "V", 100, None, "P ", "Bike"),
                (3, "C", 3, "M3", 3, "V", 100, None, "P ", "SCOOTER"),
            ],
            fw_rows=[],
        )
        self.assertEqual(
            {r["sub_class"] for r in parse_chola_mmv(wb)}, {"bike", "scooter"}
        )

    def test_fw_duplicate_rows_collapse(self):
        """The real four-wheeler sheet repeats each pair ~13x."""
        wb = _two_and_four_wheeler_wb(
            tw_rows=[],
            fw_rows=[(29, "MAHINDRA AND MAHINDRA", "MARSHAL", 186)] * 13
            + [(45, "TATA", "SIERA", 355)] * 4,
        )
        rows = parse_chola_mmv(wb)
        self.assertEqual(len(rows), 2)

    def test_rows_naming_no_vehicle_are_dropped(self):
        wb = _two_and_four_wheeler_wb(
            tw_rows=[
                (1, "", 1, "MODEL", 1, "V", 100, None, "P ", "BIKE"),
                (2, "MAKE", 2, "", 2, "V", 100, None, "P ", "BIKE"),
            ],
            fw_rows=[],
        )
        self.assertEqual(parse_chola_mmv(wb), [])

    def test_excel_carriage_return_artifact_is_stripped(self):
        """openpyxl surfaces an in-cell line break as a literal ``_x000D_``
        plus a newline. Left in, it survives slugging as a bare ``x000d``
        token — so the grid's ordinary spelling would never match this row —
        and gets written back into the export as the canonical spelling."""
        wb = _two_and_four_wheeler_wb(
            tw_rows=[],
            fw_rows=[(1, "Asha Tilak Ventures Pvt_x000D_\nLtd", "ARG", 2)],
        )
        row = parse_chola_mmv(wb)[0]
        self.assertEqual(row["make"], "Asha Tilak Ventures Pvt Ltd")
        self.assertEqual(row["make_slug"], "asha tilak ventures")

    def test_columns_are_found_by_label_not_position(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "TW"
        ws.append(("segmenttype", "variantname", "MakeName", "modelname", "fueltype"))
        ws.append(("BIKE", "STD", "BAJAJ", "AVENGER", "P "))
        rows = parse_chola_mmv(wb)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["make"], "BAJAJ")
        self.assertEqual(rows[0]["variant"], "STD")


class ParseCholaMMVCommercialTests(unittest.TestCase):
    def test_cv_sheet_parses_the_retained_columns(self):
        wb = _cv_wb(
            [
                (
                    1,
                    "EICHER MOTORS",
                    "10.59",
                    "GVW 7200",
                    7200,
                    3,
                    "Refrigerated Van",
                    "GCV",
                    650000,
                    "Diesel",
                    6,
                    2720,
                )
            ]
        )
        rows = parse_chola_mmv(wb)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["product"], "CV")
        self.assertEqual(row["make"], "EICHER MOTORS")
        self.assertEqual(row["model"], "10.59")
        self.assertEqual(row["variant"], "GVW 7200")
        self.assertEqual(row["sub_class"], "Refrigerated Van")
        self.assertEqual(row["vehicle_class"], "GCV")
        self.assertEqual(row["fuel"], "diesel")

    def test_numeric_specs_are_not_carried(self):
        """GVW / seating / wheels / CC / price are deliberately dropped —
        canonicalization and validation key on make and model alone, and
        ~22k rows of unread numbers cost real weight in the committed CSV."""
        wb = _cv_wb(
            [
                (
                    1,
                    "EICHER MOTORS",
                    "10.59",
                    "GVW 7200",
                    7200,
                    3,
                    "Refrigerated Van",
                    "GCV",
                    650000,
                    "Diesel",
                    6,
                    2720,
                )
            ]
        )
        # Assert the EXACT key set, not just the absence of the dropped names
        # — a bare assertNotIn cannot fail, since _mmv_row builds a fixed dict
        # and no parser passes those names. This version also catches a column
        # accidentally added later.
        self.assertEqual(
            set(parse_chola_mmv(wb)[0]),
            {
                "carrier",
                "product",
                "make",
                "model",
                "variant",
                "make_slug",
                "model_slug",
                "variant_slug",
                "fuel",
                "sub_class",
                "vehicle_class",
            },
        )

    def test_a_multi_sheet_workbook_naming_no_known_sheet_yields_nothing(self):
        """The CV reader only runs for a SOLE unnamed sheet, so a book with
        two unrecognised sheets parses to nothing rather than guessing which
        one holds vehicles."""
        wb = openpyxl.Workbook()
        first = wb.active
        first.title = "Notes"
        first.append(CV_HEADER)
        first.append(
            (1, "TATA", "407", "STD", 5000, 3, "Truck", "GCV", 1, "Diesel", 4, 2200)
        )
        second = wb.create_sheet("Scratch")
        second.append(CV_HEADER)
        self.assertEqual(parse_chola_mmv(wb), [])

    def test_source_subclass_typos_are_kept_verbatim(self):
        """Same rule CarrierProductHierarchyMaster.vehicle_subclass follows —
        a grid spelling the same typo has to be able to match."""
        wb = _cv_wb(
            [
                (
                    1,
                    "JCB",
                    "3DX",
                    "STD",
                    1000,
                    1,
                    "Excavaters",
                    "MISC-D",
                    1,
                    "Diesel",
                    4,
                    100,
                ),
                (
                    2,
                    "SCHWING",
                    "AM",
                    "STD",
                    1000,
                    1,
                    "Tranist Mixer",
                    "GCV",
                    1,
                    "Diesel",
                    6,
                    100,
                ),
            ]
        )
        self.assertEqual(
            {r["sub_class"] for r in parse_chola_mmv(wb)},
            {"Excavaters", "Tranist Mixer"},
        )

    def test_rows_colliding_on_the_natural_key_keep_the_first(self):
        """The CV book is not distinct on (make, model, variant) — colliding
        rows differ only in descriptive extras."""
        wb = _cv_wb(
            [
                (
                    1,
                    "TATA",
                    "407",
                    "STD",
                    5000,
                    3,
                    "Truck",
                    "GCV",
                    1,
                    "Diesel",
                    4,
                    2200,
                ),
                (2, "TATA", "407", "STD", 7000, 6, "Tipper", "GCV", 1, "CNG", 6, 3300),
            ]
        )
        rows = parse_chola_mmv(wb)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sub_class"], "Truck")
        self.assertEqual(rows[0]["fuel"], "diesel")


class MMVSlugTests(unittest.TestCase):
    def test_corporate_boilerplate_and_conjunctions_are_dropped(self):
        self.assertEqual(mmv_slug("MAHINDRA AND MAHINDRA"), "mahindra")
        self.assertEqual(mmv_slug("TATA MOTORS LTD"), "tata")
        self.assertEqual(mmv_slug("Tata"), "tata")
        self.assertEqual(mmv_slug("A V AUTOMOTIVES PVT LTD"), "a v")

    def test_punctuation_and_case_are_folded(self):
        self.assertEqual(mmv_slug("Maruti-Suzuki"), mmv_slug("MARUTI SUZUKI"))
        self.assertEqual(mmv_slug("STD (180 CC)"), "std 180 cc")

    def test_nothing_identifying_yields_empty(self):
        self.assertEqual(mmv_slug(""), "")
        self.assertEqual(mmv_slug("PVT LTD"), "")


def _index(rows):
    """Build an index from row dicts without touching the DB.

    Deliberately goes through the REAL ``build_mmv_index`` with the resolver
    stubbed, rather than hand-assembling the dict: a fixture that mirrors the
    index's internal shape silently rots the moment that shape changes, and
    every test built on it then asserts against a structure the production
    code no longer produces.
    """
    from master_data.services.mmv import build_mmv_index

    with mock.patch(
        "master_data.services.resolver.carrier_vehicle_mmv", return_value=rows
    ):
        return build_mmv_index("Chola")


def _row(product, make, model):
    return {
        "product": product,
        "make": make,
        "model": model,
        "make_slug": mmv_slug(make),
        "model_slug": mmv_slug(model),
    }


class MMVMatchTests(unittest.TestCase):
    def setUp(self):
        self.index = _index(
            [
                _row("CV", "TATA MOTORS LTD", "407"),
                _row("Car", "TATA", "NEXON"),
                _row("Car", "MARUTI", "SWIFT"),
                _row("Car", "MARUTI", "SWIFT DZIRE"),
                _row("CV", "MAHINDRA AND MAHINDRA", "BOLERO"),
                _row("CV", "SCOOTERS INDIA", "VIKRAM"),
                _row("TW", "HERO", "SPLENDOR PLUS"),
                _row("TW", "HERO ELECTRIC", "OPTIMA"),
            ]
        )

    def test_exact_and_boilerplate_variants_resolve_to_one_spelling(self):
        """The point is convergence, not a particular string: whichever
        spelling the master carries, both inputs must land on the SAME one."""
        self.assertEqual(
            match_make(self.index, "CV", "Mahindra"),
            match_make(self.index, "CV", "MAHINDRA AND MAHINDRA"),
        )
        self.assertEqual(
            match_make(self.index, "CV", "Mahindra"), "MAHINDRA AND MAHINDRA"
        )

    def test_shortest_spelling_wins_when_the_master_carries_several(self):
        index = _index(
            [
                _row("CV", "MAHINDRA AND MAHINDRA", "BOLERO"),
                _row("Car", "MAHINDRA", "XUV"),
            ]
        )
        self.assertEqual(match_make(index, "CV", "Mahindra"), "MAHINDRA")

    def test_make_spelling_is_resolved_carrier_wide_not_per_product(self):
        """A make is a brand: the commercial book only ever writes "TATA
        MOTORS LTD" where the car book writes "TATA", and the same
        manufacturer must not export under two names."""
        self.assertEqual(match_make(self.index, "CV", "TATA"), "TATA")
        self.assertEqual(match_make(self.index, "Car", "TATA"), "TATA")

    def test_matching_stays_scoped_to_the_product_bucket(self):
        self.assertIsNone(match_make(self.index, "Car", "SCOOTERS INDIA"))
        self.assertIsNone(match_make(self.index, "TW", "TATA"))

    def test_unique_prefix_hit_resolves(self):
        """The rung the docstring sells: a grid writing the bare brand
        resolves to the master's single longer spelling.

        This is the ONLY rung that can invent a canonical spelling the input
        did not contain, so it is pinned from three sides — here, the
        ambiguity case, and the exact-wins case below.
        """
        index = _index([_row("Car", "MARUTI SUZUKI", "SWIFT")])
        self.assertEqual(match_make(index, "Car", "Maruti"), "MARUTI SUZUKI")

    def test_ambiguous_prefix_declines(self):
        """Two makes share the prefix, so neither may be chosen.

        The fixture deliberately carries NO bare "HERO": with one present the
        exact rung answers first and this rung never runs.
        """
        index = _index(
            [
                _row("TW", "HERO ELECTRIC", "OPTIMA"),
                _row("TW", "HERO HONDA", "SPLENDOR"),
            ]
        )
        self.assertIsNone(match_make(index, "TW", "Hero"))

    def test_exact_match_beats_a_longer_prefix_sibling(self):
        index = _index(
            [
                _row("TW", "HERO", "SPLENDOR PLUS"),
                _row("TW", "HERO ELECTRIC", "OPTIMA"),
            ]
        )
        self.assertEqual(match_make(index, "TW", "Hero"), "HERO")

    def test_a_truncated_word_is_not_a_prefix_hit(self):
        """Prefix matching is token-boundary anchored — "hero elec" must not
        reach "hero electric", or any truncation would match."""
        self.assertIsNone(match_make(self.index, "TW", "HERO ELEC"))

    def test_non_vehicle_text_never_matches(self):
        """The failure mode this whole validator exists for: a sub-class word
        or CC band mis-extracted into vehicle_make must NOT be rewritten into
        a real manufacturer (SCOOTER -> SCOOTERS INDIA)."""
        for text in ("SCOOTER", "150_350cc", "Loader", "0.205"):
            self.assertIsNone(match_make(self.index, "CV", text), text)

    def test_model_is_scoped_to_its_make(self):
        self.assertEqual(match_model(self.index, "Car", "MARUTI", "swift"), "SWIFT")
        self.assertEqual(
            match_model(self.index, "Car", "MARUTI", "SWIFT DZIRE"), "SWIFT DZIRE"
        )
        # NEXON is a TATA model, not a MARUTI one.
        self.assertIsNone(match_model(self.index, "Car", "MARUTI", "nexon"))

    def test_model_without_a_make_needs_to_be_unambiguous(self):
        self.assertEqual(match_model(self.index, "Car", None, "nexon"), "NEXON")

    def test_model_without_a_make_declines_when_ambiguous(self):
        """SWIFT under two manufacturers is not resolvable without a make —
        picking either would name a vehicle the grid never mentioned."""
        index = _index(
            [
                _row("Car", "MARUTI", "SWIFT"),
                _row("Car", "SUZUKI", "Swift"),
            ]
        )
        self.assertIsNone(match_model(index, "Car", None, "swift"))

    def test_model_with_no_identifying_text_declines(self):
        self.assertIsNone(match_model(self.index, "Car", "MARUTI", ""))
        self.assertIsNone(match_model(self.index, "Car", "MARUTI", "PVT LTD"))

    def test_equal_length_spellings_break_alphabetically(self):
        """Two spellings, one slug, same length — the tie-break has to be
        deterministic or the exported spelling flaps between reseeds
        depending on which row the query returned first."""
        index = _index([_row("Car", "B-M-W", "X"), _row("CV", "B M W", "X")])
        # Both spellings slug to "b m w"; "B M W" wins on the alphabetical
        # tie-break (space sorts before hyphen), from either product.
        self.assertEqual(match_make(index, "Car", "B-M-W"), "B M W")
        self.assertEqual(match_make(index, "CV", "B M W"), "B M W")

    def test_empty_index_is_inert(self):
        self.assertIsNone(match_make({}, "Car", "TATA"))
        self.assertIsNone(match_model({}, "Car", "TATA", "NEXON"))

    def test_rows_with_no_product_or_make_are_skipped_by_the_index(self):
        from master_data.services.mmv import build_mmv_index

        rows = [
            {
                "product": "",
                "make": "X",
                "model": "Y",
                "make_slug": "x",
                "model_slug": "y",
            },
            {
                "product": "Car",
                "make": "",
                "model": "Y",
                "make_slug": "",
                "model_slug": "y",
            },
        ]
        with mock.patch(
            "master_data.services.resolver.carrier_vehicle_mmv", return_value=rows
        ):
            self.assertEqual(build_mmv_index("Chola"), {"_makes": {}})

    def test_index_build_failure_degrades_to_empty(self):
        """A DB outage must leave every consumer inert, not raise."""
        from master_data.services.mmv import build_mmv_index

        with mock.patch(
            "master_data.services.resolver.carrier_vehicle_mmv",
            side_effect=RuntimeError("db down"),
        ):
            self.assertEqual(build_mmv_index("Chola"), {})


if __name__ == "__main__":
    unittest.main()
