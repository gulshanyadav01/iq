"""
Regression tests for the Chola "July Grid" bifurcation fixes:

- F1: Commercial-vehicle positional ``[Electric]`` rows must be tagged
  ``fuel_type=electric`` so Electric and Non-Electric form separate deals.
- F2: Commercial Vehicle must split into GCV / PCV / Buses / MISC-D SubProducts
  (source PROD column GCCV / PCCV<6 / PCCV>6 / MSV) while keeping
  ``product_name="Commercial Vehicle"``.
- F3: Electric two-wheelers are kW-rated, not CC-rated — they must never carry a
  CC filter (guards the kW-leaked-into-cc=(7,7) collapse).
- F5: The Car product splits into Private Car / Taxi SubProducts.

Run from the backend container:
    python manage.py test cins.tests.test_cv_subproduct_and_electric -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
from cins.schema.raw_extraction import (
    RawCommissionEntry,
    RawExtraction,
    RawProviderInfo,
)


def _raw(entries):
    return RawExtraction(
        source_file="test.xlsx",
        source_type="excel",
        provider=RawProviderInfo(name="Chola"),
        start_date="2024-07-01",
        end_date="2024-07-31",
        commission_entries=entries,
    )


class CvSegmentLabelTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def _entry(self, **kw):
        base = dict(
            rule_text="rule",
            commission_value="10%",
            product_name="Commercial Vehicle",
        )
        base.update(kw)
        return RawCommissionEntry(**base)

    def test_canonical_product_type_tokens(self):
        cases = {
            "GCV": "GCV",
            "GCCV": "GCV",
            "PCV": "PCV",
            "PCCV<6": "PCV",
            "PCCV>6": "Buses",
            "Buses": "Buses",
            "MISC-D": "MISC-D",
            "MSV": "MISC-D",
        }
        for pt, expected in cases.items():
            self.assertEqual(
                self.t._cv_segment_label(self._entry(product_type=pt)), expected, pt
            )

    def test_pccv_gt6_is_buses_not_pcv(self):
        e = self._entry(product_type="Commercial Vehicle", source_section="PCCV > 6")
        self.assertEqual(self.t._cv_segment_label(e), "Buses")

    def test_body_slug_backstop(self):
        self.assertEqual(
            self.t._cv_segment_label(
                self._entry(conditions={"vehicle_sub_class": "school-bus"})
            ),
            "Buses",
        )
        self.assertEqual(
            self.t._cv_segment_label(
                self._entry(conditions={"vehicle_sub_class": "excavators"})
            ),
            "MISC-D",
        )

    def test_commercial_unclassified_defaults_to_gcv(self):
        e = self._entry(product_type="Commercial Vehicle", rule_text="2_UPTO_3.5T")
        self.assertEqual(self.t._cv_segment_label(e), "GCV")

    def test_non_commercial_returns_none(self):
        self.assertIsNone(
            self.t._cv_segment_label(
                RawCommissionEntry(
                    rule_text="r", commission_value="5%", product_name="Two Wheeler"
                )
            )
        )


class CarSegmentLabelTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_private_car(self):
        e = RawCommissionEntry(
            rule_text="r", commission_value="30%", product_name="Private Car"
        )
        self.assertEqual(self.t._car_segment_label(e), "Private Car")

    def test_taxi(self):
        e = RawCommissionEntry(
            rule_text="r",
            commission_value="30%",
            product_name="Private Car",
            conditions={"vehicle_sub_class": "taxi"},
        )
        self.assertEqual(self.t._car_segment_label(e), "Taxi")

    def test_two_wheeler_is_not_car(self):
        e = RawCommissionEntry(
            rule_text="r", commission_value="5%", product_name="Two Wheeler"
        )
        self.assertIsNone(self.t._car_segment_label(e))


class CommercialSegmentSplitTests(TestCase):
    """_transform_products must emit one product per commercial segment, each
    keeping product_name='Commercial Vehicle' and carrying the segment as its
    SubProduct (sub_category)."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_gcv_pcv_buses_miscd_split(self):
        entries = [
            RawCommissionEntry(
                rule_text=f"CV {seg}",
                commission_value="10%",
                product_name="Commercial Vehicle",
                product_type=seg,
                coverage_type_text="comprehensive",
            )
            for seg in ("GCV", "PCV", "Buses", "MISC-D")
        ]
        products = self.t._transform_products(entries, None, _raw(entries))
        self.assertEqual(len(products), 4)
        for p in products:
            self.assertEqual(p.product_name, "Commercial Vehicle")
        by_sub = {p.sub_category: p for p in products}
        self.assertEqual(set(by_sub), {"GCV", "PCV", "Buses", "MISC-D"})
        self.assertEqual(by_sub["Buses"].product_subproduct, "commercial-buses")
        # product codes are unique per segment
        self.assertEqual(len({p.product_code for p in products}), 4)


class ElectricFuelRecoveryTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_bracket_electric_sets_fuel_type(self):
        entry = RawCommissionEntry(
            rule_text="Commercial Vehicle 2_UPTO_3.5T[Electric] MH/GA PACK",
            commission_value="20%",
            product_name="Commercial Vehicle",
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertEqual(cond.fuel_type, ["electric"])

    def test_non_electric_sibling_stays_unset(self):
        entry = RawCommissionEntry(
            rule_text="Commercial Vehicle 2_UPTO_3.5T MH/GA PACK",
            commission_value="20%",
            product_name="Commercial Vehicle",
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertIsNone(cond.fuel_type)


class ElectricTwoWheelerCcTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_electric_tw_cc_reset_to_open_sentinel(self):
        # kW leaked into cc as "7" -> would parse to (7,7); must reset to open.
        entry = RawCommissionEntry(
            rule_text="TW electric",
            commission_value="17%",
            product_name="Two Wheeler",
            conditions={"fuel_type": "Electric", "cc": "7"},
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertEqual(cond.fuel_type, ["electric"])
        self.assertEqual((cond.cc_start_range, cond.cc_end_range), (0, 99999))

    def test_petrol_tw_cc_preserved(self):
        entry = RawCommissionEntry(
            rule_text="TW petrol 150cc",
            commission_value="42%",
            product_name="Two Wheeler",
            conditions={"cc": "150-350"},
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertEqual((cond.cc_start_range, cond.cc_end_range), (150, 350))


class TwMakeCcBandGuardTests(TestCase):
    """Codegen mis-splits a flattened '<make> > <band> > <value>' TW header when
    the make is not repeated → the CC band lands in vehicle_make and a rate in
    cc. The transformer must move the band back to cc and clear the bogus make."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_cc_band_in_make_moved_to_cc(self):
        entry = RawCommissionEntry(
            rule_text="Two Wheeler AP ANNUAL",
            commission_value="22%",
            product_name="Two Wheeler",
            conditions={"vehicle_make": "150_350cc", "cc": "0.205"},
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertIsNone(cond.vehicle_make)
        self.assertEqual((cond.cc_start_range, cond.cc_end_range), (150, 350))

    def test_real_make_preserved(self):
        entry = RawCommissionEntry(
            rule_text="Two Wheeler AP",
            commission_value="42%",
            product_name="Two Wheeler",
            conditions={"vehicle_make": "Hero OEM", "cc": "150-350"},
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertEqual(cond.vehicle_make, ["Hero OEM"])
        self.assertEqual((cond.cc_start_range, cond.cc_end_range), (150, 350))

    def test_make_containing_cc_substring_not_dropped(self):
        # "CCM" is a real motorcycle make; the guard must not treat a make that
        # merely contains "cc" as a mis-split CC band.
        entry = RawCommissionEntry(
            rule_text="Two Wheeler AP",
            commission_value="42%",
            product_name="Two Wheeler",
            conditions={"vehicle_make": "CCM", "cc": "150-350"},
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertEqual(cond.vehicle_make, ["CCM"])
        self.assertEqual((cond.cc_start_range, cond.cc_end_range), (150, 350))


class CommercialFuelBifurcationTests(TestCase):
    """A commercial product that has a dedicated Electric row must pin its
    fuel-agnostic siblings to the four non-electric fuels, so Electric and
    Non-Electric are separate, non-overlapping deals."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_non_electric_base_pinned_when_electric_present(self):
        entries = [
            RawCommissionEntry(
                rule_text="Commercial Vehicle 2_UPTO_3.5T MH/GA PACK",
                commission_value="20%",
                product_name="Commercial Vehicle",
                product_type="GCV",
                coverage_type_text="comprehensive",
            ),
            RawCommissionEntry(
                rule_text="Commercial Vehicle 2_UPTO_3.5T[Electric] MH/GA PACK",
                commission_value="25%",
                product_name="Commercial Vehicle",
                product_type="GCV",
                coverage_type_text="comprehensive",
            ),
        ]
        product = self.t._transform_product(
            "Commercial Vehicle", entries, None, _raw(entries), segment="GCV"
        )
        fuels = {tuple(r.conditions.fuel_type or []) for r in product.commission_rules}
        self.assertIn(("electric",), fuels)
        self.assertIn(("petrol", "diesel", "cng", "lpg"), fuels)
        # no rule left fuel-agnostic (None) once electric is broken out
        self.assertTrue(all(r.conditions.fuel_type for r in product.commission_rules))

    def test_no_electric_leaves_base_unset(self):
        entries = [
            RawCommissionEntry(
                rule_text="Commercial Vehicle 5_12T_TO_16T MH PACK",
                commission_value="20%",
                product_name="Commercial Vehicle",
                product_type="GCV",
                coverage_type_text="comprehensive",
            )
        ]
        product = self.t._transform_product(
            "Commercial Vehicle", entries, None, _raw(entries), segment="GCV"
        )
        self.assertTrue(
            all(r.conditions.fuel_type is None for r in product.commission_rules)
        )
