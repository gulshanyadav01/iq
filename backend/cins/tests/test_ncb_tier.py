"""
Tests for the Chola "NCB > 25%" parallel-table tier (defect 3).

Chola Private Car ships each CC-band grid twice: a base set and a
'… - NCB GT 25%' set at higher rates. Both previously landed ncb=None and
collided on an identical condition key (250/467 exported PC rows ambiguous).
The NCB>25% rows are now marked ncb=True from the marker in source_section /
rule_text; _pair_ncb_tiers flips the paired base twin to ncb=False. cv()
renders True->"Yes", False->"No", so the export needs no change.

Run from the backend container:
    python manage.py test cins.tests.test_ncb_tier -v 2
"""

from unittest import TestCase

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


def _raw(entries):
    return RawExtraction(
        source_file="test.xlsx",
        source_type="excel",
        provider=RawProviderInfo(name="Chola"),
        start_date="2024-04-01",
        end_date="2024-04-30",
        commission_entries=entries,
    )


def _rule(rule_id, ncb=None, rate=10.0, **cond_kwargs):
    return CommissionRule(
        rule_id=rule_id,
        priority=10,
        conditions=RuleConditions(ncb=ncb, **cond_kwargs),
        outcome=CommissionOutcome(commission_type="percentage", percentage_value=rate),
    )


class NcbMarkerTests(TestCase):
    """The NCB>25% marker in source_section or the row label sets ncb=True."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_marker_in_source_section(self):
        entry = RawCommissionEntry(
            rule_text="Private Car UPTO 1000 CC AP PACK",
            commission_value="20%",
            product_name="Private Car",
            source_section="Private Car Package - NCB GT 25%",
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertIs(cond.ncb, True)

    def test_marker_in_row_label(self):
        entry = RawCommissionEntry(
            rule_text="Private Car UPTO 1000 CC[ NCB GT 25%] AP PACK",
            commission_value="20%",
            product_name="Private Car",
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertIs(cond.ncb, True)

    def test_base_table_stays_none(self):
        entry = RawCommissionEntry(
            rule_text="Private Car UPTO 1000 CC AP PACK",
            commission_value="20%",
            product_name="Private Car",
            source_section="Private Car Package",
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertIsNone(cond.ncb)


class PairNcbTiersTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_base_twin_flipped_to_false(self):
        rules = [
            _rule("NCB", ncb=True, rate=25.0, cc_start_range=0, cc_end_range=1000),
            _rule("BASE", ncb=None, rate=15.0, cc_start_range=0, cc_end_range=1000),
        ]
        self.t._pair_ncb_tiers(rules)
        by_id = {r.rule_id: r for r in rules}
        self.assertIs(by_id["NCB"].conditions.ncb, True)
        self.assertIs(by_id["BASE"].conditions.ncb, False)

    def test_unpaired_rules_keep_none(self):
        # No marked member in the group -> ncb stays None (no NCB dimension).
        rules = [
            _rule("A", ncb=None, cc_start_range=0, cc_end_range=1000),
            _rule("B", ncb=None, cc_start_range=1000, cc_end_range=1500),
        ]
        self.t._pair_ncb_tiers(rules)
        self.assertTrue(all(r.conditions.ncb is None for r in rules))

    def test_sod_rule_not_flipped_by_marked_package(self):
        # Collision scope, not product scope: a marked Package rule must NOT
        # stamp ncb=False on a SOD rule with a different condition key.
        rules = [
            _rule(
                "PKG_NCB",
                ncb=True,
                rate=25.0,
                policy_type=["comprehensive"],
                cc_start_range=0,
                cc_end_range=1000,
            ),
            _rule(
                "PKG_BASE",
                ncb=None,
                rate=15.0,
                policy_type=["comprehensive"],
                cc_start_range=0,
                cc_end_range=1000,
            ),
            _rule(
                "SOD",
                ncb=None,
                rate=12.0,
                policy_type=["saod"],
                cc_start_range=0,
                cc_end_range=1000,
            ),
        ]
        self.t._pair_ncb_tiers(rules)
        by_id = {r.rule_id: r for r in rules}
        self.assertIs(by_id["PKG_BASE"].conditions.ncb, False)
        self.assertIsNone(by_id["SOD"].conditions.ncb)

    def test_tiers_dedupe_independently(self):
        # ncb is a dedupe scalar field, so the True and False ladders normalize
        # their CC boundaries separately after pairing.
        rules = [
            _rule("N1", ncb=True, cc_start_range=0, cc_end_range=1000),
            _rule("N2", ncb=True, cc_start_range=1000, cc_end_range=1500),
            _rule("B1", ncb=None, cc_start_range=0, cc_end_range=1000),
            _rule("B2", ncb=None, cc_start_range=1000, cc_end_range=1500),
        ]
        self.t._pair_ncb_tiers(rules)
        self.t._dedupe_range_overlaps(rules)
        by_id = {r.rule_id: r for r in rules}
        # Each ladder bumps its own upper rung; they do not fuse.
        self.assertEqual(by_id["N2"].conditions.cc_start_range, 1001)
        self.assertEqual(by_id["B2"].conditions.cc_start_range, 1001)
        self.assertIs(by_id["B1"].conditions.ncb, False)


class ParseBoolFieldTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_warns_on_unknown(self):
        self.assertIsNone(self.t._parse_bool_field("maybe"))
        self.assertTrue(any("Unrecognised boolean" in w for w in self.t.warnings))

    def test_silent_on_blank(self):
        self.assertIsNone(self.t._parse_bool_field(""))
        self.assertIsNone(self.t._parse_bool_field("   "))
        self.assertIsNone(self.t._parse_bool_field(None))
        self.assertEqual(self.t.warnings, [])
