"""
Chola CV segment slugs → vehicle_sub_class.

Codegen writes the grid's segment label verbatim into
`conditions["vehicle_sub_class"]`, e.g. "1_GCCV_3W", "1_TRAC[RENEWAL]",
"3_HARVESTOR". Two problems followed on doc 733711bd (671 warnings):

1. `_normalize_list_field` word-boundary-matches keys of <=5 chars against
   `[\\s,;/\\-]` only, so an underscore-joined slug could never match a short key
   like "trac" — and "HARVESTOR" is a source misspelling of "Harvester".
2. The GVW ladder ("2_UPTO_3.5T") and the 4W taxi CC split ("2_4W_LT_1500CC")
   are enumerated as segment labels too. They are dimensions, not body types —
   `_recover_gvw_range` / `_recover_cc_range` already harvest them — but they
   were re-read as sub-classes and emitted ~223 bogus warnings per document.

Run from the backend container:
    python manage.py test cins.tests.test_cv_subclass_slugs -v 2
"""

import json
import os
import tempfile
from unittest import TestCase, mock

from cins.processors.core.raw_to_cics_transformer import (
    VEHICLE_SUB_CLASS_MAPPINGS,
    RawToCICSTransformer,
)
from cins.schema.cics_model import CommissionOutcome, CommissionRule, RuleConditions
from cins.validators.rule_overlap_checker import RuleOverlapChecker


class SubclassSlugNormalizationTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def test_strips_index_prefix(self):
        self.assertEqual(self.t._normalize_subclass_slug("3_HARVESTOR"), "HARVESTOR")

    def test_strips_bracket_qualifier(self):
        self.assertEqual(self.t._normalize_subclass_slug("1_TRAC[RENEWAL]"), "TRAC")
        self.assertEqual(self.t._normalize_subclass_slug("1_TRAC[NEW]"), "TRAC")

    def test_unpacks_underscores(self):
        self.assertEqual(self.t._normalize_subclass_slug("1_GCCV_3W"), "GCCV 3W")

    def test_plain_text_passes_through(self):
        self.assertEqual(self.t._normalize_subclass_slug("school bus"), "school bus")

    def test_none_and_list_inputs(self):
        self.assertIsNone(self.t._normalize_subclass_slug(None))
        self.assertEqual(self.t._normalize_subclass_slug(["1_TRAC[NEW]"]), "TRAC")


class SubclassSlugMappingTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def _map(self, slug):
        self.t.warnings = []
        return self.t._normalize_list_field(
            self.t._normalize_subclass_slug(slug),
            VEHICLE_SUB_CLASS_MAPPINGS,
            "vehicle_sub_class",
        )

    def test_gccv_3w_maps_to_auto(self):
        self.assertEqual(self._map("1_GCCV_3W"), ["auto-rickshaw"])
        self.assertEqual(self.t.warnings, [])

    def test_gccv_3w_electric_maps_to_auto(self):
        self.assertEqual(self._map("1_GCCV_3W[Electric]"), ["auto-rickshaw"])

    def test_3w_auto_maps_to_auto(self):
        self.assertEqual(self._map("1_3W_AUTO"), ["auto-rickshaw"])

    def test_trac_maps_to_tractor(self):
        self.assertEqual(self._map("1_TRAC[RENEWAL]"), ["tractor"])
        self.assertEqual(self._map("1_TRAC[NEW]"), ["tractor"])

    def test_harvestor_misspelling_maps_to_harvester(self):
        self.assertEqual(self._map("3_HARVESTOR"), ["harvester"])

    def test_existing_slug_forms_still_work(self):
        self.assertEqual(self._map("2_EXCAVATOR"), ["excavators"])

    def test_genuinely_unknown_body_still_returns_none(self):
        self.assertIsNone(self._map("4_FLYING_MACHINE"))


class DimensionSlugSuppressionTests(TestCase):
    """Weight/CC band slugs must not be read as sub-classes — or warned about."""

    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def test_gvw_band_slugs_are_dimensions(self):
        for slug in (
            "2_UPTO_3.5T",
            "3_3.5T_TO_7.5T",
            "4_7.5T_TO_12T",
            "5_12T_TO_16T",
            "6_16T_TO_20T",
            "7_20T_TO_40T",
            "8_40T_TO_43T",
            "9_43T_TO_47T",
        ):
            with self.subTest(slug=slug):
                self.assertTrue(self.t._subclass_slug_is_dimension(slug))

    def test_cc_band_slugs_are_dimensions(self):
        for slug in ("2_4W_LT_1500CC", "3_4W_GT_1500CC"):
            with self.subTest(slug=slug):
                self.assertTrue(self.t._subclass_slug_is_dimension(slug))

    def test_body_slugs_are_not_dimensions(self):
        # "1_GCCV_3W" contains a digit but is a body slug — the guard must not
        # swallow it just because _gvw_slug_or_empty sees numerals.
        for slug in (
            "1_GCCV_3W",
            "1_3W_AUTO",
            "1_TRAC[RENEWAL]",
            "3_HARVESTOR",
            "2_EXCAVATOR",
            "school bus",
        ):
            with self.subTest(slug=slug):
                self.assertFalse(self.t._subclass_slug_is_dimension(slug))

    def test_empty_input_is_not_a_dimension(self):
        for value in (None, "", []):
            self.assertFalse(self.t._subclass_slug_is_dimension(value))

    def test_dimension_probe_leaves_no_warning_behind(self):
        # _normalize_list_field_quiet must roll back the warning it triggers.
        self.t.warnings = []
        self.t._subclass_slug_is_dimension("7_20T_TO_40T")
        self.assertEqual(self.t.warnings, [])


class GCVSubclassStrippedTests(TestCase):
    """The GCCV grid's SUBCLASS column holds the weight ladder, not a body type.

        | GCCV | 2_UPTO_3.5T    | TATA/ Maruti/ Mahindra | Ace, Super Ace, … |
        | GCCV | 3_3.5T_TO_7.5T | 3_3.5T_TO_7.5T         | 3_3.5T_TO_7.5T    |

    There is no body class in the source at all, so GCV carries none. The strip
    is ABSOLUTE (a body resolved from a slug goes too) and carrier-agnostic —
    goods-carrying grids price by weight everywhere, this is not a Chola quirk.
    """

    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    @staticmethod
    def _rule(sub_class):
        return CommissionRule(
            rule_id="R1",
            priority=1,
            conditions=RuleConditions(vehicle_sub_class=sub_class),
            outcome=CommissionOutcome(
                commission_type="percentage", percentage_value=10.0
            ),
        )

    def test_strips_a_resolved_body(self):
        # The 3-wheeler rows resolve auto-rickshaw from "1_GCCV_3W" — it goes.
        rules = [self._rule(["auto-rickshaw"]), self._rule(["tipper"])]
        self.assertEqual(self.t._strip_gcv_subclass(rules, "commercial-gcv"), 2)
        for rule in rules:
            self.assertIsNone(rule.conditions.vehicle_sub_class)

    def test_strips_the_not_available_sentinel(self):
        rules = [self._rule(["not-available"])]
        self.assertEqual(self.t._strip_gcv_subclass(rules, "commercial-gcv"), 1)
        self.assertIsNone(rules[0].conditions.vehicle_sub_class)

    def test_the_body_word_survives_as_rule_id_provenance(self):
        # RowCoverageValidator's declined-row check (a HIGH, which blocks the
        # output-cache pin) reads the rule's identity fields — the body word has
        # to stay reachable there once the condition is gone.
        rules = [self._rule(["others"])]
        self.t._strip_gcv_subclass(rules, "commercial-gcv")
        self.assertEqual(rules[0].rule_id, "R1_SC_others")

    def test_rule_id_suffix_stays_schema_legal(self):
        rules = [self._rule(["pick up/delivery van", "not-available"])]
        self.t._strip_gcv_subclass(rules, "commercial-gcv")
        # rule_id's schema pattern is ^[A-Za-z0-9_-]+$ — the space and slash
        # in the source spelling must not leak through.
        self.assertRegex(rules[0].rule_id, r"^[A-Za-z0-9_-]+$")
        CommissionRule(
            rule_id=rules[0].rule_id,
            priority=1,
            outcome=CommissionOutcome(commission_type="percentage"),
        )

    def test_already_empty_is_not_counted(self):
        rules = [self._rule(None), self._rule([])]
        self.assertEqual(self.t._strip_gcv_subclass(rules, "commercial-gcv"), 0)
        self.assertEqual(self.t.notices, [])

    def test_other_subproducts_untouched(self):
        for sub in ("commercial-pcv", "commercial-miscd", "car", "two-wheeler"):
            with self.subTest(sub=sub):
                rules = [self._rule(["taxi"])]
                self.assertEqual(self.t._strip_gcv_subclass(rules, sub), 0)
                self.assertEqual(rules[0].conditions.vehicle_sub_class, ["taxi"])

    def test_applies_to_every_carrier(self):
        # Carrier-agnostic on purpose: a goods-carrying grid prices by weight
        # regardless of who published it.
        self.t._carrier_code = "ICICI"
        rules = [self._rule(["tipper"])]
        self.assertEqual(self.t._strip_gcv_subclass(rules, "commercial-gcv"), 1)
        self.assertIsNone(rules[0].conditions.vehicle_sub_class)

    def test_env_flag_off_restores_the_old_behaviour(self):
        rules = [self._rule(["tipper"])]
        with mock.patch.dict(os.environ, {"CINS_GCV_NO_SUBCLASS": "false"}):
            self.assertEqual(self.t._strip_gcv_subclass(rules, "commercial-gcv"), 0)
        self.assertEqual(rules[0].conditions.vehicle_sub_class, ["tipper"])

    def test_not_available_is_a_valid_enum_value(self):
        # Still a real value for the OTHER CV sub-products (PCV / MISC-D).
        RuleConditions(vehicle_sub_class=["not-available"])


class GCVStripCollapseTests(TestCase):
    """Stripping the field can make subclass-only clones byte-identical.

    ``_bifurcate_subclass_buckets`` fans a body enumeration out into N rules
    differing ONLY in ``vehicle_sub_class``; remove that field and they collide.
    ``_transform_product`` therefore re-runs ``_collapse_duplicate_rules`` after
    the strip — which merges only when the OUTCOME matches too.
    """

    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    @staticmethod
    def _rule(rule_id, sub_class, pct):
        return CommissionRule(
            rule_id=rule_id,
            priority=1,
            conditions=RuleConditions(
                vehicle_sub_class=sub_class,
                state=["maharashtra"],
                number_of_wheels=["4w-and-above"],
            ),
            outcome=CommissionOutcome(
                commission_type="percentage",
                percentage_value=pct,
                applied_on="Net Premium",
            ),
        )

    def test_same_rate_clones_collapse_to_one(self):
        rules = [
            self._rule("R1", ["truck"], 10.0),
            self._rule("R2", ["tipper"], 10.0),
        ]
        self.assertEqual(self.t._strip_gcv_subclass(rules, "commercial-gcv"), 2)
        collapsed = self.t._collapse_duplicate_rules(rules)
        self.assertEqual(len(collapsed), 1)

    def test_different_rate_clones_both_survive(self):
        # The honest cost of stripping absolutely: the only dimension that told
        # these two apart is gone, so they stay as a genuine conflict for
        # RuleOverlapChecker to report rather than being silently merged.
        rules = [
            self._rule("R1", ["truck"], 10.0),
            self._rule("R2", ["tipper"], 12.5),
        ]
        self.t._strip_gcv_subclass(rules, "commercial-gcv")
        collapsed = self.t._collapse_duplicate_rules(rules)
        self.assertEqual(len(collapsed), 2)

    def test_overlap_checker_reports_the_surviving_pair_as_critical(self):
        rules = [
            self._rule("R1", ["truck"], 10.0),
            self._rule("R2", ["tipper"], 12.5),
        ]
        self.t._strip_gcv_subclass(rules, "commercial-gcv")
        rules = self.t._collapse_duplicate_rules(rules)

        doc = {
            "products": [
                {
                    "product_code": "GCV",
                    "commission_rules": [
                        r.model_dump(exclude_none=True) for r in rules
                    ],
                }
            ]
        }
        fd = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(doc, fd)
        fd.close()

        result = RuleOverlapChecker().check(fd.name)
        self.assertTrue(result.has_critical)


class SubproductSubclassOverrideTests(TestCase):
    """A body word can name a different vehicle in a different sub-product.

    Chola writes the MISC-D agricultural tractor as the segment slug "1_TRAC".
    The shared VEHICLE_SUB_CLASS_MAPPINGS table maps the bare token "trac" to
    "tractor", which the carrier's own hierarchy master files under GCV as a
    goods body — a different vehicle at a different payout from MISC-D's
    "Agricultural Tractor". Reconciled against prod deal 799822 (MISC-D / Third
    Party / Bihar), which reads "Agricultural Tractor".
    """

    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def test_miscd_trac_becomes_agricultural_tractor(self):
        self.t._product_subproduct = "commercial-miscd"
        self.assertEqual(
            self.t._apply_subproduct_subclass_override(["tractor"]),
            ["agricultural-tractor"],
        )

    def test_gcv_trac_stays_the_goods_body(self):
        self.t._product_subproduct = "commercial-gcv"
        self.assertEqual(
            self.t._apply_subproduct_subclass_override(["tractor"]), ["tractor"]
        )

    def test_override_never_widens_an_unmapped_value(self):
        self.t._product_subproduct = "commercial-miscd"
        self.assertEqual(
            self.t._apply_subproduct_subclass_override(["tipper", "crane"]),
            ["tipper", "crane"],
        )

    def test_no_subproduct_is_a_noop(self):
        self.t._product_subproduct = ""
        self.assertEqual(
            self.t._apply_subproduct_subclass_override(["tractor"]), ["tractor"]
        )
        self.assertIsNone(self.t._apply_subproduct_subclass_override(None))

    def test_key_hint_route_honours_the_override(self):
        # The product-key hint table is the other route a sub-class arrives by.
        self.t._product_subproduct = "commercial-miscd"
        self.assertEqual(
            self.t._extract_sub_class_from_key("MSV 1_TRAC BH ACT"),
            ["agricultural-tractor"],
        )
        self.t._product_subproduct = "commercial-gcv"
        self.assertEqual(
            self.t._extract_sub_class_from_key("Commercial Vehicle 1_TRAC AP ACT"),
            ["tractor"],
        )

    def test_agricultural_tractor_is_a_valid_enum_value(self):
        from cins.schema.cics_model import RuleConditions

        RuleConditions(vehicle_sub_class=["agricultural-tractor"])
