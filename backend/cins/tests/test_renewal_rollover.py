"""CINS_RENEWAL_IMPLIES_ROLLOVER — a CV renewal rate also prices rollover.

A rollover IS a renewal that changes carrier, and no CV grid in the corpus
prices the two apart. Before this, a rule extracted from a renewal column
exported the bare ``renewal`` and a rollover quote matched no deal at all.

Pinned here: the CV-only scope, the untouched cases (no business_type at all,
"new", already-widened), idempotency, and that the widening happens BEFORE
duplicate collapse so same-rate twins merge while different-rate ones survive.
"""

import os
from unittest import TestCase, mock

from cins.processors.core.raw_to_cics_transformer import (
    CV_SUBPRODUCTS,
    RawToCICSTransformer,
)
from cins.schema.cics_model import (
    CommissionOutcome,
    CommissionRule,
    RuleConditions,
)


def _rule(rule_id: str, business_type, rate: float = 12.0, **cond) -> CommissionRule:
    return CommissionRule(
        rule_id=rule_id,
        priority=1,
        conditions=RuleConditions(business_type=business_type, **cond),
        outcome=CommissionOutcome(commission_type="percentage", percentage_value=rate),
    )


class RenewalImpliesRolloverTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_widens_on_every_cv_subproduct(self):
        for sub_product in CV_SUBPRODUCTS:
            with self.subTest(sub_product=sub_product):
                rules = [_rule("R1", ["renewal"])]
                self.t._imply_rollover_on_renewal(rules, sub_product)
                self.assertEqual(
                    rules[0].conditions.business_type, ["renewal", "rollover"]
                )

    def test_order_puts_renewal_first(self):
        # The export comma-joins the list verbatim, so order is the cell text.
        rules = [_rule("R1", ["new", "renewal"])]
        self.t._imply_rollover_on_renewal(rules, "commercial-miscd")
        self.assertEqual(
            rules[0].conditions.business_type, ["new", "renewal", "rollover"]
        )

    def test_idempotent(self):
        rules = [_rule("R1", ["renewal"])]
        for _ in range(3):
            self.t._imply_rollover_on_renewal(rules, "commercial-gcv")
        self.assertEqual(rules[0].conditions.business_type, ["renewal", "rollover"])

    def test_already_carrying_rollover_is_untouched(self):
        rules = [_rule("R1", ["rollover", "renewal"])]
        self.t._imply_rollover_on_renewal(rules, "commercial-pcv")
        self.assertEqual(rules[0].conditions.business_type, ["rollover", "renewal"])

    def test_absent_business_type_stays_absent(self):
        # None exports a BLANK BusinessType cell today; filling it would be a
        # different claim entirely, not a widening.
        for value in (None, []):
            with self.subTest(value=value):
                rules = [_rule("R1", value)]
                self.t._imply_rollover_on_renewal(rules, "commercial-buses")
                self.assertFalse(rules[0].conditions.business_type)

    def test_new_only_is_untouched(self):
        rules = [_rule("R1", ["new"])]
        self.t._imply_rollover_on_renewal(rules, "commercial-gcv")
        self.assertEqual(rules[0].conditions.business_type, ["new"])

    def test_non_cv_products_are_untouched(self):
        # Private Car and Two Wheeler grids DO sometimes price rollover apart;
        # widening there would fabricate an overlap.
        for sub_product in ("car", "two-wheeler", "", None):
            with self.subTest(sub_product=sub_product):
                rules = [_rule("R1", ["renewal"])]
                self.t._imply_rollover_on_renewal(rules, sub_product)
                self.assertEqual(rules[0].conditions.business_type, ["renewal"])

    @mock.patch.dict(os.environ, {"CINS_RENEWAL_IMPLIES_ROLLOVER": "false"})
    def test_flag_off_is_a_no_op(self):
        rules = [_rule("R1", ["renewal"])]
        self.t._imply_rollover_on_renewal(rules, "commercial-miscd")
        self.assertEqual(rules[0].conditions.business_type, ["renewal"])

    def test_emits_a_notice(self):
        rules = [_rule("R1", ["renewal"]), _rule("R2", ["new"])]
        self.t._imply_rollover_on_renewal(rules, "commercial-miscd")
        self.assertTrue(
            any("rollover" in n.lower() for n in self.t.notices), self.t.notices
        )


class RenewalRolloverCollapseTests(TestCase):
    """The widening runs BEFORE _collapse_duplicate_rules, on purpose."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_same_rate_twin_collapses(self):
        # A grid that spells one row "renewal" and another "renewal,rollover"
        # at the same rate is describing ONE deal — let the collapse merge them
        # rather than exporting a duplicate.
        rules = [
            _rule("R1", ["renewal"], rate=12.0),
            _rule("R2", ["renewal", "rollover"], rate=12.0),
        ]
        self.t._imply_rollover_on_renewal(rules, "commercial-miscd")
        self.assertEqual(len(self.t._collapse_duplicate_rules(rules)), 1)

    def test_different_rate_twins_both_survive(self):
        # Genuine conflict — the overlap checker's job to report, not ours to
        # silently pick a winner.
        rules = [
            _rule("R1", ["renewal"], rate=12.0),
            _rule("R2", ["renewal", "rollover"], rate=15.0),
        ]
        self.t._imply_rollover_on_renewal(rules, "commercial-miscd")
        self.assertEqual(len(self.t._collapse_duplicate_rules(rules)), 2)
