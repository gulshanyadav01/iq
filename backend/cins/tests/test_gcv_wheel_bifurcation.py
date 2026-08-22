"""
Regression tests for RawToCICSTransformer._bifurcate_gcv_wheels.

GCV (Goods Carrying Vehicle) commission rules must be scoped to a single
number_of_wheels value (3W or 4W & Above). Source documents commonly publish
one GCV row that implicitly covers both wheel categories, or lists both in
one cell. Downstream deal management requires one deal per wheel category,
so any rule with missing/multiple wheel values is bifurcated.

Run from the backend container:
    python manage.py test cins.tests.test_gcv_wheel_bifurcation -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
from cins.schema.cics_model import (
    CommissionOutcome,
    CommissionRule,
    RuleConditions,
)
from cins.schema.raw_extraction import (
    RawCommissionEntry,
    RawExtraction,
    RawProviderInfo,
)


def _make_rule(rule_id: str, priority: int, **cond_kwargs) -> CommissionRule:
    return CommissionRule(
        rule_id=rule_id,
        priority=priority,
        conditions=RuleConditions(**cond_kwargs),
        outcome=CommissionOutcome(commission_type="percentage", percentage_value=10.0),
    )


class BifurcateGCVWheelsTests(TestCase):
    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_empty_wheels_expands_to_both_categories(self):
        rules = [_make_rule("GCV_R1", 10)]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        self.assertEqual(len(result), 2)
        wheels = sorted(r.conditions.number_of_wheels[0] for r in result)
        self.assertEqual(wheels, ["3w", "4w-and-above"])

    def test_both_wheels_listed_expands_to_two_rules(self):
        rules = [_make_rule("GCV_R1", 10, number_of_wheels=["3w", "4w-and-above"])]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].conditions.number_of_wheels, ["3w"])
        self.assertEqual(result[1].conditions.number_of_wheels, ["4w-and-above"])

    def test_single_wheel_passes_through_unchanged(self):
        rules = [_make_rule("GCV_R1", 10, number_of_wheels=["3w"])]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        self.assertEqual(len(result), 1)
        self.assertIs(result[0], rules[0])

    def test_clone_inherits_priority_and_outcome(self):
        rule = _make_rule("GCV_R1", 42)
        result = self.transformer._bifurcate_gcv_wheels([rule])
        for clone in result:
            self.assertEqual(clone.priority, 42)
            self.assertEqual(clone.outcome.percentage_value, 10.0)

    def test_rule_ids_get_wheel_suffix(self):
        rules = [_make_rule("GCV_R1", 10)]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        rule_ids = sorted(r.rule_id for r in result)
        self.assertEqual(rule_ids, ["GCV_R1_3W", "GCV_R1_4WAA"])

    def test_other_conditions_preserved_on_clones(self):
        # Use the (0,0) no-band sentinel so the rule still fans to both clones
        # (a real weight grade now routes 4W-only).
        rules = [
            _make_rule(
                "GCV_R1",
                10,
                gvw_start_range=0,
                gvw_end_range=0,
                policy_type=["third-party"],
                state=["maharashtra"],
            )
        ]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        self.assertEqual(len(result), 2)
        for clone in result:
            self.assertEqual(clone.conditions.gvw_start_range, 0)
            self.assertEqual(clone.conditions.gvw_end_range, 0)
            self.assertEqual(clone.conditions.policy_type, ["third-party"])
            self.assertEqual(clone.conditions.state, ["maharashtra"])

    def test_clones_are_independent_objects(self):
        rules = [_make_rule("GCV_R1", 10, state=["maharashtra"])]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        # Mutating one clone's conditions must not bleed into the other.
        result[0].conditions.state.append("karnataka")
        self.assertEqual(result[1].conditions.state, ["maharashtra"])

    def test_rule_without_conditions_expands_with_wheel_only(self):
        rule = CommissionRule(
            rule_id="GCV_R1",
            priority=10,
            conditions=None,
            outcome=CommissionOutcome(commission_type="flat", flat_value=500.0),
        )
        result = self.transformer._bifurcate_gcv_wheels([rule])
        self.assertEqual(len(result), 2)
        wheels = sorted(r.conditions.number_of_wheels[0] for r in result)
        self.assertEqual(wheels, ["3w", "4w-and-above"])
        for clone in result:
            self.assertEqual(clone.outcome.flat_value, 500.0)

    def test_mixed_input_only_bifurcates_when_needed(self):
        rules = [
            _make_rule("R1", 10, number_of_wheels=["3w"]),
            _make_rule("R2", 20),  # empty -> bifurcate
            _make_rule("R3", 30, number_of_wheels=["4w-and-above"]),
        ]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0].rule_id, "R1")
        self.assertEqual(result[1].rule_id, "R2_3W")
        self.assertEqual(result[2].rule_id, "R2_4WAA")
        self.assertEqual(result[3].rule_id, "R3")

    def test_empty_rules_list_unchanged(self):
        result = self.transformer._bifurcate_gcv_wheels([])
        self.assertEqual(result, [])

    # --- Fix iii: GVW-aware bifurcation ---

    def test_heavy_band_wheelless_maps_to_4w_only(self):
        # A lower-bounded heavy band (start > 0) cannot apply to a 3-wheeler.
        rules = [_make_rule("GCV_R1", 10, gvw_start_range=12000, gvw_end_range=25000)]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].conditions.number_of_wheels, ["4w-and-above"])

    def test_light_band_maps_to_4w_only(self):
        # A finite "UPTO 3.5T" weight grade is a real band -> 4W-only. A goods
        # 3-wheeler (auto) is never weight-graded; the client publishes no 3W at
        # 0-3500, so cloning these into 3W produced spurious rows.
        rules = [_make_rule("GCV_R1", 10, gvw_start_range=0, gvw_end_range=3500)]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].conditions.number_of_wheels, ["4w-and-above"])

    def test_zero_zero_sentinel_fans_to_both(self):
        # (0,0) is the "no band" sentinel (not a weight grade) -> still both.
        rules = [_make_rule("GCV_R1", 10, gvw_start_range=0, gvw_end_range=0)]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        self.assertEqual(len(result), 2)

    def test_all_gvw_open_band_fans_to_both(self):
        # An open all-GVW band (huge upper bound) is NOT a weight grade; client
        # 3W auto lives here, so it must remain applicable to both categories.
        rules = [_make_rule("GCV_R1", 10, gvw_start_range=0, gvw_end_range=999999)]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        self.assertEqual(len(result), 2)

    def test_explicit_multi_wheel_band_is_respected(self):
        # Explicit number_of_wheels is authoritative even with a heavy band.
        rules = [
            _make_rule(
                "GCV_R1",
                10,
                number_of_wheels=["3w", "4w-and-above"],
                gvw_start_range=12000,
                gvw_end_range=25000,
            )
        ]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        self.assertEqual(len(result), 2)

    def test_no_spurious_3w_at_heavy_band(self):
        rules = [_make_rule("GCV_R1", 10, gvw_start_range=40000, gvw_end_range=49000)]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        self.assertTrue(
            all(r.conditions.number_of_wheels == ["4w-and-above"] for r in result)
        )

    # --- Fix iii (b): special-purpose sub-class is 4W-only ---

    def test_four_wheel_only_subclass_maps_to_4w_only(self):
        # Tractor/bus/excavator/harvester/maxicab carry no GVW band and no
        # wheel token; they must NOT clone into 3W (was the source of the
        # (0,0) catch-all with conflicting rates).
        for sub in [
            "tractor",
            "school-bus",
            "staff-bus",
            "excavator",
            "excavators",  # LLM-emitted plural must also route 4W-only
            "harvester",
            "maxicab",
        ]:
            rules = [_make_rule("GCV_R1", 10, vehicle_sub_class=[sub])]
            result = self.transformer._bifurcate_gcv_wheels(rules)
            self.assertEqual(len(result), 1, sub)
            self.assertEqual(
                result[0].conditions.number_of_wheels, ["4w-and-above"], sub
            )

    def test_explicit_wheels_override_subclass_routing(self):
        # Explicit number_of_wheels is authoritative even for a 4W-only body.
        rules = [
            _make_rule(
                "GCV_R1", 10, number_of_wheels=["3w"], vehicle_sub_class=["tractor"]
            )
        ]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].conditions.number_of_wheels, ["3w"])

    def test_non_special_subclass_still_fans_to_both(self):
        # A generic sub-class with no band stays applicable to both categories.
        rules = [_make_rule("GCV_R1", 10, vehicle_sub_class=["scooter"])]
        result = self.transformer._bifurcate_gcv_wheels(rules)
        wheels = sorted(r.conditions.number_of_wheels[0] for r in result)
        self.assertEqual(wheels, ["3w", "4w-and-above"])


class ExtractSubClassFromKeyTests(TestCase):
    """Abbreviated GCV section tokens (``1_TRAC``, ``2_EXCAVATOR``, …) must
    resolve to a vehicle_sub_class so the rows are distinguished and routed to
    4W-and-Above instead of collapsing into a 3W catch-all.
    """

    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_special_section_tokens(self):
        cases = {
            "Commercial Vehicle 1_TRAC AP ACT": ["tractor"],
            "Commercial Vehicle 1_SCHOOL AP PACK": ["school-bus"],
            "Commercial Vehicle 2_STAFF MH ACT": ["staff-bus"],
            "Commercial Vehicle 2_EXCAVATOR GJ PACK": ["excavator"],
            "Commercial Vehicle 3_HARVESTOR UP ACT": ["harvester"],
            "Commercial Vehicle 4_MAXI KA PACK": ["maxicab"],
        }
        for key, expected in cases.items():
            self.assertEqual(
                self.transformer._extract_sub_class_from_key(key), expected, key
            )

    def test_agricultural_tractor_still_specific(self):
        # The longer "agri tractor" phrase keeps its canonical slug.
        self.assertEqual(
            self.transformer._extract_sub_class_from_key("GCV agri tractor"),
            ["agricultural-tractor"],
        )

    def test_no_subclass_token_returns_none(self):
        self.assertIsNone(
            self.transformer._extract_sub_class_from_key("Commercial Vehicle 8_40T")
        )


class ExtractWheelsFromKeyTests(TestCase):
    """Wheel category baked into GCV/PCV sheet headers (e.g. ``1_GCCV_3W``)
    must be lifted onto rules so bifurcation passes them through instead of
    fanning them out to both categories.
    """

    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_underscore_slug_3w(self):
        self.assertEqual(self.transformer._extract_wheels_from_key("1_GCCV_3W"), ["3w"])

    def test_underscore_slug_4w(self):
        self.assertEqual(
            self.transformer._extract_wheels_from_key("1_GCCV_4W"),
            ["4w-and-above"],
        )

    def test_space_separated_4w_and_above(self):
        self.assertEqual(
            self.transformer._extract_wheels_from_key("GCCV 4W & ABOVE"),
            ["4w-and-above"],
        )

    def test_three_wheeler_phrase(self):
        self.assertEqual(
            self.transformer._extract_wheels_from_key("PCV Three-Wheeler"),
            ["3w"],
        )

    def test_four_wheeler_phrase(self):
        self.assertEqual(
            self.transformer._extract_wheels_from_key("GCV FOUR WHEELER"),
            ["4w-and-above"],
        )

    def test_both_wheels_in_one_key(self):
        # Some sheets bundle both 3W and 4W rows under one header.
        self.assertEqual(
            sorted(self.transformer._extract_wheels_from_key("GCCV 3W / 4W & ABOVE")),
            ["3w", "4w-and-above"],
        )

    def test_no_wheel_hint_returns_none(self):
        self.assertIsNone(self.transformer._extract_wheels_from_key("GCCV - UPTO 3.5T"))

    def test_unrelated_digits_do_not_match(self):
        # "34W" must not match 4W; "g4w" must not match either.
        self.assertIsNone(self.transformer._extract_wheels_from_key("GCCV 34W BUCKET"))
        self.assertIsNone(self.transformer._extract_wheels_from_key("g4w_thing"))

    def test_empty_key_returns_none(self):
        self.assertIsNone(self.transformer._extract_wheels_from_key(""))


class TransformProductWheelBackfillTests(TestCase):
    """End-to-end check: wheel hints baked into the sheet name
    (``source_section``) must be lifted onto rules even when the LLM
    normalised ``product_name`` to bare ``"Commercial Vehicle"``.
    """

    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def _make_raw(self, entries, global_vehicle_type_text=""):
        return RawExtraction(
            source_file="test.xlsx",
            source_type="excel",
            provider=RawProviderInfo(name="Test Insurer"),
            start_date="2024-01-01",
            end_date="2024-03-31",
            commission_entries=entries,
            global_vehicle_type_text=global_vehicle_type_text,
        )

    def test_source_section_slug_supplies_wheels(self):
        # Mimics the d81925b1 intake: LLM emitted product_name="Commercial
        # Vehicle" with the wheel hint surviving only in source_section.
        entry = RawCommissionEntry(
            rule_text="GCCV 3W comprehensive",
            commission_value="10%",
            product_name="Commercial Vehicle",
            product_type="GCV",
            coverage_type_text="comprehensive",
            source_section="1_GCCV_3W",
        )
        raw = self._make_raw([entry])
        product = self.transformer._transform_product(
            "Commercial Vehicle", [entry], None, raw
        )
        self.assertEqual(len(product.commission_rules), 1)
        self.assertEqual(
            product.commission_rules[0].conditions.number_of_wheels, ["3w"]
        )

    def test_product_key_slug_supplies_wheels(self):
        # When LLM did NOT canonicalise product_name and kept the slug.
        entry = RawCommissionEntry(
            rule_text="GCCV 4W & ABOVE",
            commission_value="12%",
            product_name="1_GCCV_4W",
            product_type="GCV",
        )
        raw = self._make_raw([entry])
        product = self.transformer._transform_product("1_GCCV_4W", [entry], None, raw)
        self.assertEqual(len(product.commission_rules), 1)
        self.assertEqual(
            product.commission_rules[0].conditions.number_of_wheels,
            ["4w-and-above"],
        )

    def test_no_wheel_hint_anywhere_bifurcates_to_both(self):
        # Pure "GCCV" sheet with no wheel category named → bifurcation
        # downstream fans out into 2 rules.
        entry = RawCommissionEntry(
            rule_text="GCCV comprehensive",
            commission_value="10%",
            product_name="Commercial Vehicle",
            product_type="GCV",
            source_section="1_GCCV",
        )
        raw = self._make_raw([entry])
        product = self.transformer._transform_product(
            "Commercial Vehicle", [entry], None, raw
        )
        self.assertEqual(len(product.commission_rules), 2)
        wheels = sorted(
            r.conditions.number_of_wheels[0] for r in product.commission_rules
        )
        self.assertEqual(wheels, ["3w", "4w-and-above"])

    def test_private_car_4w_not_treated_as_wheel_count(self):
        # "4W" inside a Private Car key must NOT populate number_of_wheels.
        entry = RawCommissionEntry(
            rule_text="Private Car 4W comprehensive",
            commission_value="15%",
            product_name="Private Car",
            product_type="4W",
            source_section="Private_Car_4W",
        )
        raw = self._make_raw([entry])
        product = self.transformer._transform_product("Private Car", [entry], None, raw)
        self.assertEqual(len(product.commission_rules), 1)
        self.assertIsNone(product.commission_rules[0].conditions.number_of_wheels)

    def test_global_vehicle_type_text_fallback(self):
        # No slug on product_key or section, but global_vehicle_type_text
        # mentions "3W".
        entry = RawCommissionEntry(
            rule_text="GCV comprehensive",
            commission_value="10%",
            product_name="Commercial Vehicle",
            product_type="GCV",
        )
        raw = self._make_raw([entry], global_vehicle_type_text="GCV 3W")
        product = self.transformer._transform_product(
            "Commercial Vehicle", [entry], None, raw
        )
        self.assertEqual(len(product.commission_rules), 1)
        self.assertEqual(
            product.commission_rules[0].conditions.number_of_wheels, ["3w"]
        )

    def test_per_entry_section_beats_product_key(self):
        # Two entries from different sheets within the same product group.
        entries = [
            RawCommissionEntry(
                rule_text="GCCV 3W rule",
                commission_value="10%",
                product_name="Commercial Vehicle",
                product_type="GCV",
                source_section="1_GCCV_3W",
            ),
            RawCommissionEntry(
                rule_text="GCCV 4W rule",
                commission_value="12%",
                product_name="Commercial Vehicle",
                product_type="GCV",
                source_section="2_GCCV_4W",
            ),
        ]
        raw = self._make_raw(entries)
        product = self.transformer._transform_product(
            "Commercial Vehicle", entries, None, raw
        )
        self.assertEqual(len(product.commission_rules), 2)
        by_text = {
            r.rule_description: r.conditions.number_of_wheels
            for r in product.commission_rules
        }
        self.assertEqual(by_text["GCCV 3W rule"], ["3w"])
        self.assertEqual(by_text["GCCV 4W rule"], ["4w-and-above"])

    def test_existing_wheels_not_overwritten(self):
        # LLM already populated number_of_wheels — extractor must not clobber.
        entry = RawCommissionEntry(
            rule_text="GCCV 3W",
            commission_value="10%",
            product_name="Commercial Vehicle",
            product_type="GCV",
            source_section="1_GCCV_4W",  # slug says 4W
            conditions={"number_of_wheels": "3W"},  # LLM says 3W
        )
        raw = self._make_raw([entry])
        product = self.transformer._transform_product(
            "Commercial Vehicle", [entry], None, raw
        )
        self.assertEqual(len(product.commission_rules), 1)
        self.assertEqual(
            product.commission_rules[0].conditions.number_of_wheels, ["3w"]
        )

    # --- Fix iii: section token survives only in rule_text ---

    def test_rule_text_3w_auto_supplies_wheels_and_stays_3w(self):
        # Mimics intake 4e8bdc78: token "1_3W_AUTO" lives in rule_text, not
        # source_section. Must lift 3W and bifurcation must NOT clone a 4W row.
        entry = RawCommissionEntry(
            rule_text="Commercial Vehicle 1_3W_AUTO AP/TS ACT",
            commission_value="10%",
            product_name="Commercial Vehicle",
            product_type="GCV",
        )
        raw = self._make_raw([entry])
        product = self.transformer._transform_product(
            "Commercial Vehicle", [entry], None, raw
        )
        self.assertEqual(len(product.commission_rules), 1)
        self.assertEqual(
            product.commission_rules[0].conditions.number_of_wheels, ["3w"]
        )

    def test_rule_text_tractor_routes_to_4w_only(self):
        # "1_TRAC" in rule_text resolves the tractor body, which routes the rule
        # 4W-only — and is then STRIPPED, because GCV publishes no body type.
        # This is the regression net for that ordering: strip before
        # _bifurcate_gcv_wheels and the wheel-less tractor falls through to the
        # both-wheels arm and grows a spurious 3W clone.
        entry = RawCommissionEntry(
            rule_text="Commercial Vehicle 1_TRAC GJ PACK",
            commission_value="20%",
            product_name="Commercial Vehicle",
            product_type="GCV",
        )
        raw = self._make_raw([entry])
        product = self.transformer._transform_product(
            "Commercial Vehicle", [entry], None, raw
        )
        self.assertEqual(len(product.commission_rules), 1)
        rule = product.commission_rules[0]
        self.assertIsNone(rule.conditions.vehicle_sub_class)
        self.assertEqual(rule.conditions.number_of_wheels, ["4w-and-above"])

    def test_rule_text_excavator_no_3w_clone(self):
        entry = RawCommissionEntry(
            rule_text="Commercial Vehicle 2_EXCAVATOR MH ACT",
            commission_value="15%",
            product_name="Commercial Vehicle",
            product_type="GCV",
        )
        raw = self._make_raw([entry])
        product = self.transformer._transform_product(
            "Commercial Vehicle", [entry], None, raw
        )
        self.assertEqual(len(product.commission_rules), 1)
        self.assertEqual(
            product.commission_rules[0].conditions.number_of_wheels, ["4w-and-above"]
        )
