"""
Tests for CPA-deduction row materialization (cins.processors.core.cpa_split).

A rule with no explicit CPA condition that carries a without-cpa
conditional_deduction clause is expanded at transform time into two rules:
With-CPA at payout x and Without-CPA at x minus the deduction. The deduction
comes off the rule's OWN payout column (od / net / tp), not off the premium
basis the clause names. The clause is removed from both variants.

Third-party rules split too. The negative case is where od/net and tp diverge,
and these tests pin both halves: od/net CLAMP at 0 (`deduct_pct`), while tp
DECLINES and exports the undeducted rate on both variants
(`deduct_pct_no_negative`).

Run from the backend container:
    python manage.py test cins.tests.test_cpa_split -v 2
"""

import copy
from unittest import TestCase

from cins.processors.core.cpa_split import (
    applied_on_to_column,
    cpa_deduction_clause,
    deduct_pct,
    deduct_pct_no_negative,
    derive_applied_on,
    drop_mirrored_third_party,
    materialize_cpa_split,
    _mirror_signature,
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

    def test_deduct_pct_no_negative(self):
        # The tp counterpart of deduct_pct: it DECLINES an over-large deduction
        # rather than flooring it, so the caller can export the grid rate on
        # both CPA variants instead of a 0% the carrier never published.
        self.assertEqual(deduct_pct_no_negative(30, 1.5), (28.5, True))
        # Exactly zero is not negative — a rate the deduction consumes
        # precisely is a real 0%, and IS applied.
        self.assertEqual(deduct_pct_no_negative(1.5, 1.5), (0.0, True))
        # Over-large: input handed straight back, applied=False.
        self.assertEqual(deduct_pct_no_negative(1.0, 1.5), (1.0, False))
        self.assertEqual(deduct_pct_no_negative(0.3, 1.5), (0.3, False))
        # Non-numeric passes through, and never counts as applied.
        self.assertEqual(deduct_pct_no_negative(None, 1.5), (None, False))
        self.assertEqual(deduct_pct_no_negative("", 1.5), ("", False))
        self.assertEqual(deduct_pct_no_negative("n/a", 1.5), ("n/a", False))


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

    def test_third_party_splits_and_deducts_on_tp(self):
        # Inverted from the old test_third_party_not_split, which asserted TP
        # rules stay single because "TP/PA payout is a statutory pass-through".
        # That was wrong about what the footnote deducts: it reduces the
        # BROKER's payout, not the statutory TP premium, so there is no
        # pass-through to protect and a TP rule splits like any other.
        #
        # The incident that test cited (Chola doc dbe5b4ea, third-party
        # 24 -> 48 rules) is the CORRECT count now: 24 TP grid cells that each
        # price two commercially different deals (CPA collected / not collected)
        # are 48 exportable deals. The single row it preserved exported the
        # merged label "With CPA,Without CPA" at the UNDEDUCTED rate, which
        # overstated the without-CPA payout by the full deduction on every one.
        doc = _doc(
            [_rule(None, 25, [OVERRIDE_OD, OVERRIDE_TP, CPA_CLAUSE], ["third-party"])]
        )
        materialize_cpa_split(doc)
        rules = _rules(doc)
        self.assertEqual(len(rules), 2)
        by_cpa = {r["conditions"]["cpa"]: r for r in rules}
        self.assertEqual(by_cpa["with-cpa"]["outcome"]["percentage_value"], 25)
        self.assertEqual(by_cpa["without-cpa"]["outcome"]["percentage_value"], 23.5)
        for r in rules:
            types = [c["clause_type"] for c in r["outcome"]["qualifying_clauses"]]
            self.assertNotIn("conditional_deduction", types)

    def test_third_party_without_clause_not_split(self):
        # Only CLAUSE-carrying rules split. This is what keeps the change
        # Chola-scoped in practice without a carrier-name check anywhere: no
        # other carrier in the corpus writes the CPA footnote.
        doc = _doc([_rule(None, 25, [OVERRIDE_TP], ["third-party"])])
        materialize_cpa_split(doc)
        rules = _rules(doc)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["outcome"]["percentage_value"], 25)
        self.assertIsNone(rules[0]["conditions"].get("cpa"))

    def test_third_party_negative_deduction_exports_undeducted_on_both(self):
        # A ~1% ACT payout cannot absorb a 1.5-point deduction. The row still
        # bifurcates (that is the whole point of the change), but flooring the
        # without-CPA variant to 0 would export a rate the carrier never
        # published, so BOTH variants carry the grid rate.
        doc = _doc(
            [_rule(None, 1.0, [OVERRIDE_OD, OVERRIDE_TP, CPA_CLAUSE], ["third-party"])]
        )
        materialize_cpa_split(doc)
        rules = _rules(doc)
        self.assertEqual(len(rules), 2)
        by_cpa = {r["conditions"]["cpa"]: r for r in rules}
        self.assertEqual(by_cpa["with-cpa"]["outcome"]["percentage_value"], 1.0)
        self.assertEqual(by_cpa["without-cpa"]["outcome"]["percentage_value"], 1.0)

    def test_third_party_negative_deduction_logs_warning(self):
        # The decline is silent in the output (both rows look like an ordinary
        # undeducted pair), so the log is the only signal of how often it fires
        # on real documents. Keep it.
        doc = _doc(
            [_rule(None, 1.0, [OVERRIDE_OD, OVERRIDE_TP, CPA_CLAUSE], ["third-party"])]
        )
        with self.assertLogs("cins.cpa_split", level="WARNING") as cm:
            materialize_cpa_split(doc)
        self.assertIn("kept UNDEDUCTED", cm.output[0])
        self.assertIn("1 third-party", cm.output[0])

    def test_third_party_net_applied_on_still_declines(self):
        # rule_col comes from applied_on, NOT policy_type: a third-party rule
        # with no third-party applied_on_override falls back to "Net Premium"
        # and would take the od/net clamp path if the branch keyed on the column
        # alone. It is the rule's COVERAGE that makes a 0% floor wrong, so the
        # tp branch tests _is_third_party too.
        doc = _doc([_rule(None, 1.0, [CPA_CLAUSE], ["third-party"])])
        materialize_cpa_split(doc)
        rules = _rules(doc)
        self.assertEqual(len(rules), 2)
        by_cpa = {r["conditions"]["cpa"]: r for r in rules}
        self.assertEqual(by_cpa["without-cpa"]["outcome"]["percentage_value"], 1.0)

    def test_od_negative_deduction_still_clamps_at_zero(self):
        # The tp no-negative rule must NOT leak into the od/net path: a 1.5-point
        # deduction on a 0.5%-1% Two Wheeler comprehensive grade still floors at
        # 0, which is what keeps a negative rate out of schema validation.
        doc = _doc(
            [
                _rule(
                    None, 1.0, [OVERRIDE_OD, OVERRIDE_TP, CPA_CLAUSE], ["comprehensive"]
                )
            ]
        )
        materialize_cpa_split(doc)
        by_cpa = {r["conditions"]["cpa"]: r for r in _rules(doc)}
        self.assertEqual(by_cpa["without-cpa"]["outcome"]["percentage_value"], 0.0)

    def test_third_party_notes_fallback_splits(self):
        # The notes fallback (_cpa_clause_from_notes) must reach the TP path too
        # — Chola CV/ACT grids carry the footnote only in other_conditions.
        rule = {
            "rule_id": "act",
            "conditions": {
                "deal_parameter": "net",
                "cpa": None,
                "policy_type": ["third-party"],
            },
            "outcome": {
                "commission_type": "percentage",
                "percentage_value": 27.5,
                "applied_on": "Net Premium",
                "other_conditions": (
                    "if CPA is not collected for individual name then 1.5% of "
                    "Net Payout will be deducted"
                ),
            },
        }
        doc = {"products": [{"product_name": "CV", "commission_rules": [rule]}]}
        materialize_cpa_split(doc)
        rules = doc["products"][0]["commission_rules"]
        self.assertEqual(len(rules), 2)
        by_cpa = {r["conditions"]["cpa"]: r for r in rules}
        self.assertEqual(by_cpa["with-cpa"]["outcome"]["percentage_value"], 27.5)
        self.assertEqual(by_cpa["without-cpa"]["outcome"]["percentage_value"], 26.0)

    def test_third_party_split_is_idempotent(self):
        doc = _doc(
            [_rule(None, 25, [OVERRIDE_OD, OVERRIDE_TP, CPA_CLAUSE], ["third-party"])]
        )
        materialize_cpa_split(doc)
        once = copy.deepcopy(doc)
        materialize_cpa_split(doc)
        self.assertEqual(doc, once)

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

    def test_dedup_still_runs_before_split_for_clause_carrying_tp(self):
        # Ordering guard. Now that third-party rules DO split, running the dedup
        # after the split would compare a deducted without-cpa value against its
        # comprehensive twin, stop recognising the mirror, and let a fabricated
        # pair through as TWO rows instead of zero. Dedup runs first, so a
        # fabricated TP rule is deleted even when it carries the CPA clause.
        t1 = _cov_rule("t1", "third-party", 42.5)
        t1["outcome"]["qualifying_clauses"] = [CPA_CLAUSE]
        doc = _cov_doc([_cov_rule("c1", "comprehensive", 42.5), t1])
        materialize_cpa_split(doc)
        tp = [
            r
            for r in doc["products"][0]["commission_rules"]
            if r["conditions"]["policy_type"] == ["third-party"]
        ]
        self.assertEqual(len(tp), 0)


class MirrorSignatureNcbTests(TestCase):
    """The NCB band is part of a rule's grid slot.

    `_mirror_signature` feeds `drop_mirrored_third_party`, which DELETES rules.
    Before `ncb_start_range`/`ncb_end_range` joined `_MIRROR_DIM_KEYS`, a base
    tier and its ">25%" twin — identical in every other dimension, which is the
    whole point of an NCB ladder — collapsed onto one signature. Measured on a
    real Chola document: 101 signature groups masked more than one distinct NCB
    band. That let a third-party rule mirror a comprehensive rule it does not
    duplicate, on a path whose only action is deletion.
    """

    def test_ncb_tiers_have_distinct_signatures(self):
        base = _cov_rule("c1", "comprehensive", 42.5)
        base["conditions"].update(ncb=False, ncb_start_range=0, ncb_end_range=0)
        marked = _cov_rule("c2", "comprehensive", 42.5)
        marked["conditions"].update(ncb=True, ncb_start_range=25, ncb_end_range=100)
        # Same dims, same payout — the band is the ONLY difference.
        self.assertNotEqual(_mirror_signature(base), _mirror_signature(marked))

    def test_no_bonus_row_and_its_mirror_have_distinct_signatures(self):
        # _pair_ncb_tiers emits these two at the SAME rate with the SAME floor,
        # so only ncb_end_range separates them. The ncb boolean is deliberately
        # NOT in _MIRROR_DIM_KEYS, so the end of the band has to carry it.
        no_bonus = _cov_rule("c1", "comprehensive", 42.5)
        no_bonus["conditions"].update(ncb=False, ncb_start_range=0, ncb_end_range=0)
        mirror = _cov_rule("c2", "comprehensive", 42.5)
        mirror["conditions"].update(ncb=True, ncb_start_range=0, ncb_end_range=24)
        self.assertNotEqual(_mirror_signature(no_bonus), _mirror_signature(mirror))

    def test_third_party_not_dropped_against_ncb_tiered_comprehensive(self):
        # The ACT rate is statutory: it carries no NCB dimension and its value
        # coincides with the comprehensive tier's. Without the band in the
        # signature this TP rule mirrors, and the whole TP set is deleted.
        comp = _cov_rule("c1", "comprehensive", 42.5)
        comp["conditions"].update(ncb=True, ncb_start_range=25, ncb_end_range=100)
        tp = _cov_rule("t1", "third-party", 42.5)
        doc = _cov_doc([comp, tp], product_name="Private Car")
        drop_mirrored_third_party(doc)
        kept = [
            r
            for r in doc["products"][0]["commission_rules"]
            if r["conditions"]["policy_type"] == ["third-party"]
        ]
        self.assertEqual(len(kept), 1, "genuine third-party rate was deleted")

    def test_gcv_null_subclass_does_not_widen_the_signature(self):
        # GCV rules now carry no vehicle_sub_class at all (_strip_gcv_subclass),
        # so that _MIRROR_DIM_KEYS member is null on every side. This path
        # DELETES rules, so confirm the remaining dimensions still discriminate:
        # a genuine ACT rate on a different GVW band must survive.
        comp = _cov_rule("c1", "comprehensive", 42.5)
        comp["conditions"].update(
            vehicle_sub_class=None, gvw_start_range=0, gvw_end_range=3500
        )
        tp = _cov_rule("t1", "third-party", 42.5)
        tp["conditions"].update(
            vehicle_sub_class=None, gvw_start_range=3501, gvw_end_range=7500
        )
        doc = _cov_doc([comp, tp], product_name="Commercial Vehicle")
        drop_mirrored_third_party(doc)
        kept = [
            r
            for r in doc["products"][0]["commission_rules"]
            if r["conditions"]["policy_type"] == ["third-party"]
        ]
        self.assertEqual(len(kept), 1, "genuine third-party rate was deleted")

    def test_band_absent_on_both_sides_still_dedupes(self):
        # Regression guard for the fix itself: documents with no NCB dimension
        # at all must keep the pre-existing fabricated-TP cleanup behaviour.
        doc = _cov_doc(
            [
                _cov_rule("c1", "comprehensive", 42.5),
                _cov_rule("t1", "third-party", 42.5),
            ]
        )
        drop_mirrored_third_party(doc)
        self.assertEqual(len(doc["products"][0]["commission_rules"]), 1)
