"""
Regression tests for partition_global_modifiers used by the CICS Excel export.

A "global" modifier (other_conditions text or qualifying_clause) appears on
EVERY commission rule in the document. Those get hoisted onto a dedicated
"Global Rules" worksheet so per-row cells stay clean. Row-specific
modifiers (appear on a subset of rules) stay inline on those rows.

Run from the backend container:
    python manage.py test commission_intake.tests.test_partition_global_modifiers -v 2
"""

from unittest import TestCase

from commission_intake.views.commission_document import (
    partition_global_modifiers,
    _clause_key,
)


CPA_CLAUSE = {
    "clause_type": "conditional_deduction",
    "text": "if CPA not collected for individual name then 1.5% of OD Pay-out deducted",
    "when": {"cpa": "without-cpa"},
    "effect": {"deduction": {"value": 1.5, "value_type": "percentage", "applied_on": "OD Pay-out"}},
}
PACKAGE_CLAUSE = {
    "clause_type": "applied_on_override",
    "text": "Private Car Package pay-out is on OD Cover Premium",
    "when": {"coverage_type_text": "comprehensive"},
    "effect": {"applied_on": "OD Cover Premium"},
}


def _doc(rules):
    return {"products": [{"product_name": "Private Car", "commission_rules": rules}]}


def _rule(other=None, clauses=None):
    outcome = {"commission_type": "percentage", "percentage_value": 10.0}
    if other is not None:
        outcome["other_conditions"] = other
    if clauses is not None:
        outcome["qualifying_clauses"] = clauses
    return {"rule_id": "r", "priority": 10, "outcome": outcome}


class PartitionGlobalModifiersTests(TestCase):
    def test_empty_doc_returns_empty_sets(self):
        other, clauses = partition_global_modifiers({"products": []})
        self.assertEqual(other, set())
        self.assertEqual(clauses, set())

    def test_no_rules_returns_empty_sets(self):
        other, clauses = partition_global_modifiers(_doc([]))
        self.assertEqual(other, set())
        self.assertEqual(clauses, set())

    def test_other_conditions_on_every_rule_is_global(self):
        text = "shared note"
        doc = _doc([_rule(other=text), _rule(other=text), _rule(other=text)])
        other, _clauses = partition_global_modifiers(doc)
        self.assertEqual(other, {text})

    def test_other_conditions_repeated_subset_still_global(self):
        """note A shared on 2 rows → global; note B on 1 row → row-specific."""
        doc = _doc([_rule(other="note A"), _rule(other="note A"), _rule(other="note B")])
        other, _clauses = partition_global_modifiers(doc)
        self.assertEqual(other, {"note A"})

    def test_single_occurrence_not_global(self):
        doc = _doc([_rule(other="solo")])
        other, _clauses = partition_global_modifiers(doc)
        self.assertEqual(other, set())

    def test_clause_on_every_rule_is_global(self):
        doc = _doc([
            _rule(clauses=[CPA_CLAUSE]),
            _rule(clauses=[CPA_CLAUSE]),
            _rule(clauses=[CPA_CLAUSE]),
        ])
        _other, clauses = partition_global_modifiers(doc)
        self.assertEqual(clauses, {_clause_key(CPA_CLAUSE)})

    def test_clause_on_one_rule_not_global(self):
        """Single occurrence stays row-specific."""
        doc = _doc([_rule(clauses=[CPA_CLAUSE]), _rule(clauses=[])])
        _other, clauses = partition_global_modifiers(doc)
        self.assertEqual(clauses, set())

    def test_clause_shared_across_two_rules_is_global(self):
        """≥ 2 occurrences = global, even when some rules lack it.
        Matches real Chola docs where CPA clauses hit every Private Car
        rule but no Two Wheeler rule."""
        doc = _doc([
            _rule(clauses=[CPA_CLAUSE]),
            _rule(clauses=[CPA_CLAUSE]),
            _rule(clauses=[]),  # Two Wheeler rule with no footnotes
        ])
        _other, clauses = partition_global_modifiers(doc)
        self.assertEqual(clauses, {_clause_key(CPA_CLAUSE)})

    def test_mixed_global_and_row_specific(self):
        """Both CPA (3 rows) and Package (2 rows) cross the ≥2 threshold."""
        doc = _doc([
            _rule(clauses=[CPA_CLAUSE, PACKAGE_CLAUSE]),
            _rule(clauses=[CPA_CLAUSE, PACKAGE_CLAUSE]),
            _rule(clauses=[CPA_CLAUSE]),  # missing Package
        ])
        _other, clauses = partition_global_modifiers(doc)
        self.assertIn(_clause_key(CPA_CLAUSE), clauses)
        self.assertIn(_clause_key(PACKAGE_CLAUSE), clauses)

    def test_single_occurrence_among_repeated_stays_row_specific(self):
        doc = _doc([
            _rule(clauses=[CPA_CLAUSE, PACKAGE_CLAUSE]),
            _rule(clauses=[CPA_CLAUSE]),
        ])
        _other, clauses = partition_global_modifiers(doc)
        self.assertIn(_clause_key(CPA_CLAUSE), clauses)
        # Package appears only once → row-specific
        self.assertNotIn(_clause_key(PACKAGE_CLAUSE), clauses)

    def test_empty_other_conditions_not_counted(self):
        """Empty/None other_conditions strings don't count as a shared modifier."""
        doc = _doc([_rule(other=""), _rule(other=""), _rule(other="")])
        other, _clauses = partition_global_modifiers(doc)
        self.assertEqual(other, set())

    def test_across_multiple_products(self):
        """A modifier must appear on every rule across ALL products to be global."""
        doc = {
            "products": [
                {"commission_rules": [_rule(clauses=[CPA_CLAUSE]), _rule(clauses=[CPA_CLAUSE])]},
                {"commission_rules": [_rule(clauses=[CPA_CLAUSE])]},
            ]
        }
        _other, clauses = partition_global_modifiers(doc)
        self.assertEqual(clauses, {_clause_key(CPA_CLAUSE)})

    def test_min_shared_threshold_override(self):
        """Caller can demand 'on every rule' by passing min_shared=N."""
        doc = _doc([_rule(clauses=[CPA_CLAUSE]), _rule(clauses=[CPA_CLAUSE])])
        _o, with_2 = partition_global_modifiers(doc, min_shared=2)
        _o, with_3 = partition_global_modifiers(doc, min_shared=3)
        self.assertEqual(with_2, {_clause_key(CPA_CLAUSE)})
        self.assertEqual(with_3, set())


class ClauseKeyTests(TestCase):
    def test_same_clause_same_key(self):
        c1 = dict(CPA_CLAUSE)
        c2 = dict(CPA_CLAUSE)
        self.assertEqual(_clause_key(c1), _clause_key(c2))

    def test_key_independent_of_dict_insertion_order(self):
        c1 = {"clause_type": "x", "text": "y", "when": {"a": 1, "b": 2}}
        c2 = {"text": "y", "when": {"b": 2, "a": 1}, "clause_type": "x"}
        self.assertEqual(_clause_key(c1), _clause_key(c2))

    def test_different_clauses_different_keys(self):
        self.assertNotEqual(_clause_key(CPA_CLAUSE), _clause_key(PACKAGE_CLAUSE))
