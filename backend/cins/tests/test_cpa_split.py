"""
Tests for CPA-deduction row materialization (cins.processors.core.cpa_split).

A rule with no explicit CPA condition that carries a without-cpa
conditional_deduction clause is expanded at transform time into two rules:
With-CPA at payout x and Without-CPA at x minus the deduction (only when the
rule's commission column matches the clause's applied_on basis). The clause is
removed from both variants.

Run from the backend container:
    python manage.py test cins.tests.test_cpa_split -v 2
"""

import copy
from unittest import TestCase

from cins.processors.core.cpa_split import (
    applied_on_to_column,
    cpa_deduction_clause,
    deduct_pct,
    derive_applied_on,
    materialize_cpa_split,
)


CPA_CLAUSE = {
    "clause_type": "conditional_deduction",
    "text": "if CPA not collected then 1.5% of OD Pay-out deducted",
    "when": {"cpa": "without-cpa"},
    "effect": {
        "deduction": {
            "value": 1.5,
            "value_type": "percentage",
            "applied_on": "OD Pay-out",
        }
    },
}
OVERRIDE_OD = {
    "clause_type": "applied_on_override",
    "text": "Package pay-out is on OD Cover Premium",
    "when": {"coverage_type_text": "comprehensive"},
    "effect": {"applied_on": "OD Cover Premium"},
}
OVERRIDE_TP = {
    "clause_type": "applied_on_override",
    "text": "Liability pay-out is on TP/PA Premium",
    "when": {"coverage_type_text": "third party"},
    "effect": {"applied_on": "TP/PA Premium"},
}


def _rule(cpa, pct, clauses=None, policy_type=None, applied_on="Net Premium"):
    conditions = {"deal_parameter": "net", "cpa": cpa}
    if policy_type is not None:
        conditions["policy_type"] = policy_type
    outcome = {
        "commission_type": "percentage",
        "percentage_value": pct,
        "applied_on": applied_on,
    }
    if clauses is not None:
        outcome["qualifying_clauses"] = clauses
    return {"rule_id": "r", "conditions": conditions, "outcome": outcome}


def _doc(rules):
    return {"products": [{"product_name": "Private Car", "commission_rules": rules}]}


def _rules(doc):
    return doc["products"][0]["commission_rules"]


class HelperTests(TestCase):
    def test_applied_on_to_column(self):
        self.assertEqual(applied_on_to_column("OD Cover Premium"), "od")
        self.assertEqual(applied_on_to_column("OD Pay-out"), "od")
        self.assertEqual(applied_on_to_column("TP/PA Premium"), "tp")
        self.assertEqual(applied_on_to_column("Net Premium"), "net")
        self.assertIsNone(applied_on_to_column(None))
        self.assertIsNone(applied_on_to_column("Gross Premium"))

    def test_derive_applied_on_matches_coverage(self):
        outcome = {
            "applied_on": "Net Premium",
            "qualifying_clauses": [OVERRIDE_OD, OVERRIDE_TP],
        }
        self.assertEqual(
            derive_applied_on({"policy_type": ["comprehensive"]}, outcome),
            "OD Cover Premium",
        )
        self.assertEqual(
            derive_applied_on({"policy_type": ["third-party"]}, outcome),
            "TP/PA Premium",
        )
        self.assertEqual(
            derive_applied_on({"policy_type": ["other"]}, outcome), "Net Premium"
        )

    def test_cpa_deduction_clause(self):
        self.assertIs(cpa_deduction_clause([CPA_CLAUSE]), CPA_CLAUSE)
        self.assertIsNone(cpa_deduction_clause([OVERRIDE_OD]))
        self.assertIsNone(cpa_deduction_clause(None))

    def test_deduct_pct(self):
        self.assertEqual(deduct_pct(30, 1.5), 28.5)
        self.assertIsNone(deduct_pct(None, 1.5))
        self.assertEqual(deduct_pct("", 1.5), "")


class MaterializeTests(TestCase):
    def test_comprehensive_splits_and_deducts_on_od(self):
        doc = _doc(
            [_rule(None, 30, [OVERRIDE_OD, OVERRIDE_TP, CPA_CLAUSE], ["comprehensive"])]
        )
        materialize_cpa_split(doc)
        rules = _rules(doc)
        self.assertEqual(len(rules), 2)
        by_cpa = {r["conditions"]["cpa"]: r for r in rules}
        self.assertEqual(by_cpa["with-cpa"]["outcome"]["percentage_value"], 30)
        self.assertEqual(by_cpa["without-cpa"]["outcome"]["percentage_value"], 28.5)
        # Clause removed from both variants.
        for r in rules:
            types = [c["clause_type"] for c in r["outcome"]["qualifying_clauses"]]
            self.assertNotIn("conditional_deduction", types)

    def test_third_party_splits_without_deduction(self):
        # Basis is TP; the OD-Pay-out deduction is a no-op.
        doc = _doc(
            [_rule(None, 25, [OVERRIDE_OD, OVERRIDE_TP, CPA_CLAUSE], ["third-party"])]
        )
        materialize_cpa_split(doc)
        rules = _rules(doc)
        self.assertEqual(len(rules), 2)
        self.assertEqual({r["outcome"]["percentage_value"] for r in rules}, {25})

    def test_explicit_cpa_rule_not_split(self):
        doc = _doc([_rule("with-cpa", 30, [CPA_CLAUSE], ["comprehensive"])])
        materialize_cpa_split(doc)
        self.assertEqual(len(_rules(doc)), 1)

    def test_rule_without_clause_not_split(self):
        doc = _doc([_rule(None, 30, [OVERRIDE_OD], ["comprehensive"])])
        materialize_cpa_split(doc)
        self.assertEqual(len(_rules(doc)), 1)

    def test_idempotent(self):
        doc = _doc(
            [_rule(None, 30, [OVERRIDE_OD, OVERRIDE_TP, CPA_CLAUSE], ["comprehensive"])]
        )
        materialize_cpa_split(doc)
        once = copy.deepcopy(doc)
        materialize_cpa_split(doc)
        self.assertEqual(doc, once)

    def test_mixed_doc_counts(self):
        # 1 splitting rule (+1) + 1 plain rule = 3 rows.
        doc = _doc(
            [
                _rule(
                    None, 30, [OVERRIDE_OD, OVERRIDE_TP, CPA_CLAUSE], ["comprehensive"]
                ),
                _rule(None, 10, None, ["comprehensive"]),
            ]
        )
        materialize_cpa_split(doc)
        self.assertEqual(len(_rules(doc)), 3)
