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

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import (
    VEHICLE_SUB_CLASS_MAPPINGS,
    RawToCICSTransformer,
)


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


class GCVUnspecifiedSubclassTests(TestCase):
    """The GCCV grid's SUBCLASS column holds the weight ladder, not a body type.

        | GCCV | 2_UPTO_3.5T    | TATA/ Maruti/ Mahindra | Ace, Super Ace, … |
        | GCCV | 3_3.5T_TO_7.5T | 3_3.5T_TO_7.5T         | 3_3.5T_TO_7.5T    |

    There is no body class in the source, so record Chola's own "Not Available"
    rather than leaving the mandatory field null (400 rules on doc 733711bd).
    """

    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"
        self.t._product_subproduct = "commercial-gcv"

    def test_empty_subclass_defaults_to_not_available(self):
        self.assertEqual(self.t._apply_chola_gcv_subclass(None), ["not-available"])
        self.assertEqual(self.t._apply_chola_gcv_subclass([]), ["not-available"])

    def test_resolved_body_is_never_overwritten(self):
        # The 3-wheeler rows resolve auto-rickshaw from "1_GCCV_3W" and keep it.
        self.assertEqual(
            self.t._apply_chola_gcv_subclass(["auto-rickshaw"]), ["auto-rickshaw"]
        )
        self.assertEqual(self.t._apply_chola_gcv_subclass(["tipper"]), ["tipper"])

    def test_other_subproducts_untouched(self):
        for sub in ("commercial-pcv", "commercial-misc-d", "car", "two-wheeler"):
            with self.subTest(sub=sub):
                self.t._product_subproduct = sub
                self.assertIsNone(self.t._apply_chola_gcv_subclass(None))

    def test_other_carriers_untouched(self):
        self.t._carrier_code = "ICICI"
        self.assertIsNone(self.t._apply_chola_gcv_subclass(None))

    def test_not_available_is_a_valid_enum_value(self):
        from cins.schema.cics_model import RuleConditions

        RuleConditions(vehicle_sub_class=["not-available"])
