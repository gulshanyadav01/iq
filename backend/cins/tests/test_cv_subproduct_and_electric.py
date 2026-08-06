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
from cins.schema.cics_model import CommissionRule
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


class Gcv3wOpenGvwTests(TestCase):
    """A goods 3-wheeler carries no gvw band (all-GVW, never weight-graded).
    Its degenerate (0, 0) default must widen to the open (0, 999999) ceiling."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_gcv_3w_wheel_token_widens_gvw(self):
        entry = RawCommissionEntry(
            rule_text="Commercial Vehicle 1_GCCV_3W AP/TS PACK",
            commission_value="20%",
            product_name="Commercial Vehicle",
            conditions={
                "number_of_wheels": "3W",
                "vehicle_sub_class": "1_GCCV_3W",
            },
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertEqual((cond.gvw_start_range, cond.gvw_end_range), (0, 999999))

    def test_gcv_3w_subclass_only_widens_gvw(self):
        # Wheel token absent, only the GCV-3W sub-class slug carries the signal.
        entry = RawCommissionEntry(
            rule_text="Commercial Vehicle 1_GCCV_3W AP/TS PACK",
            commission_value="20%",
            product_name="Commercial Vehicle",
            conditions={"vehicle_sub_class": "1_GCCV_3W"},
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertEqual((cond.gvw_start_range, cond.gvw_end_range), (0, 999999))

    def test_real_gvw_band_preserved(self):
        # A genuine 4W weight grade must NOT be widened.
        entry = RawCommissionEntry(
            rule_text="Commercial Vehicle GCV 4W 3500-7500 PACK",
            commission_value="20%",
            product_name="Commercial Vehicle",
            conditions={"number_of_wheels": "4W & Above", "gvw": "3500-7500"},
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertEqual((cond.gvw_start_range, cond.gvw_end_range), (3500, 7500))

    def test_four_wheel_zero_zero_not_widened(self):
        # 4W row that legitimately parsed to (0, 0) stays (0, 0) — no 3W signal.
        entry = RawCommissionEntry(
            rule_text="Commercial Vehicle GCV 4W PACK",
            commission_value="20%",
            product_name="Commercial Vehicle",
            conditions={"number_of_wheels": "4W & Above"},
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertEqual((cond.gvw_start_range, cond.gvw_end_range), (0, 0))


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

    def test_body_token_in_make_moved_to_subclass(self):
        # A make-less trailing column "SCOOTER > 0.37" mis-splits the body word
        # into vehicle_make and the rate into cc. SCOOTER is a sub-class, never a
        # make; the rate is not a CC. Route SCOOTER -> vehicle_sub_class, clear
        # the bogus make, and leave cc unconstrained (all-CC), matching the
        # make-qualified scooter rows. Rule is still emitted (kept, not dropped).
        entry = RawCommissionEntry(
            rule_text="Two Wheeler SCOOTER 0.37 AP ACT",
            commission_value="30%",
            product_name="Two Wheeler",
            conditions={"vehicle_make": "SCOOTER", "cc": "0.37"},
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertIsNone(cond.vehicle_make)
        self.assertEqual(cond.vehicle_sub_class, ["scooter"])
        self.assertEqual((cond.cc_start_range, cond.cc_end_range), (0, 99999))

    def test_body_token_negative_value_cc_cleaned(self):
        # Same shape with the negative header value "-0.05" landing in cc.
        entry = RawCommissionEntry(
            rule_text="Two Wheeler SCOOTER -0.05 AP ACT",
            commission_value="5%",
            product_name="Two Wheeler",
            conditions={"vehicle_make": "SCOOTER", "cc": "-0.05"},
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertIsNone(cond.vehicle_make)
        self.assertEqual(cond.vehicle_sub_class, ["scooter"])
        self.assertEqual((cond.cc_start_range, cond.cc_end_range), (0, 99999))


class PccvTaxiRoutingTests(TestCase):
    """R1: BIG_TAXIS / MAXI_CAB rows sit under the ``PCCV > 6`` section (token
    → Buses) but are taxis (PCV). The taxi-body check must beat the section
    token; school/staff bus rows in the same section stay Buses."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def _e(self, **kw):
        base = dict(
            rule_text="rule",
            commission_value="10%",
            product_name="Commercial Vehicle",
            source_section="PCCV > 6",
        )
        base.update(kw)
        return RawCommissionEntry(**base)

    def test_big_taxi_is_pcv_not_buses(self):
        e = self._e(conditions={"vehicle_sub_class": "big taxi"})
        self.assertEqual(self.t._cv_segment_label(e), "PCV")

    def test_maxi_cab_is_pcv_not_buses(self):
        e = self._e(conditions={"vehicle_sub_class": "maxi cab"})
        self.assertEqual(self.t._cv_segment_label(e), "PCV")

    def test_school_bus_stays_buses(self):
        e = self._e(conditions={"vehicle_sub_class": "school bus"})
        self.assertEqual(self.t._cv_segment_label(e), "Buses")

    def test_staff_bus_stays_buses(self):
        e = self._e(conditions={"vehicle_sub_class": "staff bus"})
        self.assertEqual(self.t._cv_segment_label(e), "Buses")

    def test_bare_pccv_gt6_token_still_buses(self):
        e = RawCommissionEntry(
            rule_text="rule",
            commission_value="10%",
            product_name="Commercial Vehicle",
            product_type="PCCV>6",
        )
        self.assertEqual(self.t._cv_segment_label(e), "Buses")


class SeatingRecoveryTests(TestCase):
    """G3a: PCCV<6 → (0,5), PCCV>6 → (6,99); the 6-seater is in the upper band."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def _cond(self, **kw):
        base = dict(
            rule_text="Commercial Vehicle",
            commission_value="10%",
            product_name="Commercial Vehicle",
        )
        base.update(kw)
        e = RawCommissionEntry(**base)
        return self.t._transform_conditions(e, None, _raw([e]))

    def test_pccv_lt6_recovers_0_5(self):
        cond = self._cond(
            source_section="PCCV < 6", conditions={"vehicle_sub_class": "taxi"}
        )
        self.assertEqual(
            (cond.seating_capacity_start_range, cond.seating_capacity_end_range), (0, 5)
        )

    def test_pccv_gt6_recovers_6_99(self):
        cond = self._cond(
            source_section="PCCV > 6", conditions={"vehicle_sub_class": "taxi"}
        )
        self.assertEqual(
            (cond.seating_capacity_start_range, cond.seating_capacity_end_range),
            (6, 99),
        )

    def test_above_6_token_boundary_six_in_upper_band(self):
        cond = self._cond(conditions={"seating_capacity": "ABOVE 6"})
        self.assertEqual(
            (cond.seating_capacity_start_range, cond.seating_capacity_end_range),
            (6, 99),
        )

    def test_no_token_stays_open_sentinel(self):
        cond = self._cond(
            source_section="GCCV", conditions={"vehicle_sub_class": "truck"}
        )
        self.assertEqual(
            (cond.seating_capacity_start_range, cond.seating_capacity_end_range),
            (0, 99),
        )


class CcRecoveryTests(TestCase):
    """PCCV<6 4W taxi CC bands baked into the sub-class slug (Chola-gated)."""

    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def _cond(self, **kw):
        e = RawCommissionEntry(
            rule_text=kw.pop("rule_text", "Commercial Vehicle"),
            commission_value="10%",
            product_name="Commercial Vehicle",
            **kw,
        )
        return self.t._transform_conditions(e, None, _raw([e]))

    def test_cc_from_conditions_lt_band(self):
        cond = self._cond(conditions={"cc": "0-1500"})
        self.assertEqual((cond.cc_start_range, cond.cc_end_range), (0, 1500))

    def test_cc_recovered_from_lt_slug(self):
        cond = self._cond(
            source_section="PCCV < 6",
            conditions={"vehicle_sub_class": "2_4W_LT_1500CC"},
        )
        self.assertEqual((cond.cc_start_range, cond.cc_end_range), (0, 1500))

    def test_cc_recovered_from_gt_slug(self):
        cond = self._cond(
            source_section="PCCV < 6",
            conditions={"vehicle_sub_class": "3_4W_GT_1500CC"},
        )
        self.assertEqual((cond.cc_start_range, cond.cc_end_range), (1500, 99999))

    def test_cc_recovery_gated_to_chola(self):
        # Non-Chola carrier: the LT/GT slug must NOT trigger CC recovery.
        self.t._carrier_code = "Digit"
        cond = self._cond(
            source_section="PCCV < 6",
            conditions={"vehicle_sub_class": "2_4W_LT_1500CC"},
        )
        self.assertEqual((cond.cc_start_range, cond.cc_end_range), (0, 99999))

    def test_cc_recovery_not_triggered_by_embedded_letters(self):
        # A make/model that merely contains "lt"/"gt" before a CC number
        # ("Bolt 1500cc") must not spuriously narrow an all-CC rule — the
        # lt/gt token is anchored to a slug boundary.
        cond = self._cond(
            rule_text="Bolt 1500cc variant",
            conditions={"vehicle_sub_class": "taxi"},
        )
        self.assertEqual((cond.cc_start_range, cond.cc_end_range), (0, 99999))


class MsvDistinctRulesTests(TestCase):
    """R2: MSV rows become one distinct rule per body — no MISC-D lumping."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_msv_rows_stay_distinct(self):
        specs = [
            ("tractor", "10%"),
            ("excavators", "12%"),
            ("harvester", "14%"),
            ("others", "0%"),
        ]
        entries = [
            RawCommissionEntry(
                rule_text=f"MSV {sc}",
                commission_value=cv,
                product_name="Commercial Vehicle",
                product_type="MISC-D",
                coverage_type_text="comprehensive",
                conditions={"vehicle_sub_class": sc},
            )
            for sc, cv in specs
        ]
        product = self.t._transform_product(
            "Commercial Vehicle", entries, None, _raw(entries), segment="MISC-D"
        )
        subclasses = {
            tuple(r.conditions.vehicle_sub_class or [])
            for r in product.commission_rules
        }
        for expected in (("tractor",), ("excavators",), ("harvester",), ("others",)):
            self.assertIn(expected, subclasses)
        self.assertEqual(product.sub_category, "MISC-D")

    def test_trac_renewal_sets_business_type(self):
        e = RawCommissionEntry(
            rule_text="MSV 1_TRAC[RENEWAL]",
            commission_value="10%",
            product_name="Commercial Vehicle",
            conditions={"vehicle_sub_class": "1_TRAC[RENEWAL]"},
        )
        cond = self.t._transform_conditions(e, None, _raw([e]))
        self.assertEqual(cond.business_type, ["renewal"])

    def test_msv_subclass_aliases_normalized(self):
        # Grid-verbatim MSV/PCCV>6 body aliases map to canonical sub-classes.
        e = RawCommissionEntry(
            rule_text="MSV",
            commission_value="10%",
            product_name="Commercial Vehicle",
            conditions={
                "vehicle_sub_class": "Earth Movers, Bullgraders, Big Taxis, Maxi Cab"
            },
        )
        cond = self.t._transform_conditions(e, None, _raw([e]))
        subs = set(cond.vehicle_sub_class or [])
        self.assertIn("excavators", subs)  # earth movers -> excavators
        self.assertIn("bulldozer", subs)  # bullgraders -> bulldozer
        self.assertIn("taxi", subs)  # big taxis -> taxi
        self.assertIn("maxicab", subs)  # maxi cab -> maxicab

    def test_excavator_model_enumeration_to_subclass(self):
        e = RawCommissionEntry(
            rule_text="MSV 2_EXCAVATOR",
            commission_value="12%",
            product_name="Commercial Vehicle",
            conditions={"vehicle_sub_class": "Loader, Excavators, Crane"},
        )
        cond = self.t._transform_conditions(e, None, _raw([e]))
        self.assertIn("excavators", cond.vehicle_sub_class)
        self.assertIn("loader", cond.vehicle_sub_class)
        self.assertIn("crane", cond.vehicle_sub_class)

    def test_excavator_body_enumeration_not_leaked_into_make_model(self):
        # Source MSV "2_EXCAVATOR" repeats the body enumeration verbatim in the
        # MAKE and MODEL columns; codegen copies it into make+model via the
        # generic CV column rule. Bodies are sub-classes, not make/model — a
        # make list identical to the model list must be dropped (Chola-gated).
        bodies = (
            "Loader, Excavators/Earth Movers, Crane, "
            "Bulldozers/Bullgraders, Road Rollers, Fork Lift Trucks"
        )
        e = RawCommissionEntry(
            rule_text="MSV 2_EXCAVATOR",
            commission_value="12%",
            product_name="Commercial Vehicle",
            conditions={
                "vehicle_sub_class": bodies,
                "vehicle_make": bodies,
                "vehicle_model": bodies,
            },
        )
        self.t._carrier_code = "Chola"
        cond = self.t._transform_conditions(e, None, _raw([e]))
        self.assertIsNone(cond.vehicle_make)
        self.assertIsNone(cond.vehicle_model)
        # Bodies still survive as the sub-class dimension.
        self.assertIn("excavators", cond.vehicle_sub_class)
        self.assertIn("crane", cond.vehicle_sub_class)

    def test_real_make_model_split_preserved(self):
        # A genuine make x model grading (make != model, e.g. GCCV 2_UPTO_3.5T
        # "TATA/Maruti/Mahindra" + model list) must NOT be dropped.
        e = RawCommissionEntry(
            rule_text="GCV 2_UPTO_3.5T AP/TS ACT",
            commission_value="42%",
            product_name="Commercial Vehicle",
            conditions={
                "vehicle_make": "TATA/ Maruti/ Mahindra",
                "vehicle_model": "Ace, Super Ace, Yodha",
            },
        )
        self.t._carrier_code = "Chola"
        cond = self.t._transform_conditions(e, None, _raw([e]))
        # Preserved AND split into its three constituent makes — see
        # cins.tests.test_composite_make_split.
        self.assertEqual(cond.vehicle_make, ["TATA", "Maruti", "Mahindra"])
        self.assertEqual(cond.vehicle_model, ["Ace", "Super Ace", "Yodha"])


class ElectricGcv3wTests(TestCase):
    """P3: the electric goods 3-wheeler (merged-header 4th segment) carries a
    "1_GCCV_3W[Electric]" sub-class. The transformer's [Electric] backstop must
    recover fuel_type=electric even when codegen drops the fuel_type field, so
    the electric deal stays distinct from its non-electric base twin."""

    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def test_electric_suffix_recovers_fuel_without_fuel_field(self):
        # Codegen omitted fuel_type; only the [Electric] sub-class suffix marks it.
        e = RawCommissionEntry(
            rule_text="Commercial Vehicle 1_GCCV_3W[Electric] AP PACK",
            commission_value="27.5%",
            product_name="Commercial Vehicle",
            conditions={
                "number_of_wheels": "3W",
                "vehicle_sub_class": "1_GCCV_3W[Electric]",
            },
        )
        cond = self.t._transform_conditions(e, None, _raw([e]))
        self.assertEqual(cond.fuel_type, ["electric"])
        # FIX 2 still widens the all-GVW 3W band.
        self.assertEqual((cond.gvw_start_range, cond.gvw_end_range), (0, 999999))

    def test_base_and_electric_stay_distinct_deals(self):
        base = RawCommissionEntry(
            rule_text="Commercial Vehicle 1_GCCV_3W AP PACK",
            commission_value="30%",
            product_name="Commercial Vehicle",
            product_type="GCV",
            coverage_type_text="comprehensive",
            conditions={
                "number_of_wheels": "3W",
                "vehicle_sub_class": "1_GCCV_3W",
                "fuel_type": "Petrol, Diesel, CNG, LPG",
            },
        )
        elec = RawCommissionEntry(
            rule_text="Commercial Vehicle 1_GCCV_3W[Electric] AP PACK",
            commission_value="27.5%",
            product_name="Commercial Vehicle",
            product_type="GCV",
            coverage_type_text="comprehensive",
            conditions={
                "number_of_wheels": "3W",
                "vehicle_sub_class": "1_GCCV_3W[Electric]",
            },
        )
        product = self.t._transform_product(
            "Commercial Vehicle", [base, elec], None, _raw([base, elec]), segment="GCV"
        )
        threew = [
            r
            for r in product.commission_rules
            if r.conditions
            and "3w" in [str(w).lower() for w in (r.conditions.number_of_wheels or [])]
        ]
        fuels = [tuple(r.conditions.fuel_type or []) for r in threew]
        # Exactly one electric 3W deal, and at least one non-electric sibling.
        self.assertTrue(any("electric" in f for f in fuels), fuels)
        self.assertTrue(any("electric" not in f for f in fuels), fuels)

    def test_recover_gcv_3w_electric_tags_second_rate(self):
        # Codegen emitted both trailing values as fuel-None rows (base 30 first,
        # electric 27.5 second) with NO electric marker at all. The deterministic
        # net must tag the later-emitted rate as electric.
        from cins.schema.cics_model import CommissionOutcome, RuleConditions

        def _r(rate):
            return CommissionRule(
                rule_id=f"r{str(rate).replace('.', '_')}",
                priority=10,
                conditions=RuleConditions(
                    number_of_wheels=["3w"],
                    vehicle_sub_class=["1_gccv_3w"],
                    state=["tamil-nadu"],
                    policy_type=["comprehensive"],
                    gvw_start_range=0,
                    gvw_end_range=999999,
                ),
                outcome=CommissionOutcome(
                    commission_type="percentage", percentage_value=rate
                ),
            )

        rules = [_r(30.0), _r(27.5)]  # base emitted first, electric second
        self.t._recover_gcv_3w_electric(rules)
        by_rate = {r.outcome.percentage_value: r for r in rules}
        self.assertEqual(by_rate[27.5].conditions.fuel_type, ["electric"])
        self.assertIsNone(by_rate[30.0].conditions.fuel_type)

    def test_recover_gcv_3w_electric_single_rate_untouched(self):
        # A state with only a base 3W rate (no electric column) stays fuel-None.
        from cins.schema.cics_model import CommissionOutcome, RuleConditions

        rule = CommissionRule(
            rule_id="solo",
            priority=10,
            conditions=RuleConditions(
                number_of_wheels=["3w"],
                vehicle_sub_class=["1_gccv_3w"],
                state=["karnataka"],
                policy_type=["comprehensive"],
            ),
            outcome=CommissionOutcome(
                commission_type="percentage", percentage_value=25.0
            ),
        )
        self.t._recover_gcv_3w_electric([rule])
        self.assertIsNone(rule.conditions.fuel_type)


class ExcavatorSubclassSplitTests(TestCase):
    """P2: the MSV 2_EXCAVATOR body enumeration (repeated verbatim in make and
    model) is relocated into vehicle_sub_class and fanned out into one distinct
    deal per subclass."""

    BODIES = (
        "Loader, Excavators/Earth Movers, Crane, "
        "Bulldozers/Bullgraders, Road Rollers, Fork Lift Trucks"
    )
    EXPECTED = {
        "loader",
        "excavators",
        "crane",
        "bulldozer",
        "road-rollers",
        "fork-lift-trucks",
    }

    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def test_coarse_slug_relocated_to_fine_subclasses(self):
        # Real production shape: vehicle_sub_class is the COARSE section slug
        # "2_EXCAVATOR"; the fine enumeration lives only in make == model.
        e = RawCommissionEntry(
            rule_text="MISC-D 2_EXCAVATOR AP ACT",
            commission_value="12%",
            product_name="Commercial Vehicle",
            conditions={
                "vehicle_sub_class": "2_EXCAVATOR",
                "vehicle_make": self.BODIES,
                "vehicle_model": self.BODIES,
            },
        )
        cond = self.t._transform_conditions(e, None, _raw([e]))
        self.assertIsNone(cond.vehicle_make)
        self.assertIsNone(cond.vehicle_model)
        self.assertEqual(set(cond.vehicle_sub_class), self.EXPECTED)

    def test_fan_out_one_deal_per_subclass(self):
        entries = [
            RawCommissionEntry(
                rule_text=f"MISC-D 2_EXCAVATOR AP {cov}",
                commission_value="12%",
                product_name="Commercial Vehicle",
                product_type="MISC-D",
                coverage_type_text="comprehensive",
                conditions={
                    "vehicle_sub_class": "2_EXCAVATOR",
                    "vehicle_make": self.BODIES,
                    "vehicle_model": self.BODIES,
                },
            )
            for cov in ("ACT", "PACK")
        ]
        product = self.t._transform_product(
            "Commercial Vehicle", entries, None, _raw(entries), segment="MISC-D"
        )
        subclasses = [
            tuple(r.conditions.vehicle_sub_class or [])
            for r in product.commission_rules
        ]
        # Every emitted rule is scoped to exactly ONE subclass.
        self.assertTrue(all(len(s) == 1 for s in subclasses))
        self.assertEqual({s[0] for s in subclasses}, self.EXPECTED)

    def test_unrecognised_enumeration_falls_back_to_null(self):
        # A make == model with no mappable body token keeps the historical
        # null-both behaviour (never kept as a bogus make/model split).
        e = RawCommissionEntry(
            rule_text="GCV 2_UPTO_3.5T AP ACT",
            commission_value="12%",
            product_name="Commercial Vehicle",
            conditions={
                "vehicle_sub_class": "2_UPTO_3.5T",
                "vehicle_make": "Zeta, Omega",
                "vehicle_model": "Zeta, Omega",
            },
        )
        cond = self.t._transform_conditions(e, None, _raw([e]))
        self.assertIsNone(cond.vehicle_make)
        self.assertIsNone(cond.vehicle_model)


class StaleGccvLeakGuardTests(TestCase):
    """Chola merged-3W-header leak: GCCV goods rows copied into PCCV/MSV headers
    must be dropped from non-GCV segments, kept in GCV."""

    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def _entries(self, descs):
        return [
            RawCommissionEntry(
                rule_text=dsc,
                commission_value="10%",
                product_name="Commercial Vehicle",
                coverage_type_text="comprehensive",
            )
            for dsc in descs
        ]

    def _products(self, descs):
        entries = self._entries(descs)
        prods = self.t._transform_products(entries, None, _raw(entries))
        return {p.sub_category: p for p in prods}

    def _descs(self, products, sub):
        p = products.get(sub)
        return [r.rule_description for r in p.commission_rules] if p else []

    def test_gccv_leak_dropped_from_pcv_kept_in_gcv(self):
        # A GCCV goods row + a real PCV row: the GCCV copy must land ONLY in GCV.
        prods = self._products(["GCCV 3W AP/TS ACT", "PCV 1_3W_AUTO AP/TS ACT"])
        self.assertFalse(
            any((d or "").lower().startswith("gccv") for d in self._descs(prods, "PCV"))
        )
        self.assertTrue(
            any((d or "").lower().startswith("gccv") for d in self._descs(prods, "GCV"))
        )
        self.assertTrue(
            any((d or "").startswith("PCV") for d in self._descs(prods, "PCV"))
        )

    def test_all_leak_segment_never_created(self):
        # A GCCV row that would segment into MISC-D produces NO MISC-D product
        # (and no empty-product crash) — it stays a GCV row only.
        prods = self._products(["GCCV 3W AP ACT"])
        self.assertNotIn("MISC-D", prods)
        self.assertIn("GCV", prods)

    def test_gccv_leak_dropped_from_buses(self):
        # A GCCV goods row + a real bus row: GCCV copy lands only in GCV.
        prods = self._products(["GCCV 3W AP ACT", "Buses 1_SCHOOL_BUS AP ACT"])
        self.assertFalse(
            any(
                (d or "").lower().startswith("gccv")
                for d in self._descs(prods, "Buses")
            )
        )
        self.assertIn("Buses", prods)
        self.assertTrue(
            any((d or "").lower().startswith("gccv") for d in self._descs(prods, "GCV"))
        )

    def test_non_chola_not_dropped(self):
        self.t._carrier_code = "Digit"
        # A stale source_section forces the GCCV rule_text into a PCV segment;
        # for a non-Chola carrier the guard must NOT fire.
        entries = [
            RawCommissionEntry(
                rule_text="GCCV 3W AP ACT",
                commission_value="10%",
                product_name="Commercial Vehicle",
                coverage_type_text="comprehensive",
                source_section="PCV 1_3W_AUTO",
            )
        ]
        prods = {
            p.sub_category: p
            for p in self.t._transform_products(entries, None, _raw(entries))
        }
        self.assertIn("PCV", prods)
        self.assertTrue(
            any(
                (r.rule_description or "").lower().startswith("gccv")
                for r in prods["PCV"].commission_rules
            )
        )


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
