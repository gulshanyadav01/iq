"""
Composite make cells must split into separate makes.

Chola's GCCV "2_UPTO_3.5T" MAKE cell is the newline-joined
``TATA/\\nMaruti/\\nMahindra`` (the indexer flattens the newlines to spaces), and
the TW sheet ships a ``Hero vs Bajaj`` comparison column. Both used to survive as
a single opaque ``vehicle_make`` string, so any downstream match on "TATA" or
"Bajaj" missed the rule entirely.

Guards under test:
  - the "All Other Make/Models" sentinel keeps its slash (``_bifurcate_make_buckets``
    matches the intact phrase),
  - a split is rejected unless every segment still looks like a make,
  - models are never split.

Run from the backend container:
    python manage.py test cins.tests.test_composite_make_split -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
from cins.schema.raw_extraction import (
    RawCommissionEntry,
    RawExtraction,
    RawProviderInfo,
)


def _raw(entries, provider="Chola"):
    return RawExtraction(
        source_file="test.xlsx",
        source_type="excel",
        provider=RawProviderInfo(name=provider),
        start_date="2025-07-01",
        end_date="2025-07-31",
        commission_entries=entries,
    )


def _gccv_entry(make, model, value="42.5%"):
    return RawCommissionEntry(
        rule_text="Commercial Vehicle 2_UPTO_3.5T",
        commission_value=value,
        product_name="Commercial Vehicle",
        product_type="GCV",
        coverage_type_text="comprehensive",
        conditions={"gvw": "UPTO 3.5T", "vehicle_make": make, "vehicle_model": model},
    )


class SplitCompositeMakesUnitTests(TestCase):
    """Direct coverage of the helper, independent of the transform pipeline."""

    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def test_slash_joined_makes_split(self):
        self.assertEqual(
            self.t._split_composite_makes(["TATA/ Maruti/ Mahindra"]),
            ["TATA", "Maruti", "Mahindra"],
        )

    def test_slash_without_spaces_splits(self):
        self.assertEqual(
            self.t._split_composite_makes(["TATA/Maruti/Mahindra"]),
            ["TATA", "Maruti", "Mahindra"],
        )

    def test_vs_joined_makes_split(self):
        for label in (
            "Hero vs Bajaj",
            "Hero VS Bajaj",
            "Hero vs. Bajaj",
            "Hero v/s Bajaj",
        ):
            with self.subTest(label=label):
                self.assertEqual(
                    self.t._split_composite_makes([label]), ["Hero", "Bajaj"]
                )

    def test_sentinel_keeps_its_slash(self):
        self.assertEqual(
            self.t._split_composite_makes(["All Other Make/Models"]),
            ["All Other Make/Models"],
        )
        self.assertEqual(self.t._split_composite_makes(["Others"]), ["Others"])

    def test_trailing_slash_does_not_produce_a_blank_make(self):
        self.assertEqual(self.t._split_composite_makes(["TATA/"]), ["TATA/"])
        self.assertEqual(self.t._split_composite_makes(["/Maruti"]), ["/Maruti"])

    def test_plain_makes_untouched(self):
        self.assertEqual(
            self.t._split_composite_makes(["Hero OEM", "TVS OEM"]),
            ["Hero OEM", "TVS OEM"],
        )

    def test_duplicates_collapse_case_insensitively(self):
        self.assertEqual(
            self.t._split_composite_makes(["TATA/Maruti", "tata"]), ["TATA", "Maruti"]
        )

    def test_empty_input_passes_through(self):
        self.assertIsNone(self.t._split_composite_makes(None))
        self.assertEqual(self.t._split_composite_makes([]), [])


class SplitCompositeMakesTransformTests(TestCase):
    """End-to-end through _transform_product, incl. the exclusion buckets."""

    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def _rules(self, entries):
        product = self.t._transform_product(
            "Commercial Vehicle", entries, None, _raw(entries), segment="GCV"
        )
        return product.commission_rules

    def test_slash_joined_make_splits_in_emitted_rule(self):
        rules = self._rules([_gccv_entry("TATA/ Maruti/ Mahindra", "Ace, Super Ace")])
        made = [r for r in rules if r.conditions.vehicle_make]
        self.assertTrue(made)
        for r in made:
            self.assertEqual(
                set(r.conditions.vehicle_make), {"TATA", "Maruti", "Mahindra"}
            )

    def test_synthesized_make_exclude_is_split_too(self):
        entries = [
            _gccv_entry("TATA/ Maruti/ Mahindra", "Ace, Super Ace", "42.5%"),
            _gccv_entry("All Other Make/Models", "All Other Make/Models", "20%"),
        ]
        rules = self._rules(entries)
        excl = [r for r in rules if r.conditions.vehicle_make_exclude]
        self.assertEqual(len(excl), 1, rules)
        self.assertEqual(
            set(excl[0].conditions.vehicle_make_exclude),
            {"TATA", "Maruti", "Mahindra"},
        )

    def test_model_slashes_are_not_split(self):
        rules = self._rules([_gccv_entry("TATA", "Ace/Gold, Super Ace")])
        made = [r for r in rules if r.conditions.vehicle_model]
        self.assertTrue(made)
        for r in made:
            self.assertIn("Ace/Gold", r.conditions.vehicle_model)

    def test_subclass_body_row_still_relocates_not_splits(self):
        # make == model enumeration is a sub-class list, not a make x model
        # grading. The split must not run before that guard and leave bogus makes.
        body = (
            "Loader, Excavators/Earth Movers, Crane, "
            "Bulldozers/Bullgraders, Road Rollers, Fork Lift Trucks"
        )
        entry = RawCommissionEntry(
            rule_text="Commercial Vehicle 2_EXCAVATOR",
            commission_value="30%",
            product_name="Commercial Vehicle",
            product_type="MISC-D",
            coverage_type_text="comprehensive",
            conditions={"vehicle_make": body, "vehicle_model": body},
        )
        product = self.t._transform_product(
            "Commercial Vehicle", [entry], None, _raw([entry]), segment="MISC-D"
        )
        for r in product.commission_rules:
            self.assertIsNone(r.conditions.vehicle_make, r.conditions)
            self.assertIsNone(r.conditions.vehicle_model, r.conditions)
            self.assertTrue(r.conditions.vehicle_sub_class, r.conditions)
