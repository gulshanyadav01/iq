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
    drop_mirrored_third_party,
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

    def test_deduct_pct_clamped_at_zero(self):
        # A deduction exceeding the base commission (a 1.5% OD-CPA deduction on a
        # 0.5%-1% Two Wheeler grade) must floor at 0, never emit a negative rate
        # that fails schema validation. Carriers never pay negative commission.
        self.assertEqual(deduct_pct(1.0, 1.5), 0.0)
        self.assertEqual(deduct_pct(0.5, 1.5), 0.0)
        self.assertEqual(deduct_pct(1.5, 1.5), 0.0)


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

    def test_cv_splits_from_notes_fallback_and_deducts_on_net(self):
        # Chola CV rules carry NO structured conditional_deduction clause; the
        # footnote lives only in outcome.other_conditions ("...1.5% of Net
        # Payout will be deducted"). cpa_split must detect it from the notes
        # and deduct on the Net payout column. Regression: CV without-CPA was
        # left equal to with-CPA (overstated 1.5pp).
        cv_notes = (
            "For Commercial Vehicle Package/Liability policy is on Net Premium "
            "if CPA is not collected for individual name then 1.5% of Net "
            "Payout will be deducted"
        )
        rule = {
            "rule_id": "cv",
            "conditions": {"deal_parameter": "net", "cpa": None},
            "outcome": {
                "commission_type": "percentage",
                "percentage_value": 27.5,
                "applied_on": "Net Premium",
                "other_conditions": cv_notes,
            },
        }
        doc = {
            "products": [
                {"product_name": "Commercial Vehicle", "commission_rules": [rule]}
            ]
        }
        materialize_cpa_split(doc)
        rules = doc["products"][0]["commission_rules"]
        self.assertEqual(len(rules), 2)
        by_cpa = {r["conditions"]["cpa"]: r for r in rules}
        self.assertEqual(by_cpa["with-cpa"]["outcome"]["percentage_value"], 27.5)
        self.assertEqual(by_cpa["without-cpa"]["outcome"]["percentage_value"], 26.0)

    def test_no_notes_no_clause_is_noop(self):
        # A rule with neither a clause nor a deduction footnote is untouched.
        rule = {
            "rule_id": "cv",
            "conditions": {"deal_parameter": "net", "cpa": None},
            "outcome": {
                "commission_type": "percentage",
                "percentage_value": 27.5,
                "applied_on": "Net Premium",
                "other_conditions": "Generic note with no deduction clause.",
            },
        }
        doc = {"products": [{"product_name": "CV", "commission_rules": [rule]}]}
        materialize_cpa_split(doc)
        self.assertEqual(len(doc["products"][0]["commission_rules"]), 1)

    def test_third_party_not_split(self):
        # TP/PA payout is a statutory pass-through — never CPA-deducted. A TP
        # rule that still carries the CPA clause must NOT be split: splitting it
        # would emit two identical with-cpa/without-cpa variants (the deduction
        # is a no-op on the TP column), doubling the third-party rule count.
        # Regression: Chola doc dbe5b4ea emitted third-party 24 -> 48 this way.
        doc = _doc(
            [_rule(None, 25, [OVERRIDE_OD, OVERRIDE_TP, CPA_CLAUSE], ["third-party"])]
        )
        materialize_cpa_split(doc)
        rules = _rules(doc)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["outcome"]["percentage_value"], 25)
        # Left cpa-unconstrained (single rule, no with/without split).
        self.assertIsNone(rules[0]["conditions"].get("cpa"))

    def test_cv_net_splits_and_deducts_on_net(self):
        # Fix ii: a Commercial-Vehicle Net rule carrying the CPA clause must
        # deduct from its Net payout (the deduction follows the rule's own
        # payout column, even though the clause names "OD Pay-out").
        doc = _doc(
            [_rule(None, 20, [CPA_CLAUSE], ["package"], applied_on="Net Premium")]
        )
        materialize_cpa_split(doc)
        rules = _rules(doc)
        self.assertEqual(len(rules), 2)
        by_cpa = {r["conditions"]["cpa"]: r for r in rules}
        self.assertEqual(by_cpa["with-cpa"]["outcome"]["percentage_value"], 20)
        self.assertEqual(by_cpa["without-cpa"]["outcome"]["percentage_value"], 18.5)

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


def _cov_rule(rule_id, policy_type, value, make="Hero", cc=(0, 150)):
    return {
        "rule_id": rule_id,
        "conditions": {
            "policy_type": [policy_type],
            "state": ["andhra-pradesh"],
            "vehicle_make": [make],
            "cc_start_range": cc[0],
            "cc_end_range": cc[1],
        },
        "outcome": {"commission_type": "percentage", "percentage_value": value},
    }


def _cov_doc(rules, product_name="Two Wheeler"):
    return {"products": [{"product_name": product_name, "commission_rules": rules}]}


class MirrorDedupTests(TestCase):
    def test_all_third_party_mirror_are_dropped(self):
        # Fabricated: every third-party rule mirrors a comprehensive (dims+value).
        doc = _cov_doc(
            [
                _cov_rule("c1", "comprehensive", 42.5, make="Hero"),
                _cov_rule("c2", "comprehensive", 35.0, make="TVS"),
                _cov_rule("t1", "third-party", 42.5, make="Hero"),
                _cov_rule("t2", "third-party", 35.0, make="TVS"),
            ]
        )
        drop_mirrored_third_party(doc)
        rules = doc["products"][0]["commission_rules"]
        pts = [r["conditions"]["policy_type"] for r in rules]
        self.assertEqual(len(rules), 2)
        self.assertNotIn(["third-party"], pts)

    def test_partial_mirror_keeps_all_third_party(self):
        # Real coverage: one TP mirrors by coincidence, one has its own rate.
        doc = _cov_doc(
            [
                _cov_rule("c1", "comprehensive", 0.4, make="Hero"),
                _cov_rule("c2", "comprehensive", 0.5, make="TVS"),
                _cov_rule("t1", "third-party", 0.4, make="Hero"),  # coincidental mirror
                _cov_rule("t2", "third-party", 0.3, make="TVS"),  # genuine ACT rate
            ],
            product_name="Private Car",
        )
        drop_mirrored_third_party(doc)
        tp = [
            r
            for r in doc["products"][0]["commission_rules"]
            if r["conditions"]["policy_type"] == ["third-party"]
        ]
        self.assertEqual(len(tp), 2)  # both kept — product has a real TP column

    def test_no_comprehensive_keeps_third_party(self):
        doc = _cov_doc([_cov_rule("t1", "third-party", 0.3)])
        drop_mirrored_third_party(doc)
        self.assertEqual(len(doc["products"][0]["commission_rules"]), 1)

    def test_materialize_runs_dedup_first(self):
        # Fabricated TP must be gone AND never CPA-split.
        doc = _cov_doc(
            [
                _cov_rule("c1", "comprehensive", 42.5),
                _cov_rule("t1", "third-party", 42.5),
            ]
        )
        materialize_cpa_split(doc)
        tp = [
            r
            for r in doc["products"][0]["commission_rules"]
            if r["conditions"]["policy_type"] == ["third-party"]
        ]
        self.assertEqual(len(tp), 0)
