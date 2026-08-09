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

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
from cins.schema.cics_model import (
    CommissionOutcome,
    CommissionRule,
    RuleConditions,
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
        self.assertEqual(
            self.t._recover_gvw_range({"subclass": "10_ABOVE_47T"}, self.raw),
            (47000, 999999),
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
