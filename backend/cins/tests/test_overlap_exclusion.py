"""
Exclusion-aware disjointness in RuleOverlapChecker (Step 4e).

The GCCV ≤3.5T buckets (a)/(b)/(c) differ only by include vs exclude make/model
lists. Without exclusion awareness the checker flags them HIGH partial-overlap
on every doc. When one rule's include list is a SUBSET of the other's exclude
list they can never co-match → disjoint.

Run from the backend container:
    python manage.py test cins.tests.test_overlap_exclusion -v 2
"""

from unittest import TestCase

from cins.validators.rule_overlap_checker import OverlapSeverity, RuleOverlapChecker


def _rule(rule_id, conditions, priority=10):
    return {"rule_id": rule_id, "priority": priority, "conditions": conditions}


class ExclusionDisjointnessTests(TestCase):
    def setUp(self):
        self.c = RuleOverlapChecker()

    def _pair(self, c1, c2):
        self.c.overlaps = []
        self.c._check_rule_pair("P", _rule("r1", c1), _rule("r2", c2))
        return self.c.overlaps

    # A shared dimension (policy_type) is included so that WITHOUT the fix the
    # pair would flag a HIGH partial overlap — the fix must flip it to disjoint.
    _SHARED = {
        "policy_type": ["comprehensive"],
        "gvw_start_range": 0,
        "gvw_end_range": 3500,
    }

    def test_make_include_subset_of_exclude_is_disjoint(self):
        # (a) make=TMM vs (c) make_exclude=TMM
        a = {**self._SHARED, "vehicle_make": ["TATA", "Maruti", "Mahindra"]}
        c = {**self._SHARED, "vehicle_make_exclude": ["TATA", "Maruti", "Mahindra"]}
        self.assertEqual(self._pair(a, c), [])

    def test_model_include_subset_of_exclude_is_disjoint(self):
        # (a) make=TMM model=[Ace] vs (b) make=TMM model_exclude=[Ace]
        a = {
            **self._SHARED,
            "vehicle_make": ["TATA", "Maruti", "Mahindra"],
            "vehicle_model": ["Ace"],
        }
        b = {
            **self._SHARED,
            "vehicle_make": ["TATA", "Maruti", "Mahindra"],
            "vehicle_model_exclude": ["Ace"],
        }
        self.assertEqual(self._pair(a, b), [])

    def test_bucket_b_vs_c_disjoint(self):
        b = {
            **self._SHARED,
            "vehicle_make": ["TATA", "Maruti", "Mahindra"],
            "vehicle_model_exclude": ["Ace"],
        }
        c = {**self._SHARED, "vehicle_make_exclude": ["TATA", "Maruti", "Mahindra"]}
        self.assertEqual(self._pair(b, c), [])

    def test_partial_subset_not_reported(self):
        # include not fully covered by exclude → the rules do intersect (on Foo),
        # but a partial overlap is priority-resolved and no longer reported.
        a = {**self._SHARED, "vehicle_make": ["TATA", "Foo"]}
        c = {**self._SHARED, "vehicle_make_exclude": ["TATA"]}
        self.assertEqual(self._pair(a, c), [])


class CompleteOverlapSeverityTests(TestCase):
    """Identical conditions are a duplicate regardless of priority.

    Priorities are renumbered unique per product by
    ``processors.core.rule_priority.normalize_rule_priorities``, so severity
    must not be gated on ``priority1 == priority2`` for complete overlaps —
    otherwise every real duplicate silently downgrades to INFO.
    """

    def setUp(self):
        self.c = RuleOverlapChecker()

    def _pair(self, c1, c2, p1, p2):
        self.c.overlaps = []
        self.c._check_rule_pair(
            "P", _rule("r1", c1, priority=p1), _rule("r2", c2, priority=p2)
        )
        return self.c.overlaps

    _CONDITIONS = {"policy_type": ["comprehensive"], "state": ["MH"]}

    def test_identical_conditions_critical_with_different_priorities(self):
        overlaps = self._pair(self._CONDITIONS, dict(self._CONDITIONS), 10, 20)
        self.assertEqual(len(overlaps), 1)
        self.assertEqual(overlaps[0].overlap_type, "complete_overlap")
        self.assertEqual(overlaps[0].severity, OverlapSeverity.CRITICAL)

    def test_identical_conditions_critical_with_same_priority(self):
        overlaps = self._pair(self._CONDITIONS, dict(self._CONDITIONS), 10, 10)
        self.assertEqual(overlaps[0].severity, OverlapSeverity.CRITICAL)

    def test_two_catch_all_rules_critical_with_different_priorities(self):
        overlaps = self._pair({}, {}, 10, 20)
        self.assertEqual(len(overlaps), 1)
        self.assertEqual(overlaps[0].overlap_type, "complete_overlap")
        self.assertEqual(overlaps[0].severity, OverlapSeverity.CRITICAL)

    def test_partial_overlap_is_not_reported(self):
        # A narrower rule nested inside a broader one. It resolves by priority
        # (unique per product by construction), so it is not a finding — at any
        # priority pairing, including equal priorities.
        c1 = {"policy_type": ["comprehensive"], "state": ["MH"]}
        c2 = {"policy_type": ["comprehensive"], "state": ["MH"], "zero_dep": True}

        self.assertEqual(self._pair(c1, c2, 10, 10), [])
        self.assertEqual(self._pair(c1, c2, 10, 20), [])

    def test_only_complete_overlaps_are_ever_emitted(self):
        # Guards the contract itself: nothing but complete_overlap/CRITICAL.
        for c1, c2 in (
            ({"state": ["MH"]}, {"state": ["MH", "KA"]}),
            ({"state": ["MH"]}, {"state": ["MH"], "cpa": "with-cpa"}),
            ({"state": ["MH"]}, dict(self._CONDITIONS)),
        ):
            for o in self._pair(c1, c2, 10, 20):
                self.assertEqual(o.overlap_type, "complete_overlap")
                self.assertEqual(o.severity, OverlapSeverity.CRITICAL)


# Every field RuleConditions declares that _check_rule_pair inspects. The CICS
# document is dumped with exclude_none=False, so a production rule carries all of
# these with most set to None — the shape the checker actually has to survive.
_ALL_KEYS = [
    "vehicle_sub_class",
    "policy_type",
    "state",
    "rto",
    "number_of_wheels",
    "school_bus_type",
    "booking_mode",
    "fuel_type",
    "vehicle_make",
    "vehicle_model",
    "vehicle_carrier",
    "business_type",
    "deal_parameter",
    "zero_dep",
    "cpa",
    "ncb",
    "vehicle_age",
    "od_term",
    "tp_term",
    "gvw_start_range",
    "gvw_end_range",
    "cc_start_range",
    "cc_end_range",
    "seating_capacity_start_range",
    "seating_capacity_end_range",
    "policies_count_start_range",
    "policies_count_end_range",
]


def _dense(**constrained):
    """A production-shaped conditions dict: every key present, most None."""
    return {**{k: None for k in _ALL_KEYS}, **constrained}


class NullConditionSemanticsTests(TestCase):
    """A null condition means "unconstrained", not "matches nothing".

    `_sets_overlap(None, None)` returned False, which set has_overlap = False and
    dropped the pair. Under exclude_none=False every key is present-but-null, so
    that short-circuit fired on essentially every pair and silently disabled the
    checker: doc 733711bd reported 0 overlaps while carrying 134 pairs of
    identical-condition rules with different payouts.
    """

    def setUp(self):
        self.c = RuleOverlapChecker()

    def _pair(self, c1, c2, p1=10, p2=20):
        self.c.overlaps = []
        self.c._check_rule_pair("P", _rule("r1", c1, p1), _rule("r2", c2, p2))
        return self.c.overlaps

    def test_dense_identical_rules_are_critical_complete_overlap(self):
        conds = _dense(policy_type=["comprehensive"], state=["MH"])
        overlaps = self._pair(conds, dict(conds))
        self.assertEqual(len(overlaps), 1, overlaps)
        self.assertEqual(overlaps[0].overlap_type, "complete_overlap")
        self.assertEqual(overlaps[0].severity, OverlapSeverity.CRITICAL)

    def test_dense_disjoint_rules_still_disjoint(self):
        a = _dense(policy_type=["comprehensive"], state=["MH"])
        b = _dense(policy_type=["third-party"], state=["KA"])
        self.assertEqual(self._pair(a, b), [])

    def test_unconstrained_side_subsumes_constrained_side_but_is_partial(self):
        # A rule with no state filter matches every state, so it does intersect
        # the MH-scoped rule — but their conditions are not identical, so it is
        # a partial overlap and correctly silent.
        a = _dense(policy_type=["comprehensive"], state=["MH"])
        b = _dense(policy_type=["comprehensive"])
        self.assertEqual(self._pair(a, b), [])

    def test_both_null_dimension_excluded_from_all_dims(self):
        # policy_type null on both sides must not count as a constrained
        # dimension, or `set(overlapping_dims) == all_dims` never holds and a
        # true duplicate degrades to partial_overlap.
        conds = _dense(state=["MH"])
        overlaps = self._pair(conds, dict(conds))
        self.assertEqual(overlaps[0].overlap_type, "complete_overlap")
        self.assertNotIn("policy_type", overlaps[0].overlapping_dimensions)

    def test_range_pair_folds_to_label_for_complete_overlap(self):
        # overlapping_dims records ranges by label ("gvw"); all_dims must fold
        # gvw_start_range/gvw_end_range to the same label or they never match.
        conds = _dense(state=["MH"], gvw_start_range=0, gvw_end_range=3500)
        overlaps = self._pair(conds, dict(conds))
        self.assertEqual(overlaps[0].overlap_type, "complete_overlap")
        self.assertEqual(overlaps[0].severity, OverlapSeverity.CRITICAL)

    def test_dense_disjoint_ranges_stay_disjoint(self):
        a = _dense(state=["MH"], gvw_start_range=0, gvw_end_range=3500)
        b = _dense(state=["MH"], gvw_start_range=7500, gvw_end_range=12000)
        self.assertEqual(self._pair(a, b), [])

    def test_unconstrained_range_vs_constrained_range_is_partial(self):
        a = _dense(state=["MH"], gvw_start_range=0, gvw_end_range=3500)
        b = _dense(state=["MH"])
        self.assertEqual(self._pair(a, b), [])

    def test_single_field_null_vs_set_is_partial(self):
        a = _dense(state=["MH"], cpa="with-cpa")
        b = _dense(state=["MH"])
        self.assertEqual(self._pair(a, b), [])

    def test_null_is_not_treated_as_a_distinguishing_value(self):
        # The load-bearing case for null semantics: if a null field short-circuited
        # to "disjoint" (the old _sets_overlap(None, None) -> False behaviour),
        # these two identical rules would report nothing at all.
        a = _dense(state=["MH"], cpa=None, gvw_start_range=None)
        b = _dense(state=["MH"], cpa=None, gvw_start_range=None)
        overlaps = self._pair(a, b)
        self.assertEqual(len(overlaps), 1, overlaps)
        self.assertEqual(overlaps[0].severity, OverlapSeverity.CRITICAL)

    def test_single_field_differing_values_stay_disjoint(self):
        a = _dense(state=["MH"], cpa="with-cpa")
        b = _dense(state=["MH"], cpa="without-cpa")
        self.assertEqual(self._pair(a, b), [])

    def test_explicit_null_equals_omitted_key(self):
        # A sparse dict and a dense one describing the same rule must be judged
        # identical, not merely partially overlapping.
        sparse = {"state": ["MH"]}
        dense = _dense(state=["MH"])
        overlaps = self._pair(sparse, dense)
        self.assertEqual(len(overlaps), 1, overlaps)
        self.assertEqual(overlaps[0].overlap_type, "complete_overlap")

    def test_exclusion_buckets_still_disjoint_when_dense(self):
        # The GCCV (a)/(c) buckets must stay disjoint in production shape too.
        a = _dense(
            policy_type=["comprehensive"],
            gvw_start_range=0,
            gvw_end_range=3500,
            vehicle_make=["TATA", "Maruti", "Mahindra"],
        )
        c = _dense(
            policy_type=["comprehensive"],
            gvw_start_range=0,
            gvw_end_range=3500,
            vehicle_make_exclude=["TATA", "Maruti", "Mahindra"],
        )
        self.assertEqual(self._pair(a, c), [])
