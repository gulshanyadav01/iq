"""
Regression tests for RawToCICSTransformer._dedupe_range_overlaps.

Source documents express GVW/CC/seating-capacity buckets with shared
boundaries ("UPTO 3.5T" then "3.5T TO 7.5T"). The parser maps both
labels to integer 3500, so the lower bucket's end_range and the upper
bucket's start_range collide. Downstream rule matching treats both
ends as inclusive, so a vehicle at exactly the boundary matches two
rules. The post-pass must bump the upper bucket's start to prev_end+1.

Run from the backend container:
    python manage.py test cins.tests.test_range_dedupe -v 2
"""

from types import SimpleNamespace
from unittest import TestCase

from cins.processors.core.code_gen_executor import _SANDBOX_HELPERS
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


class DedupeGVWBoundariesTests(TestCase):
    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_gvw_shared_boundary_bumps_next_start(self):
        rules = [
            _make_rule("R1", 10, gvw_start_range=0, gvw_end_range=3500),
            _make_rule("R2", 20, gvw_start_range=3500, gvw_end_range=7500),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        self.assertEqual(rules[0].conditions.gvw_end_range, 3500)
        self.assertEqual(rules[1].conditions.gvw_start_range, 3501)
        self.assertEqual(rules[1].conditions.gvw_end_range, 7500)

    def test_gvw_three_bucket_chain(self):
        rules = [
            _make_rule("R1", 10, gvw_start_range=0, gvw_end_range=3500),
            _make_rule("R2", 20, gvw_start_range=3500, gvw_end_range=7500),
            _make_rule("R3", 30, gvw_start_range=7500, gvw_end_range=12000),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        starts = [r.conditions.gvw_start_range for r in rules]
        ends = [r.conditions.gvw_end_range for r in rules]
        self.assertEqual(starts, [0, 3501, 7501])
        self.assertEqual(ends, [3500, 7500, 12000])

    def test_gvw_no_overlap_unchanged(self):
        rules = [
            _make_rule("R1", 10, gvw_start_range=0, gvw_end_range=3500),
            _make_rule("R2", 20, gvw_start_range=3501, gvw_end_range=7500),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        self.assertEqual(rules[0].conditions.gvw_end_range, 3500)
        self.assertEqual(rules[1].conditions.gvw_start_range, 3501)

    def test_gvw_input_order_independent(self):
        # Reverse-ordered input must still produce monotonic result.
        rules = [
            _make_rule("R2", 20, gvw_start_range=3500, gvw_end_range=7500),
            _make_rule("R1", 10, gvw_start_range=0, gvw_end_range=3500),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        by_id = {r.rule_id: r for r in rules}
        self.assertEqual(by_id["R1"].conditions.gvw_end_range, 3500)
        self.assertEqual(by_id["R2"].conditions.gvw_start_range, 3501)


class DedupeCCBoundariesTests(TestCase):
    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_cc_shared_boundary_bumps_next_start(self):
        rules = [
            _make_rule("R1", 10, cc_start_range=0, cc_end_range=1000),
            _make_rule("R2", 20, cc_start_range=1000, cc_end_range=1500),
            _make_rule("R3", 30, cc_start_range=1500, cc_end_range=99999),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        starts = [r.conditions.cc_start_range for r in rules]
        ends = [r.conditions.cc_end_range for r in rules]
        self.assertEqual(starts, [0, 1001, 1501])
        self.assertEqual(ends, [1000, 1500, 99999])


class DedupeSeatingBoundariesTests(TestCase):
    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_seating_capacity_shared_boundary(self):
        rules = [
            _make_rule(
                "R1",
                10,
                seating_capacity_start_range=0,
                seating_capacity_end_range=18,
            ),
            _make_rule(
                "R2",
                20,
                seating_capacity_start_range=18,
                seating_capacity_end_range=36,
            ),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        self.assertEqual(rules[1].conditions.seating_capacity_start_range, 19)


class DedupeGroupingTests(TestCase):
    """Rules that differ on a non-range dimension must not be merged."""

    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_different_vehicle_sub_class_not_grouped(self):
        # R1 and R2 share GVW boundary 3500 but belong to different
        # sub-classes — they don't actually collide, so leave both alone.
        rules = [
            _make_rule(
                "R1",
                10,
                vehicle_sub_class=["tipper"],
                gvw_start_range=0,
                gvw_end_range=3500,
            ),
            _make_rule(
                "R2",
                20,
                vehicle_sub_class=["tanker"],
                gvw_start_range=3500,
                gvw_end_range=7500,
            ),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        self.assertEqual(rules[0].conditions.gvw_end_range, 3500)
        self.assertEqual(rules[1].conditions.gvw_start_range, 3500)

    def test_different_policy_type_not_grouped(self):
        rules = [
            _make_rule(
                "R1",
                10,
                policy_type=["third-party"],
                gvw_start_range=0,
                gvw_end_range=3500,
            ),
            _make_rule(
                "R2",
                20,
                policy_type=["comprehensive"],
                gvw_start_range=3500,
                gvw_end_range=7500,
            ),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        self.assertEqual(rules[1].conditions.gvw_start_range, 3500)

    def test_same_other_conditions_does_group(self):
        rules = [
            _make_rule(
                "R1",
                10,
                vehicle_sub_class=["tipper"],
                policy_type=["third-party"],
                gvw_start_range=0,
                gvw_end_range=3500,
            ),
            _make_rule(
                "R2",
                20,
                vehicle_sub_class=["tipper"],
                policy_type=["third-party"],
                gvw_start_range=3500,
                gvw_end_range=7500,
            ),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        self.assertEqual(rules[1].conditions.gvw_start_range, 3501)

    def test_gvw_dedupe_does_not_disturb_cc_buckets(self):
        # Two GVW buckets each replicated across two CC buckets.
        # Each (CC bucket) group should de-overlap GVW independently.
        rules = [
            _make_rule(
                "R1",
                10,
                cc_start_range=0,
                cc_end_range=1000,
                gvw_start_range=0,
                gvw_end_range=3500,
            ),
            _make_rule(
                "R2",
                20,
                cc_start_range=0,
                cc_end_range=1000,
                gvw_start_range=3500,
                gvw_end_range=7500,
            ),
            _make_rule(
                "R3",
                30,
                cc_start_range=1001,
                cc_end_range=1500,
                gvw_start_range=0,
                gvw_end_range=3500,
            ),
            _make_rule(
                "R4",
                40,
                cc_start_range=1001,
                cc_end_range=1500,
                gvw_start_range=3500,
                gvw_end_range=7500,
            ),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        by_id = {r.rule_id: r for r in rules}
        self.assertEqual(by_id["R1"].conditions.gvw_end_range, 3500)
        self.assertEqual(by_id["R2"].conditions.gvw_start_range, 3501)
        self.assertEqual(by_id["R3"].conditions.gvw_end_range, 3500)
        self.assertEqual(by_id["R4"].conditions.gvw_start_range, 3501)


class DedupePoliciesCountBoundariesTests(TestCase):
    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_policies_count_shared_boundary_bumps_next_start(self):
        rules = [
            _make_rule(
                "R1",
                10,
                policies_count_start_range=0,
                policies_count_end_range=30,
            ),
            _make_rule(
                "R2",
                20,
                policies_count_start_range=30,
                policies_count_end_range=100,
            ),
            _make_rule(
                "R3",
                30,
                policies_count_start_range=100,
                policies_count_end_range=500,
            ),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        starts = [r.conditions.policies_count_start_range for r in rules]
        ends = [r.conditions.policies_count_end_range for r in rules]
        self.assertEqual(starts, [0, 31, 101])
        self.assertEqual(ends, [30, 100, 500])


class DedupeLadderEdgeSingletonPromotionTests(TestCase):
    """Boundary singletons sharing a boundary with a sibling range are
    promoted to open-ended ranges. Chola TW July grid encodes CC buckets
    as headers "150cc | 150_350cc | 350cc"; the codegen helper emits
    bare singletons (150,150) and (350,350) for the boundary buckets.
    """

    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_cc_lowest_singleton_promoted_to_zero(self):
        rules = [
            _make_rule("R1", 10, cc_start_range=150, cc_end_range=150),
            _make_rule("R2", 20, cc_start_range=150, cc_end_range=350),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        self.assertEqual(rules[0].conditions.cc_start_range, 0)
        self.assertEqual(rules[0].conditions.cc_end_range, 150)
        self.assertEqual(rules[1].conditions.cc_start_range, 151)
        self.assertEqual(rules[1].conditions.cc_end_range, 350)

    def test_cc_highest_singleton_promoted_to_ceiling(self):
        rules = [
            _make_rule("R1", 10, cc_start_range=150, cc_end_range=350),
            _make_rule("R2", 20, cc_start_range=350, cc_end_range=350),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        self.assertEqual(rules[0].conditions.cc_end_range, 350)
        self.assertEqual(rules[1].conditions.cc_start_range, 351)
        self.assertEqual(rules[1].conditions.cc_end_range, 99999)

    def test_cc_chola_tw_three_bucket_ladder(self):
        # Full Chola TW shape: (150,150), (150,350), (350,350) →
        # (0,150), (151,350), (351,99999).
        rules = [
            _make_rule("R1", 10, cc_start_range=150, cc_end_range=150),
            _make_rule("R2", 20, cc_start_range=150, cc_end_range=350),
            _make_rule("R3", 30, cc_start_range=350, cc_end_range=350),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        starts = [r.conditions.cc_start_range for r in rules]
        ends = [r.conditions.cc_end_range for r in rules]
        self.assertEqual(starts, [0, 151, 351])
        self.assertEqual(ends, [150, 350, 99999])

    def test_non_boundary_singletons_not_promoted(self):
        # Two singletons that don't share a boundary with anything stay put.
        rules = [
            _make_rule("R1", 10, cc_start_range=150, cc_end_range=150),
            _make_rule("R2", 20, cc_start_range=350, cc_end_range=350),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        # No boundary sharing → no promotion.
        self.assertEqual(rules[0].conditions.cc_start_range, 150)
        self.assertEqual(rules[0].conditions.cc_end_range, 150)
        self.assertEqual(rules[1].conditions.cc_start_range, 350)
        self.assertEqual(rules[1].conditions.cc_end_range, 350)

    def test_gvw_lowest_singleton_promoted(self):
        rules = [
            _make_rule("R1", 10, gvw_start_range=3500, gvw_end_range=3500),
            _make_rule("R2", 20, gvw_start_range=3500, gvw_end_range=7500),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        self.assertEqual(rules[0].conditions.gvw_start_range, 0)
        self.assertEqual(rules[0].conditions.gvw_end_range, 3500)

    def test_gvw_highest_singleton_promoted_to_999999(self):
        rules = [
            _make_rule("R1", 10, gvw_start_range=3500, gvw_end_range=7500),
            _make_rule("R2", 20, gvw_start_range=7500, gvw_end_range=7500),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        self.assertEqual(rules[1].conditions.gvw_start_range, 7501)
        self.assertEqual(rules[1].conditions.gvw_end_range, 999999)

    def test_policies_count_ladder_promotion(self):
        # Chola TW: UPTO 30, 30-100, 100-500 → after parse:
        # (0,30) (from UPTO parsing), (30,100), (100,500). But if codegen
        # emits singleton for UPTO 30 as (30,30) it should still promote.
        rules = [
            _make_rule(
                "R1",
                10,
                policies_count_start_range=30,
                policies_count_end_range=30,
            ),
            _make_rule(
                "R2",
                20,
                policies_count_start_range=30,
                policies_count_end_range=100,
            ),
            _make_rule(
                "R3",
                30,
                policies_count_start_range=500,
                policies_count_end_range=500,
            ),
            _make_rule(
                "R4",
                25,
                policies_count_start_range=100,
                policies_count_end_range=500,
            ),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        # Sort for assertions
        by_id = {r.rule_id: r for r in rules}
        self.assertEqual(by_id["R1"].conditions.policies_count_start_range, 0)
        self.assertEqual(by_id["R1"].conditions.policies_count_end_range, 30)
        self.assertEqual(by_id["R3"].conditions.policies_count_start_range, 501)
        self.assertEqual(by_id["R3"].conditions.policies_count_end_range, 999999)


class DedupeNoisyMakeFieldTests(TestCase):
    """Rules whose only differing dimension is vehicle_make/vehicle_model
    must still de-overlap. Source docs commonly leak indexer slug
    fragments and sentinel makes ("All Other Make/Models") into these
    fields, which used to split a single ladder into per-make groups
    that never deduped. Reproduces commission doc
    d81925b1-e458-47fd-b79b-51154b94ce5e (COMMERCIAL_VEHICLE / GCV).
    """

    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_ladder_with_noisy_vehicle_make_dedupes(self):
        rules = [
            _make_rule(
                "R1",
                10,
                state=["andhra-pradesh"],
                policy_type=["third-party"],
                vehicle_make=["All Other Make/Models"],
                gvw_start_range=0,
                gvw_end_range=3500,
            ),
            _make_rule(
                "R2",
                20,
                state=["andhra-pradesh"],
                policy_type=["third-party"],
                vehicle_make=None,
                gvw_start_range=3500,
                gvw_end_range=7500,
            ),
            _make_rule(
                "R3",
                30,
                state=["andhra-pradesh"],
                policy_type=["third-party"],
                vehicle_make=["2_UPTO_3.5T[Electric]"],
                gvw_start_range=7500,
                gvw_end_range=12000,
            ),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        by_id = {r.rule_id: r for r in rules}
        self.assertEqual(by_id["R1"].conditions.gvw_end_range, 3500)
        self.assertEqual(by_id["R2"].conditions.gvw_start_range, 3501)
        self.assertEqual(by_id["R3"].conditions.gvw_start_range, 7501)

    def test_ladder_with_noisy_vehicle_model_dedupes(self):
        rules = [
            _make_rule(
                "R1",
                10,
                state=["karnataka"],
                policy_type=["comprehensive"],
                vehicle_model=["All Other Models"],
                cc_start_range=0,
                cc_end_range=1000,
            ),
            _make_rule(
                "R2",
                20,
                state=["karnataka"],
                policy_type=["comprehensive"],
                vehicle_model=None,
                cc_start_range=1000,
                cc_end_range=1500,
            ),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        self.assertEqual(rules[1].conditions.cc_start_range, 1001)


class DedupeSentinelSkipTests(TestCase):
    """Sentinel (0, 0) / (0, 99999) / (0, 99) ranges represent 'no filter on
    this dimension' — emitted by _transform_conditions when the source
    entry omits the field entirely. They must NOT participate in dedupe;
    otherwise an unrelated real-range rule sharing the lower bound gets
    shifted. Reproduces 52 spurious 0 → 1 GVW shifts on commission doc
    d81925b1 once vehicle_make grouping was relaxed.
    """

    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_gvw_sentinel_does_not_shift_real_range(self):
        rules = [
            # Sentinel "no GVW filter" — must not push the real rule.
            _make_rule(
                "R0",
                5,
                state=["andhra-pradesh"],
                policy_type=["third-party"],
                gvw_start_range=0,
                gvw_end_range=0,
            ),
            _make_rule(
                "R1",
                10,
                state=["andhra-pradesh"],
                policy_type=["third-party"],
                gvw_start_range=0,
                gvw_end_range=3500,
            ),
            _make_rule(
                "R2",
                20,
                state=["andhra-pradesh"],
                policy_type=["third-party"],
                gvw_start_range=3500,
                gvw_end_range=7500,
            ),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        by_id = {r.rule_id: r for r in rules}
        # Sentinel untouched.
        self.assertEqual(by_id["R0"].conditions.gvw_start_range, 0)
        self.assertEqual(by_id["R0"].conditions.gvw_end_range, 0)
        # Real ladder still de-overlaps. R1 start stays 0 (not bumped to 1).
        self.assertEqual(by_id["R1"].conditions.gvw_start_range, 0)
        self.assertEqual(by_id["R1"].conditions.gvw_end_range, 3500)
        self.assertEqual(by_id["R2"].conditions.gvw_start_range, 3501)

    def test_cc_sentinel_does_not_shift_real_range(self):
        rules = [
            _make_rule(
                "R0",
                5,
                state=["karnataka"],
                policy_type=["comprehensive"],
                cc_start_range=0,
                cc_end_range=99999,
            ),
            _make_rule(
                "R1",
                10,
                state=["karnataka"],
                policy_type=["comprehensive"],
                cc_start_range=0,
                cc_end_range=1000,
            ),
            _make_rule(
                "R2",
                20,
                state=["karnataka"],
                policy_type=["comprehensive"],
                cc_start_range=1000,
                cc_end_range=1500,
            ),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        by_id = {r.rule_id: r for r in rules}
        self.assertEqual(by_id["R0"].conditions.cc_start_range, 0)
        self.assertEqual(by_id["R0"].conditions.cc_end_range, 99999)
        self.assertEqual(by_id["R1"].conditions.cc_start_range, 0)
        self.assertEqual(by_id["R2"].conditions.cc_start_range, 1001)

    def test_seating_sentinel_does_not_shift_real_range(self):
        rules = [
            _make_rule(
                "R0",
                5,
                state=["tamil-nadu"],
                seating_capacity_start_range=0,
                seating_capacity_end_range=99,
            ),
            _make_rule(
                "R1",
                10,
                state=["tamil-nadu"],
                seating_capacity_start_range=0,
                seating_capacity_end_range=18,
            ),
            _make_rule(
                "R2",
                20,
                state=["tamil-nadu"],
                seating_capacity_start_range=18,
                seating_capacity_end_range=36,
            ),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        by_id = {r.rule_id: r for r in rules}
        self.assertEqual(by_id["R0"].conditions.seating_capacity_end_range, 99)
        self.assertEqual(by_id["R1"].conditions.seating_capacity_start_range, 0)
        self.assertEqual(by_id["R2"].conditions.seating_capacity_start_range, 19)


class CollapseDuplicateRulesTests(TestCase):
    """LLM extraction often emits the same logical rule multiple times with
    different vehicle_make values (one per source-table row). Two such
    duplicates at the same (start, end) cause the invert-guard cascade in
    _dedupe_range_overlaps: first dup bumps to start+1, second can't bump
    (would invert), stays at the original boundary and collides with the
    prior bucket. Pre-collapsing identical-condition + identical-outcome
    duplicates eliminates the cascade.
    """

    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def _mk_outcome(self, pct: float = 10.0):
        from cins.schema.cics_model import CommissionOutcome

        return CommissionOutcome(commission_type="percentage", percentage_value=pct)

    def _rule(self, rule_id: str, priority: int, outcome, **cond):
        return CommissionRule(
            rule_id=rule_id,
            priority=priority,
            conditions=RuleConditions(**cond),
            outcome=outcome,
        )

    def test_collapses_same_conditions_same_outcome_different_make(self):
        out = self._mk_outcome(45.0)
        rules = [
            self._rule(
                "R1",
                10,
                out,
                state=["andhra-pradesh"],
                policy_type=["comprehensive"],
                vehicle_make=["Suzuki"],
                cc_start_range=150,
                cc_end_range=350,
            ),
            self._rule(
                "R2",
                20,
                out,
                state=["andhra-pradesh"],
                policy_type=["comprehensive"],
                vehicle_make=["Honda"],
                cc_start_range=150,
                cc_end_range=350,
            ),
            self._rule(
                "R3",
                30,
                out,
                state=["andhra-pradesh"],
                policy_type=["comprehensive"],
                vehicle_make=["Others"],
                cc_start_range=150,
                cc_end_range=350,
            ),
        ]
        collapsed = self.transformer._collapse_duplicate_rules(rules)
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0].rule_id, "R1")  # lowest priority wins

    def test_preserves_when_outcome_differs(self):
        # Different outcomes = genuinely different rules; do NOT collapse.
        rules = [
            self._rule(
                "R1",
                10,
                self._mk_outcome(45.0),
                state=["andhra-pradesh"],
                vehicle_make=["Suzuki"],
                cc_start_range=150,
                cc_end_range=350,
            ),
            self._rule(
                "R2",
                20,
                self._mk_outcome(50.0),
                state=["andhra-pradesh"],
                vehicle_make=["Honda"],
                cc_start_range=150,
                cc_end_range=350,
            ),
        ]
        collapsed = self.transformer._collapse_duplicate_rules(rules)
        self.assertEqual(len(collapsed), 2)

    def test_preserves_when_non_make_condition_differs(self):
        out = self._mk_outcome(45.0)
        rules = [
            self._rule(
                "R1",
                10,
                out,
                state=["andhra-pradesh"],
                policy_type=["comprehensive"],
                cc_start_range=150,
                cc_end_range=350,
            ),
            self._rule(
                "R2",
                20,
                out,
                state=["andhra-pradesh"],
                policy_type=["third-party"],
                cc_start_range=150,
                cc_end_range=350,
            ),
        ]
        collapsed = self.transformer._collapse_duplicate_rules(rules)
        self.assertEqual(len(collapsed), 2)

    def test_keeps_highest_priority_lowest_number(self):
        out = self._mk_outcome(45.0)
        rules = [
            self._rule(
                "R1",
                30,
                out,
                vehicle_make=["Honda"],
                cc_start_range=150,
                cc_end_range=350,
            ),
            self._rule(
                "R2",
                10,
                out,
                vehicle_make=["Suzuki"],
                cc_start_range=150,
                cc_end_range=350,
            ),
            self._rule(
                "R3",
                20,
                out,
                vehicle_make=["Others"],
                cc_start_range=150,
                cc_end_range=350,
            ),
        ]
        collapsed = self.transformer._collapse_duplicate_rules(rules)
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0].rule_id, "R2")

    def test_list_order_normalised(self):
        # state ['a','b'] should collapse with ['b','a'] when other fields match.
        out = self._mk_outcome(10.0)
        rules = [
            self._rule(
                "R1",
                10,
                out,
                state=["andhra-pradesh", "telangana"],
                cc_start_range=0,
                cc_end_range=1000,
            ),
            self._rule(
                "R2",
                20,
                out,
                state=["telangana", "andhra-pradesh"],
                cc_start_range=0,
                cc_end_range=1000,
            ),
        ]
        collapsed = self.transformer._collapse_duplicate_rules(rules)
        self.assertEqual(len(collapsed), 1)

    def test_collapse_resolves_invert_guard_cascade(self):
        # Reproduces TW POLICIES bug: (0,30) + two (30,100) dups differing only
        # by vehicle_make. After collapse + dedupe, the single (30,100) bumps
        # cleanly to (31,100).
        out = self._mk_outcome(10.0)
        rules = [
            self._rule(
                "R1",
                10,
                out,
                state=["andhra-pradesh"],
                vehicle_sub_class=["scooter"],
                vehicle_make=["Others"],
                policies_count_start_range=0,
                policies_count_end_range=30,
            ),
            self._rule(
                "R2",
                20,
                out,
                state=["andhra-pradesh"],
                vehicle_sub_class=["scooter"],
                vehicle_make=["Suzuki"],
                policies_count_start_range=30,
                policies_count_end_range=100,
            ),
            self._rule(
                "R3",
                30,
                out,
                state=["andhra-pradesh"],
                vehicle_sub_class=["scooter"],
                vehicle_make=["Others"],
                policies_count_start_range=30,
                policies_count_end_range=100,
            ),
        ]
        collapsed = self.transformer._collapse_duplicate_rules(rules)
        self.assertEqual(len(collapsed), 2)
        self.transformer._dedupe_range_overlaps(collapsed)
        # Remaining (30, 100) should be cleanly bumped to (31, 100).
        ladder = sorted(
            (
                r.conditions.policies_count_start_range,
                r.conditions.policies_count_end_range,
            )
            for r in collapsed
        )
        self.assertEqual(ladder, [(0, 30), (31, 100)])

    def test_empty_list_returns_empty(self):
        self.assertEqual(self.transformer._collapse_duplicate_rules([]), [])

    def test_rules_without_conditions_preserved(self):
        out = self._mk_outcome(10.0)
        r1 = CommissionRule(rule_id="R1", priority=10, conditions=None, outcome=out)
        r2 = CommissionRule(rule_id="R2", priority=20, conditions=None, outcome=out)
        collapsed = self.transformer._collapse_duplicate_rules([r1, r2])
        # Both have identical (empty) conditions + same outcome -> collapsed.
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0].rule_id, "R1")


class DedupeEdgeCasesTests(TestCase):
    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_rules_without_conditions_skipped(self):
        rule = CommissionRule(
            rule_id="R1",
            priority=10,
            conditions=None,
            outcome=CommissionOutcome(commission_type="flat", flat_value=100.0),
        )
        # Must not raise.
        self.transformer._dedupe_range_overlaps([rule])

    def test_single_rule_unchanged(self):
        rules = [_make_rule("R1", 10, gvw_start_range=0, gvw_end_range=3500)]
        self.transformer._dedupe_range_overlaps(rules)
        self.assertEqual(rules[0].conditions.gvw_start_range, 0)
        self.assertEqual(rules[0].conditions.gvw_end_range, 3500)

    def test_skip_field_when_start_or_end_none(self):
        # Mixed: one rule lacks a GVW bound — that rule is excluded from
        # the GVW de-overlap group. The other two should still de-overlap.
        rules = [
            _make_rule("R1", 10, gvw_start_range=0, gvw_end_range=3500),
            _make_rule("R2", 20, cc_start_range=0, cc_end_range=1000),
            _make_rule("R3", 30, gvw_start_range=3500, gvw_end_range=7500),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        self.assertEqual(rules[2].conditions.gvw_start_range, 3501)

    def test_inverted_range_not_created(self):
        # If bumping would create start > end, leave the rule alone
        # so the validator can surface the genuine overlap.
        rules = [
            _make_rule("R1", 10, gvw_start_range=0, gvw_end_range=7500),
            _make_rule("R2", 20, gvw_start_range=3500, gvw_end_range=7500),
        ]
        self.transformer._dedupe_range_overlaps(rules)
        # R2 fully contained in R1 — bump would set start to 7501 > end 7500,
        # so we leave it for the validator.
        self.assertEqual(rules[1].conditions.gvw_start_range, 3500)
        self.assertEqual(rules[1].conditions.gvw_end_range, 7500)


class GVWSlugDetectionTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_weight_slugs_detected(self):
        for slug in (
            "2_UPTO_3.5T",
            "3_3.5T_TO_7.5T",
            "10_ABOVE_47T",
            "UPTO 3.5T",
            "3500-7500 KG",
            "GVW < 2000",
        ):
            self.assertTrue(
                self.t._gvw_slug_or_empty(slug), f"{slug!r} should be GVW-shaped"
            )

    def test_non_gvw_rejected(self):
        for slug in (
            "3 WHEELER",
            "16 SEATER",
            "350cc",
            "150-350cc",
            "GCCV",
            "SCOOTER",
            "7 KW",
            None,
            "",
        ):
            self.assertEqual(
                self.t._gvw_slug_or_empty(slug), "", f"{slug!r} must not be read as GVW"
            )


class GVWRecoveryTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()
        self.raw = SimpleNamespace(global_vehicle_type_text=None)

    def test_recover_from_subclass_key(self):
        # Codegen stores the slug under the short "subclass" key.
        self.assertEqual(
            self.t._recover_gvw_range({"subclass": "2_UPTO_3.5T"}, self.raw),
            (0, 3500),
        )

    def test_recover_range_and_open_ends(self):
        self.assertEqual(
            self.t._recover_gvw_range({"subclass": "3_3.5T_TO_7.5T"}, self.raw),
            (3500, 7500),
        )
        # "ABOVE 47T" is EXCLUSIVE, and the rung below it ends at 47000 — so the
        # open band starts at 47001, not 47000. _recover_gvw_range parses with
        # strict_bounds=True; previously it returned 47000 and relied on the
        # de-overlap pass to bump it, which only fires when the 40T-47T sibling
        # happens to share a group.
        self.assertEqual(
            self.t._recover_gvw_range({"subclass": "10_ABOVE_47T"}, self.raw),
            (47001, 999999),
        )

    def test_recover_from_canonical_key(self):
        self.assertEqual(
            self.t._recover_gvw_range({"vehicle_sub_class": "UPTO 3.5T"}, self.raw),
            (0, 3500),
        )

    def test_no_recovery_for_non_gvw_subclass(self):
        self.assertIsNone(
            self.t._recover_gvw_range({"subclass": "3 WHEELER"}, self.raw)
        )
        self.assertIsNone(self.t._recover_gvw_range({}, self.raw))


class BoundaryNormalizationTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_fragmented_ladder_bumped_across_states(self):
        # Same CC ladder split across states (relaxed key drops state) — the
        # (1000,1500) band should bump even though its (0,1000) rung sits in a
        # different all-conditions group.
        rules = [
            _make_rule("R1", 10, state=["a"], cc_start_range=0, cc_end_range=1000),
            _make_rule("R2", 20, state=["b"], cc_start_range=1000, cc_end_range=1500),
            _make_rule("R3", 30, state=["b"], cc_start_range=1500, cc_end_range=99999),
        ]
        self.t._normalize_boundary_overlaps(rules)
        self.assertEqual(rules[1].conditions.cc_start_range, 1001)
        self.assertEqual(rules[2].conditions.cc_start_range, 1501)
        self.assertEqual(rules[0].conditions.cc_start_range, 0)
        # Each bump is logged as an informational NOTICE, never a warning — a CV
        # document emits hundreds and they buried the real failures.
        self.assertEqual(len(self.t.notices), 2, self.t.notices)
        self.assertTrue(
            all("Boundary bump" in n for n in self.t.notices), self.t.notices
        )
        self.assertEqual(self.t.warnings, [])

    def test_counter_example_not_bumped(self):
        # Genuine gap ladder: no band ends at 1000 or 1600, so neither start is
        # a shared tier edge — leave them alone (C2 guard).
        rules = [
            _make_rule("R1", 10, state=["a"], cc_start_range=1000, cc_end_range=1500),
            _make_rule("R2", 20, state=["b"], cc_start_range=1600, cc_end_range=2000),
        ]
        self.t._normalize_boundary_overlaps(rules)
        self.assertEqual(rules[0].conditions.cc_start_range, 1000)
        self.assertEqual(rules[1].conditions.cc_start_range, 1600)

    def test_floor_point_range_promoted_with_sibling(self):
        rules = [
            _make_rule("R1", 10, cc_start_range=1000, cc_end_range=1000),
            _make_rule("R2", 20, cc_start_range=1000, cc_end_range=1500),
        ]
        self.t._normalize_boundary_overlaps(rules)
        self.assertEqual(rules[0].conditions.cc_start_range, 0)
        self.assertEqual(rules[0].conditions.cc_end_range, 1000)
        self.assertEqual(rules[1].conditions.cc_start_range, 1001)
        self.assertTrue(
            any("floor-promotion" in n for n in self.t.notices), self.t.notices
        )
        self.assertEqual(self.t.warnings, [])

    def test_lone_point_range_not_widened(self):
        # (1000,1000) with no sibling starting at 1000 stays a point rate.
        rules = [
            _make_rule("R1", 10, cc_start_range=1000, cc_end_range=1000),
            _make_rule("R2", 20, cc_start_range=2000, cc_end_range=3000),
        ]
        self.t._normalize_boundary_overlaps(rules)
        self.assertEqual(rules[0].conditions.cc_start_range, 1000)
        self.assertEqual(rules[0].conditions.cc_end_range, 1000)

    # --- Change B: wildcard-compatible cross-group evidence (defect 8) ---

    def test_fuelless_band_bumped_on_fueltagged_evidence(self):
        # Reproduces defect 8: a fuel-less 40000-43000 band forms its own group
        # (fuel_type is in the boundary key). The lower rung ending at 40000
        # lives only in the fuel-tagged ladder. A None fuel slot is a wildcard,
        # so the fuel-less band must collect that rung as evidence and bump to
        # 40001. Fails before the wildcard fix.
        rules = [
            _make_rule(
                "F1", 10, state=["a"], gvw_start_range=40000, gvw_end_range=43000
            ),
            _make_rule(
                "F2", 20, state=["b"], gvw_start_range=40000, gvw_end_range=43000
            ),
            _make_rule(
                "P1", 30, fuel_type=["petrol"], gvw_start_range=0, gvw_end_range=40000
            ),
            _make_rule(
                "P2",
                40,
                fuel_type=["petrol"],
                gvw_start_range=40001,
                gvw_end_range=43000,
            ),
        ]
        self.t._normalize_boundary_overlaps(rules)
        by_id = {r.rule_id: r for r in rules}
        self.assertEqual(by_id["F1"].conditions.gvw_start_range, 40001)
        self.assertEqual(by_id["F2"].conditions.gvw_start_range, 40001)
        # Fuel-tagged rungs are untouched (application stays scoped per group).
        self.assertEqual(by_id["P1"].conditions.gvw_start_range, 0)
        self.assertEqual(by_id["P2"].conditions.gvw_start_range, 40001)

    def test_disjoint_petrol_electric_not_cross_bumped(self):
        # ['petrol'] and ['electric'] have an empty intersection, so an electric
        # band's start must NOT bump just because a petrol band ends there.
        rules = [
            _make_rule(
                "E1",
                10,
                state=["a"],
                fuel_type=["electric"],
                cc_start_range=1000,
                cc_end_range=1500,
            ),
            _make_rule(
                "E2",
                20,
                state=["b"],
                fuel_type=["electric"],
                cc_start_range=1000,
                cc_end_range=1500,
            ),
            _make_rule(
                "P1", 30, fuel_type=["petrol"], cc_start_range=0, cc_end_range=1000
            ),
        ]
        self.t._normalize_boundary_overlaps(rules)
        by_id = {r.rule_id: r for r in rules}
        self.assertEqual(by_id["E1"].conditions.cc_start_range, 1000)
        self.assertEqual(by_id["E2"].conditions.cc_start_range, 1000)

    def test_disjoint_policy_type_not_cross_bumped(self):
        # Boundary-pass analogue of the dedupe-pass policy_type test: a comp
        # ladder must not bump on a tp ladder's edge (disjoint list slot).
        rules = [
            _make_rule(
                "C1",
                10,
                state=["a"],
                policy_type=["comprehensive"],
                cc_start_range=1000,
                cc_end_range=1500,
            ),
            _make_rule(
                "C2",
                20,
                state=["b"],
                policy_type=["comprehensive"],
                cc_start_range=1000,
                cc_end_range=1500,
            ),
            _make_rule(
                "T1", 30, policy_type=["tp"], cc_start_range=0, cc_end_range=1000
            ),
        ]
        self.t._normalize_boundary_overlaps(rules)
        by_id = {r.rule_id: r for r in rules}
        self.assertEqual(by_id["C1"].conditions.cc_start_range, 1000)
        self.assertEqual(by_id["C2"].conditions.cc_start_range, 1000)

    def test_fuelless_floor_promotion_on_fueltagged_sibling(self):
        # A fuel-less (1000,1000) point promotes to (0,1000) on a fuel-tagged
        # sibling that starts a wider band at 1000.
        rules = [
            _make_rule("F1", 10, state=["a"], cc_start_range=1000, cc_end_range=1000),
            _make_rule("F2", 20, state=["b"], cc_start_range=1000, cc_end_range=1000),
            _make_rule(
                "P1", 30, fuel_type=["petrol"], cc_start_range=1000, cc_end_range=1500
            ),
        ]
        self.t._normalize_boundary_overlaps(rules)
        by_id = {r.rule_id: r for r in rules}
        self.assertEqual(by_id["F1"].conditions.cc_start_range, 0)
        self.assertEqual(by_id["F2"].conditions.cc_start_range, 0)


class PointBandFlagTests(TestCase):
    """A cc/GVW band still collapsed to a point after all three passes is
    REPORTED, not repaired.

    "150cc" means (0, 150) in a ladder whose next column is "150_350cc" and
    (151, 99999) in one whose previous column is "UPTO 150 CC". Both boundary
    passes already widen it whenever a sibling column supplies that evidence;
    when none does, guessing would silently misprice a deal."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_lone_cc_point_band_is_flagged(self):
        rules = [_make_rule("R1", 10, cc_start_range=150, cc_end_range=150)]
        self.t._flag_point_bands(rules, "Two Wheeler")
        self.assertEqual(len(self.t.warnings), 1)
        self.assertIn("Unresolved cc point band", self.t.warnings[0])
        self.assertIn("R1", self.t.warnings[0])
        # Reported, not repaired.
        self.assertEqual(rules[0].conditions.cc_start_range, 150)

    def test_gvw_point_band_is_flagged(self):
        rules = [_make_rule("R1", 10, gvw_start_range=3500, gvw_end_range=3500)]
        self.t._flag_point_bands(rules, "Commercial Vehicle")
        self.assertEqual(len(self.t.warnings), 1)
        self.assertIn("Unresolved GVW point band", self.t.warnings[0])

    def test_sentinels_and_real_bands_are_not_flagged(self):
        rules = [
            # (0, 0) is the "no GVW filter" sentinel, not a zero-width band.
            _make_rule("R1", 10, gvw_start_range=0, gvw_end_range=0),
            _make_rule("R2", 20, cc_start_range=0, cc_end_range=99999),
            _make_rule("R3", 30, cc_start_range=151, cc_end_range=350),
        ]
        self.t._flag_point_bands(rules, "Two Wheeler")
        self.assertEqual(self.t.warnings, [])

    def test_promoted_ladder_leaves_nothing_to_flag(self):
        # The Chola TW three-bucket ladder: once _dedupe_range_overlaps has run,
        # no point band survives, so the flag must stay silent.
        rules = [
            _make_rule("R1", 10, cc_start_range=150, cc_end_range=150),
            _make_rule("R2", 20, cc_start_range=150, cc_end_range=350),
            _make_rule("R3", 30, cc_start_range=350, cc_end_range=350),
        ]
        self.t._dedupe_range_overlaps(rules)
        self.t._flag_point_bands(rules, "Two Wheeler")
        self.assertEqual([r.conditions.cc_start_range for r in rules], [0, 151, 351])
        self.assertEqual(self.t.warnings, [])

    def test_many_offenders_are_summarised(self):
        rules = [
            _make_rule(f"R{i}", i, cc_start_range=150, cc_end_range=150)
            for i in range(1, 9)
        ]
        self.t._flag_point_bands(rules, "Two Wheeler")
        self.assertEqual(len(self.t.warnings), 1)
        self.assertIn("on 8 rule(s)", self.t.warnings[0])
        self.assertIn("(+3 more)", self.t.warnings[0])


class LateMutationLadderTests(TestCase):
    """The boundary passes group rules by all their NON-range conditions,
    vehicle_sub_class included — so a GCV weight ladder whose rungs are told
    apart ONLY by a body word is one singleton group per body, and neither
    earlier pass can see the pair. _strip_gcv_subclass then nulls the body,
    which is what makes them siblings, and until the third pass was added
    nothing ran after it: the rungs exported their shared boundary verbatim."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def _make_raw(self, entries):
        return RawExtraction(
            document_type="commission_grid",
            source_type="excel",
            provider=RawProviderInfo(name="Test Insurer"),
            start_date="2024-01-01",
            end_date="2024-03-31",
            commission_entries=entries,
        )

    def test_gcv_ladder_split_by_subclass_is_monotonic_after_the_strip(self):
        # Two GVW rungs sharing the boundary 3500, told apart ONLY by a body
        # word. The body must be carried in conditions["vehicle_sub_class"] —
        # that is the field the group key reads. Reaching the strip at
        # (0,3500) / (3500,7500) is the precondition this test exists for;
        # remove the third pass and the second rung stays at 3500.
        entries = [
            RawCommissionEntry(
                rule_text="GCCV 4W tipper upto 3.5T",
                commission_value="10%",
                product_name="Commercial Vehicle",
                product_type="GCV",
                conditions={"vehicle_sub_class": "TIPPER", "gvw": "UPTO 3.5T"},
                source_section="1_GCCV_4W",
            ),
            RawCommissionEntry(
                rule_text="GCCV 4W tanker 3.5T to 7.5T",
                commission_value="12%",
                product_name="Commercial Vehicle",
                product_type="GCV",
                conditions={"vehicle_sub_class": "TANKER", "gvw": "3.5T TO 7.5T"},
                source_section="1_GCCV_4W",
            ),
        ]
        product = self.t._transform_product(
            "Commercial Vehicle", entries, None, self._make_raw(entries)
        )
        bands = sorted(
            (r.conditions.gvw_start_range, r.conditions.gvw_end_range)
            for r in product.commission_rules
        )
        self.assertEqual(bands, [(0, 3500), (3501, 7500)])

    def test_cv_ladder_split_by_renewal_rollover_widening_is_monotonic(self):
        # business_type is also a group-key field, and _imply_rollover_on_renewal
        # rewrites it — but it runs BEFORE the first dedupe, so by grouping time
        # a rung spelled "renewal" and one spelled "renewal, rollover" have
        # converged and share a key. This pins that ordering: move the widening
        # after the dedupe and these two rungs split into singleton groups and
        # keep their shared 3500 boundary.
        entries = [
            RawCommissionEntry(
                rule_text="GCCV 4W renewal upto 3.5T",
                commission_value="10%",
                product_name="Commercial Vehicle",
                product_type="GCV",
                customer_type_text="renewal",
                conditions={"gvw": "UPTO 3.5T"},
                source_section="1_GCCV_4W",
            ),
            RawCommissionEntry(
                rule_text="GCCV 4W renewal/rollover 3.5T to 7.5T",
                commission_value="12%",
                product_name="Commercial Vehicle",
                product_type="GCV",
                customer_type_text="renewal, rollover",
                conditions={"gvw": "3.5T TO 7.5T"},
                source_section="1_GCCV_4W",
            ),
        ]
        product = self.t._transform_product(
            "Commercial Vehicle", entries, None, self._make_raw(entries)
        )
        bands = sorted(
            (r.conditions.gvw_start_range, r.conditions.gvw_end_range)
            for r in product.commission_rules
        )
        self.assertEqual(bands, [(0, 3500), (3501, 7500)])

    def test_isolated_above_cc_rule_starts_at_the_next_integer(self):
        # The case no amount of re-running the evidence-gated passes can fix:
        # ONE rule, no sibling rung anywhere. ">150cc" is exclusive, so it must
        # start at 151 — otherwise it overlaps a "UPTO 150 CC" rule published on
        # some other sheet of the same document.
        entries = [
            RawCommissionEntry(
                rule_text="TW above 150cc",
                commission_value="10%",
                product_name="Two Wheeler",
                product_type="2W",
                conditions={"cc": "ABOVE 150 CC"},
            ),
        ]
        product = self.t._transform_product(
            "Two Wheeler", entries, None, self._make_raw(entries)
        )
        self.assertEqual(len(product.commission_rules), 1)
        cond = product.commission_rules[0].conditions
        self.assertEqual((cond.cc_start_range, cond.cc_end_range), (151, 99999))


class DedupeMultiRuleRungTests(TestCase):
    """A rung of the ladder is routinely MORE THAN ONE RULE, and the walk has
    to normalise it as a unit.

    A grid column is published once per make and per rate, and
    vehicle_make/vehicle_model are deliberately excluded from the group key —
    so the Chola TW ladder arrives as several rules on "150cc", one on
    "150_350cc" and several on "350cc", all in ONE group. Walking rules
    directly fixed the first rule of each rung and left every sibling behind:
    the second (150,150) hit ``start <= prev_end`` with prev_end already 150,
    the bump to 151 would have inverted the range, the invert-guard skipped it,
    and it shipped as the point band "150cc to 150cc"."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def _ladder(self, spec):
        rules = []
        for i, (s, e) in enumerate(spec):
            rules.append(
                _make_rule(
                    f"R{i}",
                    i,
                    state=["andhra-pradesh"],
                    vehicle_sub_class=["bike"],
                    vehicle_make=[f"MAKE{i}"],
                    cc_start_range=s,
                    cc_end_range=e,
                )
            )
        return rules

    def test_chola_tw_rung_multiplicity_from_doc_b6b085b3(self):
        # The real shape: 3 rules on "150cc", 1 on "150_350cc", 7 on "350cc".
        # Old behaviour left 2 of the 150s and 6 of the 350s as point bands.
        rules = self._ladder(
            [(150, 150)] * 3 + [(150, 350)] + [(350, 350)] * 7,
        )
        self.t._dedupe_range_overlaps(rules)
        bands = sorted(
            (r.conditions.cc_start_range, r.conditions.cc_end_range) for r in rules
        )
        self.assertEqual(bands, [(0, 150)] * 3 + [(151, 350)] + [(351, 99999)] * 7)

    def test_every_rule_on_a_rung_moves_together(self):
        rules = self._ladder([(0, 1000), (0, 1000), (1000, 1500), (1000, 1500)])
        self.t._dedupe_range_overlaps(rules)
        starts = [r.conditions.cc_start_range for r in rules]
        self.assertEqual(starts, [0, 0, 1001, 1001])

    def test_single_rung_repeated_is_left_alone(self):
        # One distinct band cannot overlap itself, however many rules carry it —
        # and it must NOT be floor-promoted by treating a duplicate as "the next
        # rung", which is what walking rules used to do.
        rules = self._ladder([(150, 150), (150, 150), (150, 150)])
        self.t._dedupe_range_overlaps(rules)
        bands = [
            (r.conditions.cc_start_range, r.conditions.cc_end_range) for r in rules
        ]
        self.assertEqual(bands, [(150, 150)] * 3)


class CholaTwLadderEndToEndTests(TestCase):
    """The client-stated contract for the Chola TW cc ladder, in ONE place:

        150cc      -> 0 - 150
        150_350CC  -> 151 - 350
        350cc      -> 351 - 99999

    Every other test here covers one half — the sandbox helper's header parse
    (test_sandbox_helpers.ParseCcRangeUnderscoreBandTests) or the band walk
    (DedupeMultiRuleRungTests). This runs the whole chain, so a regression in
    EITHER half fails it: grid header -> parse_cc_range -> _parse_range_field
    -> _dedupe_range_overlaps -> _normalize_boundary_overlaps."""

    HEADERS = ("150cc", "150_350CC", "350cc")
    EXPECTED = [(0, 150), (151, 350), (351, 99999)]

    @classmethod
    def setUpClass(cls):
        ns: dict = {}
        exec(compile(_SANDBOX_HELPERS, "<helpers>", "exec"), ns)
        cls.parse_cc_range = staticmethod(ns["parse_cc_range"])

    def setUp(self):
        self.t = RawToCICSTransformer()

    def _band(self, header):
        start, end = self.t._parse_range_field(
            self.parse_cc_range(header), strict_bounds=True
        )
        return (0 if start is None else start, 99999 if end is None else end)

    def _walk(self, counts):
        rules, i = [], 0
        for header, n in zip(self.HEADERS, counts):
            start, end = self._band(header)
            for _ in range(n):
                i += 1
                rules.append(
                    _make_rule(
                        f"R{i}",
                        i,
                        state=["andhra-pradesh"],
                        vehicle_sub_class=["bike"],
                        vehicle_make=[f"MAKE{i}"],
                        cc_start_range=start,
                        cc_end_range=end,
                    )
                )
        self.t._dedupe_range_overlaps(rules)
        self.t._normalize_boundary_overlaps(rules)
        return rules

    def test_one_rule_per_column(self):
        rules = self._walk((1, 1, 1))
        bands = [
            (r.conditions.cc_start_range, r.conditions.cc_end_range) for r in rules
        ]
        self.assertEqual(bands, self.EXPECTED)

    def test_real_rung_multiplicity_from_doc_b6b085b3(self):
        # 3 makes on the low column, 1 on the middle, 7 on the high one. Every
        # rule of a rung must land on that rung's band, not just the first.
        counts = (3, 1, 7)
        rules = self._walk(counts)
        bands = [
            (r.conditions.cc_start_range, r.conditions.cc_end_range) for r in rules
        ]
        expected = [b for b, n in zip(self.EXPECTED, counts) for _ in range(n)]
        self.assertEqual(bands, expected)

    def test_no_point_band_survives(self):
        rules = self._walk((3, 1, 7))
        self.t._flag_point_bands(rules, "Two Wheeler")
        self.assertEqual(self.t.warnings, [])

    def test_the_three_columns_stay_three_distinct_rungs(self):
        # The middle column collapsing onto "350cc" is what removed the
        # ladder's evidence and produced "150cc to 150cc" / "350cc to 350cc".
        self.assertEqual(len({self._band(h) for h in self.HEADERS}), 3)


class BoundaryDeadlockTests(TestCase):
    """A rule un-bumped on TWO range dimensions at once used to be invisible on
    both, because each dimension's group key holds the others' CURRENT values.

    `_DEDUPE_RANGES` is walked one dimension at a time. A rule whose cc is still
    (150,350) and whose NOP is still (100,500) matches no sibling on the cc walk
    (their NOP already reads (101,500)) and none on the NOP walk (their cc
    already reads (151,350)). Each dimension waits for the other; neither moves.
    Range slots in the boundary key therefore compare by OVERLAP, not equality.

    Measured on Chola May'26 doc b6b085b3: 18 Two Wheeler rules stuck at
    cc (150,350) + NOP (100,500) after every other rule had converged."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def _doc(self):
        # Distinct states so _dedupe_range_overlaps cannot group them — the
        # per-group walk is exactly what has no adjacent rung here (the grid
        # has a "150_350cc" cell for this make but no "150cc" cell).
        return [
            # Supplies the cc tier edge at 150.
            _make_rule(
                "CC_EDGE",
                10,
                state=["gujarat"],
                cc_start_range=0,
                cc_end_range=150,
                policies_count_start_range=101,
                policies_count_end_range=500,
            ),
            # Supplies the NOP tier edge at 100.
            _make_rule(
                "NOP_EDGE",
                20,
                state=["karnataka"],
                cc_start_range=151,
                cc_end_range=350,
                policies_count_start_range=31,
                policies_count_end_range=100,
            ),
            # Un-bumped on both dimensions at once.
            _make_rule(
                "STUCK",
                30,
                state=["tamil-nadu"],
                cc_start_range=150,
                cc_end_range=350,
                policies_count_start_range=100,
                policies_count_end_range=500,
            ),
        ]

    def test_deadlocked_rule_resolves_on_both_dimensions(self):
        rules = self._doc()
        self.t._normalize_boundary_overlaps(rules)
        stuck = {r.rule_id: r for r in rules}["STUCK"].conditions
        self.assertEqual((stuck.cc_start_range, stuck.cc_end_range), (151, 350))
        self.assertEqual(
            (
                stuck.policies_count_start_range,
                stuck.policies_count_end_range,
            ),
            (101, 500),
        )

    def test_the_evidence_rules_are_left_alone(self):
        rules = self._doc()
        self.t._normalize_boundary_overlaps(rules)
        by_id = {r.rule_id: r for r in rules}
        edge = by_id["CC_EDGE"].conditions
        self.assertEqual((edge.cc_start_range, edge.cc_end_range), (0, 150))
        nop = by_id["NOP_EDGE"].conditions
        self.assertEqual(
            (nop.policies_count_start_range, nop.policies_count_end_range), (31, 100)
        )

    def test_non_overlapping_bands_remain_separate_contexts(self):
        # Overlap-compatibility must not fuse ladders that genuinely differ on
        # another dimension: a (0,150) cc rule and a (151,350) cc rule do not
        # overlap, so a NOP boundary in one is no evidence for the other.
        rules = [
            _make_rule(
                "LOW",
                10,
                state=["gujarat"],
                cc_start_range=0,
                cc_end_range=150,
                policies_count_start_range=31,
                policies_count_end_range=100,
            ),
            _make_rule(
                "HIGH",
                20,
                state=["karnataka"],
                cc_start_range=151,
                cc_end_range=350,
                policies_count_start_range=100,
                policies_count_end_range=500,
            ),
        ]
        self.t._normalize_boundary_overlaps(rules)
        high = {r.rule_id: r for r in rules}["HIGH"].conditions
        self.assertEqual(
            (high.policies_count_start_range, high.policies_count_end_range),
            (100, 500),
        )
