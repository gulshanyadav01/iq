"""
Recovery of the coverage type from a mis-split composite state header.

Chola grids publish the coverage as a sub-column of the state header
("UP- East > ACT"). The Pass-3 recipe split it with `rsplit(">", 1)`, which is
correct for that two-segment Private Car form but lands one segment right on the
commercial-vehicle grids, whose header cells carry the rate columns too
("AP/TS > ACT > 0.325"): `state_text` kept "AP/TS > ACT" and
`coverage_type_text` got the rate. `policy_type` then normalised to None on 940
of 981 CV rules, collapsing the ACT/PACK distinction into 134 pairs of rules with
identical conditions and different payouts (observed on doc 733711bd).

`_recover_composite_coverage` repairs it deterministically at transform time, so
already-extracted documents recover via re-transform without another LLM call.

Run from the backend container:
    python manage.py test cins.tests.test_chola_coverage_recovery -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
from cins.schema.raw_extraction import RawCommissionEntry


def _entry(coverage_type_text, state_text):
    return RawCommissionEntry(
        rule_text="GCCV 2_UPTO_3.5T",
        coverage_type_text=coverage_type_text,
        state_text=state_text,
        commission_value="42.5%",
    )


class CompositeCoverageRecoveryTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def _recover(self, coverage, state):
        return self.t._recover_composite_coverage(_entry(coverage, state))

    def test_three_segment_header_recovers_act(self):
        self.assertEqual(self._recover("0.325", "AP/TS > ACT"), "ACT")
        self.assertTrue(
            any("Coverage recovered" in n for n in self.t.notices), self.t.notices
        )
        self.assertEqual(self.t.warnings, [])

    def test_three_segment_header_recovers_pack(self):
        self.assertEqual(self._recover("0.375", "AP/TS > PACK"), "PACK")

    def test_four_segment_header_peels_every_trailing_rate(self):
        # "BH/JH > PACK > 0.37 > 0.47" shifts twice: state_text itself ends in a
        # rate. Observed 42 times on doc 733711bd.
        self.assertEqual(self._recover("0.47", "BH/JH > PACK > 0.37"), "PACK")

    def test_recovered_token_normalizes_to_policy_type(self):
        from cins.processors.core.raw_to_cics_transformer import POLICY_TYPE_MAPPINGS

        for state, expected in (
            ("AP/TS > ACT", ["third-party"]),
            ("AP/TS > PACK", ["comprehensive"]),
        ):
            recovered = self._recover("0.325", state)
            self.assertEqual(
                self.t._normalize_list_field(
                    recovered, POLICY_TYPE_MAPPINGS, "policy_type"
                ),
                expected,
            )

    def test_two_segment_private_car_header_untouched(self):
        # The PC path splits correctly, so coverage_type_text is already "ACT"
        # and there is nothing to recover.
        self.assertEqual(self._recover("ACT", "UP- East"), "ACT")
        self.assertEqual(self.t.notices, [])

    def test_numeric_coverage_without_composite_state_still_warns(self):
        # No ">" to recover from — leave it numeric so _normalize_list_field
        # raises the real "Unknown policy_type" warning rather than hiding it.
        self.assertEqual(self._recover("0.325", "AP/TS"), "0.325")
        self.assertEqual(self.t.notices, [])

    def test_all_numeric_composite_is_not_recovered(self):
        # Nothing non-numeric left after peeling → not a coverage header.
        self.assertEqual(self._recover("0.325", "0.2 > 0.37"), "0.325")
        self.assertEqual(self.t.notices, [])

    def test_non_numeric_coverage_is_passed_through(self):
        self.assertEqual(self._recover("Comprehensive", "AP/TS > ACT"), "Comprehensive")
        self.assertEqual(self.t.notices, [])

    def test_empty_coverage_is_passed_through(self):
        self.assertEqual(self._recover("", "AP/TS > ACT"), "")

    def test_other_carriers_unaffected(self):
        self.t._carrier_code = "ICICI"
        self.assertEqual(self._recover("0.325", "AP/TS > ACT"), "0.325")
        self.assertEqual(self.t.notices, [])
