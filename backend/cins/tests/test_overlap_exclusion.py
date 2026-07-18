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

from cins.validators.rule_overlap_checker import RuleOverlapChecker


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

    def test_partial_subset_still_overlaps(self):
        # include not fully covered by exclude → genuine overlap remains.
        a = {**self._SHARED, "vehicle_make": ["TATA", "Foo"]}
        c = {**self._SHARED, "vehicle_make_exclude": ["TATA"]}
        overlaps = self._pair(a, c)
        self.assertTrue(overlaps, "TATA/Foo vs exclude(TATA) must still overlap on Foo")
