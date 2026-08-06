"""
Tests for rule priority normalization (cins.processors.core.rule_priority).

Priorities are assigned once by the transformer on the pre-expansion entry list;
every fan-out clone copies the parent's value and _merge_products concatenates
per-sheet rule sets that each numbered themselves from 10. The normalization
pass renumbers the final rule list so priorities are unique per product without
disturbing their relative order.

Run from the backend container:
    python manage.py test cins.tests.test_rule_priority -v 2
"""

import copy
from unittest import TestCase

from cins.processors.core.rule_priority import normalize_rule_priorities


def _doc(*rule_groups):
    """Build a minimal CICS doc — one product per group of (rule_id, priority)."""
    return {
        "products": [
            {
                "product_code": f"PROD_{n}",
                "commission_rules": [
                    {"rule_id": rid, "priority": prio} for rid, prio in group
                ],
            }
            for n, group in enumerate(rule_groups)
        ]
    }


def _rules(doc, product_index=0):
    return [
        (r["rule_id"], r["priority"])
        for r in doc["products"][product_index]["commission_rules"]
    ]


class NormalizeRulePrioritiesTests(TestCase):
    def test_clone_fanout_collision_is_renumbered(self):
        """GCV/subclass clones share the parent's priority — split them apart."""
        doc = _doc([("R1", 10), ("R1_3W", 10), ("R2", 20)])

        normalize_rule_priorities(doc)

        self.assertEqual(_rules(doc), [("R1", 10), ("R1_3W", 20), ("R2", 30)])

    def test_cross_sheet_merge_shape(self):
        """Two sheets each numbered 10,20,30... concatenated by _merge_products."""
        doc = _doc(
            [("A1", 10), ("A2", 20), ("A3", 30), ("B1", 10), ("B2", 20)],
        )

        normalize_rule_priorities(doc)

        priorities = [p for _, p in _rules(doc)]
        self.assertEqual(len(set(priorities)), len(priorities))
        self.assertEqual(sorted(priorities), [10, 20, 30, 40, 50])
        # Rule order in the document is untouched; only the numbers change.
        # Ranking interleaves the two sheets by existing priority, ties broken
        # by original position (sheet A before sheet B).
        self.assertEqual(
            _rules(doc),
            [("A1", 10), ("A2", 30), ("A3", 50), ("B1", 20), ("B2", 40)],
        )

    def test_specificity_order_is_preserved(self):
        """Relative order from _compute_priorities survives renumbering."""
        doc = _doc([("MOST", 10), ("MID", 50), ("LEAST", 120)])

        normalize_rule_priorities(doc)

        self.assertEqual(_rules(doc), [("MOST", 10), ("MID", 20), ("LEAST", 30)])

    def test_missing_priority_sorts_last_and_gets_a_number(self):
        doc = {
            "products": [
                {
                    "product_code": "P",
                    "commission_rules": [
                        {"rule_id": "NONE", "priority": None},
                        {"rule_id": "ABSENT"},
                        {"rule_id": "SET", "priority": 20},
                    ],
                }
            ]
        }

        normalize_rule_priorities(doc)

        self.assertEqual(_rules(doc), [("NONE", 20), ("ABSENT", 30), ("SET", 10)])

    def test_idempotent(self):
        doc = _doc([("A", 10), ("B", 10), ("C", 40)])

        once = copy.deepcopy(normalize_rule_priorities(doc))
        twice = normalize_rule_priorities(copy.deepcopy(once))

        self.assertEqual(once, twice)

    def test_products_are_numbered_independently(self):
        doc = _doc([("A", 10), ("B", 10)], [("C", 70), ("D", 70)])

        normalize_rule_priorities(doc)

        self.assertEqual(_rules(doc, 0), [("A", 10), ("B", 20)])
        self.assertEqual(_rules(doc, 1), [("C", 10), ("D", 20)])

    def test_tolerates_malformed_input(self):
        for payload in (None, [], {}, {"products": None}, {"products": ["x"]}):
            self.assertEqual(normalize_rule_priorities(payload), payload)

        doc = {"products": [{"product_code": "P", "commission_rules": None}]}
        self.assertIs(normalize_rule_priorities(doc), doc)

    def test_non_dict_rules_are_skipped_not_dropped(self):
        doc = {
            "products": [
                {
                    "product_code": "P",
                    "commission_rules": [
                        {"rule_id": "A", "priority": 10},
                        "junk",
                        {"rule_id": "B", "priority": 10},
                    ],
                }
            ]
        }

        normalize_rule_priorities(doc)

        rules = doc["products"][0]["commission_rules"]
        self.assertEqual(len(rules), 3)
        self.assertEqual(rules[1], "junk")
        self.assertEqual(rules[0]["priority"], 10)
        self.assertEqual(rules[2]["priority"], 20)
