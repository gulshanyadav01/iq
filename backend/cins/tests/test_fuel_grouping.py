"""Tests for carrier-scoped fuel grouping (Fix i) and SAOD tagging (Fix iv).

Fix i: for Chola, CNG/LPG follow the Petrol grid, not the Diesel grid. Electric
follows the Petrol grid ONLY for Private Car (its grid note says EV = Petrol
grid); Commercial Vehicle / Two Wheeler publish a separate electric column, so
electric is NOT folded into their petrol grid:
    [P] petrol     -> {petrol, cng, lpg}          (+ electric for Private Car)
    [D] non-petrol -> {diesel}
Other carriers keep the historical non-petrol -> {diesel, cng, lpg} expansion
and do not expand a bare petrol grid.

Fix iv: a Standalone-OD section tagged coverage_type_text="SAOD" must normalise
to policy_type ["saod"], distinct from comprehensive.

Run from the backend container:
    python manage.py test cins.tests.test_fuel_grouping -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import (
    POLICY_TYPE_MAPPINGS,
    RawToCICSTransformer,
)


class FuelGroupingTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def _expand(self, fuels, carrier, product_name=None):
        self.t._carrier_code = carrier
        return self.t._expand_fuel_group(fuels, product_name)

    def test_chola_petrol_commercial_excludes_electric(self):
        # CV / default: petrol grid = petrol + cng + lpg, NO electric (electric
        # is a separate commercial deal column).
        self.assertEqual(
            self._expand(["petrol"], "Chola"),
            ["cng", "lpg", "petrol"],
        )
        self.assertEqual(
            self._expand(["petrol"], "Chola", "Commercial Vehicle"),
            ["cng", "lpg", "petrol"],
        )

    def test_chola_petrol_private_car_includes_electric(self):
        # Private Car: EV follows the petrol grid → electric included.
        self.assertEqual(
            self._expand(["petrol"], "Chola", "Private Car"),
            ["cng", "electric", "lpg", "petrol"],
        )

    def test_chola_diesel_grid_is_diesel_only(self):
        self.assertEqual(self._expand(["non-petrol"], "Chola"), ["diesel"])

    def test_chola_expansion_idempotent(self):
        once = self._expand(["petrol"], "Chola")
        twice = self._expand(once, "Chola")
        self.assertEqual(once, twice)

    def test_other_carrier_non_petrol_keeps_three_fuels(self):
        self.assertEqual(
            self._expand(["non-petrol"], "ICICI"),
            ["cng", "diesel", "lpg"],
        )

    def test_other_carrier_petrol_unchanged(self):
        self.assertEqual(self._expand(["petrol"], "ICICI"), ["petrol"])

    def test_empty_passthrough(self):
        self.assertEqual(self._expand(None, "Chola"), None)
        self.assertEqual(self._expand([], "Chola"), [])


class SAODMappingTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_saod_aliases_map_to_saod(self):
        for label in ("SAOD", "Standalone OD", "Stand Alone OD", "SOD"):
            self.assertEqual(
                self.t._normalize_list_field(
                    label, POLICY_TYPE_MAPPINGS, "policy_type"
                ),
                ["saod"],
                msg=f"{label!r} should normalise to saod",
            )

    def test_comprehensive_not_saod(self):
        self.assertEqual(
            self.t._normalize_list_field(
                "Comprehensive", POLICY_TYPE_MAPPINGS, "policy_type"
            ),
            ["comprehensive"],
        )
