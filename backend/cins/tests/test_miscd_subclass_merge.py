"""CINS_MISCD_SUBCLASS_MERGE — MISC-D bodies share one rule.

The client prices Agricultural Tractor and Harvester as their own deals and
treats every other MISC-D body as one bucket, so the per-body fan-out turned a
single "Loader, Excavator, Crane, Bulldozer, Road Rollers, Fork Lift Trucks"
grid cell into six deals at one rate — six rows to reconcile where the carrier
books one.

The load-bearing parts pinned here:

- the merge is per SOURCE rule, never across rules (two grid rows stay two);
- the merged list is widened to the carrier's whole MISC-D book from
  ``CarrierProductHierarchyMaster``, not just the bodies this document named;
- ...EXCEPT when two rest buckets are told apart by body type alone, where
  widening would price one vehicle twice;
- an unreadable master falls back to the rule's own bodies, never to empty;
- GCV / PCV / Buses keep the per-body fan-out;
- the flag off restores the old shape exactly.
"""

import os
from unittest import mock

from django.test import TestCase as DjangoTestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
from cins.schema.cics_model import CommissionOutcome, CommissionRule, RuleConditions
from master_data.models import CarrierProductHierarchyMaster

# A slice of the real chola_product_hierarchy.csv MISC-D block. "Excavaters" is
# the source's OWN typo and is expected to drop out of the vocabulary — the
# same dict fails it on the extraction side, so it could never match a rule.
MISCD_MASTER = [
    "Agricultural Tractor",
    "Harvester",
    "Crane",
    "Loader",
    "Road Rollers",
    "Fork Lift Trucks",
    "Ambulance",
    "Not Available",
]

BODIES = ["crane", "loader", "road-rollers", "fork-lift-trucks"]


def _rule(rule_id, sub_classes, rate=12.0, **cond):
    return CommissionRule(
        rule_id=rule_id,
        priority=1,
        conditions=RuleConditions(vehicle_sub_class=list(sub_classes), **cond),
        outcome=CommissionOutcome(commission_type="percentage", percentage_value=rate),
    )


def _subclasses(rules):
    return [tuple(r.conditions.vehicle_sub_class or []) for r in rules]


class MiscDSubclassMergeTests(DjangoTestCase):
    @classmethod
    def setUpTestData(cls):
        for body in MISCD_MASTER:
            CarrierProductHierarchyMaster.objects.create(
                carrier="Chola",
                product="CV",
                sub_product="MISC-D",
                vehicle_subclass=body,
                canonical_subclass=body,
            )
        # A GCV row, to prove the bucket filter is real.
        CarrierProductHierarchyMaster.objects.create(
            carrier="Chola",
            product="CV",
            sub_product="GCV",
            vehicle_subclass="Truck",
            canonical_subclass="Truck",
        )

    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def _out(self, rules, sub_product="commercial-miscd"):
        return self.t._bifurcate_subclass_buckets(rules, sub_product)

    def test_keeps_tractor_and_harvester_and_merges_the_rest(self):
        rules = self._out([_rule("R1", ["agricultural-tractor", "harvester"] + BODIES)])
        got = {frozenset(s) for s in _subclasses(rules)}
        self.assertIn(frozenset({"agricultural-tractor"}), got)
        self.assertIn(frozenset({"harvester"}), got)
        self.assertEqual(len(rules), 3)

    def test_merged_rule_carries_the_carriers_whole_misc_d_book(self):
        rules = self._out([_rule("R1", BODIES)])
        self.assertEqual(len(rules), 1)
        self.assertEqual(
            set(rules[0].conditions.vehicle_sub_class),
            # The whole master minus the two own-rule bodies, minus the
            # "Not Available" sentinel (it names no vehicle) and minus nothing
            # else. Ambulance is in even though this document never named it.
            {"crane", "loader", "road-rollers", "fork-lift-trucks", "ambulance"},
        )
        self.assertNotIn("agricultural-tractor", rules[0].conditions.vehicle_sub_class)
        self.assertNotIn("harvester", rules[0].conditions.vehicle_sub_class)
        self.assertNotIn("not-available", rules[0].conditions.vehicle_sub_class)
        self.assertNotIn("truck", rules[0].conditions.vehicle_sub_class)

    def test_merged_rule_id_is_stable_and_suffixed(self):
        rules = self._out([_rule("R1", BODIES)])
        self.assertEqual(rules[0].rule_id, "R1_SC_MISCD")

    def test_two_grid_rows_stay_two_rules(self):
        # The merge suppresses ONE rule's fan-out; it never merges across
        # source rules. These differ by coverage, so widening both is safe.
        rules = self._out(
            [
                _rule("R1", ["crane", "loader"], policy_type=["comprehensive"]),
                _rule("R2", ["road-rollers"], rate=15.0, policy_type=["third-party"]),
            ]
        )
        self.assertEqual(len(rules), 2)
        for r in rules:
            self.assertIn("ambulance", r.conditions.vehicle_sub_class)

    def test_a_lone_rest_body_row_is_widened(self):
        # THE real MSV grid shape: the source publishes one row per body
        # ("2_EXCAVATOR", "3_HARVESTOR", "1_TRAC"), so every MISC-D rule reaches
        # the merge already a singleton. Skipping singletons made this a no-op
        # on the only document the feature exists for.
        rules = self._out([_rule("R1", ["crane"])])
        self.assertEqual(len(rules), 1)
        self.assertEqual(
            set(rules[0].conditions.vehicle_sub_class),
            {"crane", "loader", "road-rollers", "fork-lift-trucks", "ambulance"},
        )
        self.assertEqual(rules[0].rule_id, "R1_SC_MISCD")

    def test_a_lone_tractor_or_harvester_row_passes_through(self):
        # Already the shape the client asked for — do not re-issue it under a
        # _SC_ rule_id, and never widen it into the rest bucket.
        for body in ("agricultural-tractor", "harvester"):
            with self.subTest(body=body):
                rules = self._out([_rule("R1", [body])])
                self.assertEqual(len(rules), 1)
                self.assertEqual(rules[0].conditions.vehicle_sub_class, [body])
                self.assertEqual(rules[0].rule_id, "R1")

    def test_body_only_conflict_is_not_widened(self):
        # Identical in every dimension but body, at different rates: widening
        # both to the full book would price one vehicle twice.
        rules = self._out(
            [
                _rule("R1", ["crane", "loader"], rate=10.0),
                _rule("R2", ["road-rollers", "fork-lift-trucks"], rate=12.0),
            ]
        )
        self.assertEqual(len(rules), 2)
        self.assertEqual(
            set(rules[0].conditions.vehicle_sub_class), {"crane", "loader"}
        )
        self.assertEqual(
            set(rules[1].conditions.vehicle_sub_class),
            {"road-rollers", "fork-lift-trucks"},
        )
        self.assertTrue(
            any("not widened" in n or "twice" in n for n in self.t.notices),
            self.t.notices,
        )

    def test_same_rate_duplicates_are_not_treated_as_a_conflict(self):
        # Same slot, same rate, different bodies — duplicates the collapse
        # merges, not a pricing conflict.
        rules = self._out(
            [
                _rule("R1", ["crane", "loader"]),
                _rule("R2", ["road-rollers", "fork-lift-trucks"]),
            ]
        )
        for r in rules:
            self.assertIn("ambulance", r.conditions.vehicle_sub_class)

    def test_singleton_outside_miscd_is_never_widened(self):
        rules = self._out([_rule("R1", ["crane"])], "commercial-gcv")
        self.assertEqual(rules[0].conditions.vehicle_sub_class, ["crane"])
        self.assertEqual(rules[0].rule_id, "R1")

    def test_stable_across_a_second_run(self):
        once = self._out([_rule("R1", BODIES)])
        twice = self.t._bifurcate_subclass_buckets(once, "commercial-miscd")
        self.assertEqual(_subclasses(once), _subclasses(twice))

    def test_source_rule_is_not_mutated(self):
        source = _rule("R1", BODIES)
        self._out([source])
        self.assertEqual(source.conditions.vehicle_sub_class, BODIES)

    def test_other_cv_subproducts_still_fan_out_per_body(self):
        for sub_product in ("commercial-gcv", "commercial-pcv", "commercial-buses"):
            with self.subTest(sub_product=sub_product):
                t = RawToCICSTransformer()
                t._carrier_code = "Chola"
                rules = t._bifurcate_subclass_buckets(
                    [_rule("R1", ["crane", "loader"])], sub_product
                )
                self.assertEqual(_subclasses(rules), [("crane",), ("loader",)])

    @mock.patch.dict(os.environ, {"CINS_MISCD_SUBCLASS_MERGE": "false"})
    def test_flag_off_restores_per_body_fan_out(self):
        rules = self._out([_rule("R1", ["crane", "loader"])])
        self.assertEqual(_subclasses(rules), [("crane",), ("loader",)])
        self.assertEqual([r.rule_id for r in rules], ["R1_SC_crane", "R1_SC_loader"])


class MiscDMergeWithoutMasterTests(DjangoTestCase):
    """Fail open: no seed, unreadable master, wrong carrier."""

    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def test_unseeded_master_falls_back_to_the_rules_own_bodies(self):
        rules = self.t._bifurcate_subclass_buckets(
            [_rule("R1", ["crane", "loader"])], "commercial-miscd"
        )
        self.assertEqual(len(rules), 1)
        self.assertEqual(
            set(rules[0].conditions.vehicle_sub_class), {"crane", "loader"}
        )

    def test_resolver_raising_falls_back_rather_than_failing(self):
        with mock.patch(
            "master_data.services.resolver.carrier_product_hierarchy",
            side_effect=RuntimeError("db down"),
        ):
            rules = self.t._bifurcate_subclass_buckets(
                [_rule("R1", ["crane", "loader"])], "commercial-miscd"
            )
        self.assertEqual(
            set(rules[0].conditions.vehicle_sub_class), {"crane", "loader"}
        )

    def test_never_emits_an_empty_sub_class(self):
        rules = self.t._bifurcate_subclass_buckets(
            [_rule("R1", ["crane", "loader"])], "commercial-miscd"
        )
        self.assertTrue(rules[0].conditions.vehicle_sub_class)
