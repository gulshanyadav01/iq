"""
Tests for DEAL_PARAMETER_MAPPINGS.

`deal_parameter` is normalised from `applied_on_text` (the payout basis). Chola
publishes the liability basis as "TP/PA Premium" on both the Two Wheeler and
Private Car grids, and once as the looser "Total Premium"; neither had a mapping
key, so doc 733711bd emitted 180 `Unknown deal_parameter` warnings and dropped
the basis on every ACT rule.

Run from the backend container:
    python manage.py test cins.tests.test_deal_parameter -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import (
    DEAL_PARAMETER_MAPPINGS,
    RawToCICSTransformer,
)


class DealParameterMappingTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def _normalize(self, text):
        return self.t._normalize_list_field(
            text, DEAL_PARAMETER_MAPPINGS, "deal_parameter"
        )

    def test_tp_pa_premium_maps_to_regular_tp(self):
        self.assertEqual(self._normalize("TP/PA Premium"), ["regular-tp"])
        self.assertEqual(self.t.warnings, [])

    def test_total_premium_maps_to_regular_tp(self):
        self.assertEqual(self._normalize("Total Premium"), ["regular-tp"])
        self.assertEqual(self.t.warnings, [])

    def test_bare_tp_pa_maps_to_regular_tp(self):
        self.assertEqual(self._normalize("TP/PA"), ["regular-tp"])

    def test_case_and_spacing_insensitive(self):
        for text in ("tp/pa premium", "  TP/PA PREMIUM  ", "Tp/Pa Premium"):
            with self.subTest(text=text):
                self.assertEqual(self._normalize(text), ["regular-tp"])

    def test_net_premium_still_maps_to_net(self):
        # The package basis must not be dragged into regular-tp.
        self.assertEqual(self._normalize("Net Premium"), ["net"])

    def test_od_cover_premium_still_maps_to_od(self):
        self.assertEqual(self._normalize("OD Cover Premium"), ["od"])

    def test_regular_tp_unchanged(self):
        self.assertEqual(self._normalize("Regular TP"), ["regular-tp"])

    def test_tp_with_vehicle_age_not_shadowed(self):
        # The longer key must win over the new "tp/pa" entry.
        self.assertEqual(
            self._normalize("TP with vehicle age"), ["tp-with-vehicle-age"]
        )

    def test_unknown_basis_still_warns(self):
        self.assertIsNone(self._normalize("Moon Premium"))
        self.assertTrue(
            any("Unknown deal_parameter" in w for w in self.t.warnings),
            self.t.warnings,
        )

    def test_mapped_values_are_all_in_schema_enum(self):
        # commission_schema_v1.json / cics_model.py allow only these.
        allowed = {
            "od",
            "net",
            "regular-tp",
            "tp-with-vehicle-age",
            "flat-amount",
            "ape",
        }
        self.assertTrue(set(DEAL_PARAMETER_MAPPINGS.values()) <= allowed)
