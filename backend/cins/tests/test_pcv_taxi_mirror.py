"""
Tests for the CV/PCV -> Private Car taxi mirror
(cins.processors.core.pcv_taxi_mirror).

A commercial-pcv rule whose vehicle_sub_class is exactly ["taxi"] is duplicated
into a sibling product carrying Product="Private Car" / SubProduct="Taxi", so
the export renders both deals. The commercial rows are untouched.

Run from the backend container:
    python manage.py test cins.tests.test_pcv_taxi_mirror -v 2
"""

import copy
import os
from unittest import TestCase, mock

from cins.processors.core.cpa_split import materialize_cpa_split
from cins.processors.core.pcv_taxi_mirror import (
    MIRROR_SUFFIX,
    materialize_pcv_taxi_mirror,
)
from cins.processors.core.rule_priority import normalize_rule_priorities


def _rule(rule_id, sub_classes, pct=10.0, **conditions):
    base = {"deal_parameter": "net", "state": "maharashtra"}
    base.update(conditions)
    if sub_classes is not None:
        base["vehicle_sub_class"] = sub_classes
    return {
        "rule_id": rule_id,
        "priority": 10,
        "conditions": base,
        "outcome": {
            "commission_type": "percentage",
            "percentage_value": pct,
            "applied_on": "Net Premium",
        },
    }


def _product(rules, subproduct="commercial-pcv", code="P1"):
    return {
        "product_code": code,
        "product_name": "Commercial Vehicle",
        "product_category": "motor",
        "sub_category": "PCV",
        "product_subproduct": subproduct,
        "vehicle_type": "pcv",
        "deal_type": "market-deal",
        "zone": "west",
        "commission_rules": rules,
    }


def _doc(products, traces=None):
    doc = {"provider": {"provider_id": "Chola"}, "products": products}
    if traces is not None:
        doc["source_traces"] = traces
    return doc


def _by_name(doc, name):
    return [p for p in doc["products"] if p["product_name"] == name]


class MirrorHappyPathTests(TestCase):
    def test_pcv_taxi_rule_is_mirrored_under_private_car(self):
        doc = _doc([_product([_rule("R1", ["taxi"])])])
        materialize_pcv_taxi_mirror(doc)

        self.assertEqual(len(doc["products"]), 2)
        mirror = _by_name(doc, "Private Car")[0]
        self.assertEqual(mirror["product_code"], "P1" + MIRROR_SUFFIX)
        self.assertEqual(mirror["sub_category"], "Taxi")
        self.assertEqual(mirror["product_subproduct"], "car")
        self.assertEqual(mirror["vehicle_type"], "car-taxi")
        self.assertEqual(mirror["product_category"], "motor")
        self.assertEqual(len(mirror["commission_rules"]), 1)

        cloned = mirror["commission_rules"][0]
        self.assertEqual(cloned["rule_id"], "R1" + MIRROR_SUFFIX)
        # The sub-class stays the lowercase slug — every downstream validator
        # and the export's renderer expect slugs, not display labels.
        self.assertEqual(cloned["conditions"]["vehicle_sub_class"], ["taxi"])
        self.assertEqual(cloned["conditions"]["state"], "maharashtra")
        self.assertEqual(cloned["outcome"]["percentage_value"], 10.0)

    def test_source_product_is_left_untouched(self):
        doc = _doc([_product([_rule("R1", ["taxi"])])])
        source_before = copy.deepcopy(doc["products"][0])
        materialize_pcv_taxi_mirror(doc)
        self.assertEqual(doc["products"][0], source_before)

    def test_deal_metadata_carries_across(self):
        doc = _doc([_product([_rule("R1", ["taxi"])])])
        materialize_pcv_taxi_mirror(doc)
        mirror = _by_name(doc, "Private Car")[0]
        self.assertEqual(mirror["deal_type"], "market-deal")
        self.assertEqual(mirror["zone"], "west")

    def test_only_taxi_rules_are_mirrored_from_a_mixed_product(self):
        doc = _doc(
            [
                _product(
                    [
                        _rule("R1", ["taxi"]),
                        _rule("R2", ["greater-than-six-seater"]),
                        _rule("R3", ["taxi"]),
                    ]
                )
            ]
        )
        materialize_pcv_taxi_mirror(doc)
        mirror = _by_name(doc, "Private Car")[0]
        self.assertEqual(
            [r["rule_id"] for r in mirror["commission_rules"]],
            ["R1" + MIRROR_SUFFIX, "R3" + MIRROR_SUFFIX],
        )

    def test_each_source_product_gets_its_own_mirror(self):
        doc = _doc(
            [
                _product([_rule("R1", ["taxi"])], code="P1"),
                _product([_rule("R2", ["taxi"])], code="P2"),
            ]
        )
        materialize_pcv_taxi_mirror(doc)
        mirrors = _by_name(doc, "Private Car")
        self.assertEqual(
            sorted(p["product_code"] for p in mirrors),
            ["P1" + MIRROR_SUFFIX, "P2" + MIRROR_SUFFIX],
        )

    def test_deep_copy_isolates_the_clone_from_its_source(self):
        doc = _doc([_product([_rule("R1", ["taxi"])])])
        materialize_pcv_taxi_mirror(doc)
        mirror = _by_name(doc, "Private Car")[0]
        mirror["commission_rules"][0]["outcome"]["percentage_value"] = 99.0
        mirror["commission_rules"][0]["conditions"]["vehicle_sub_class"].append("x")
        source_rule = doc["products"][0]["commission_rules"][0]
        self.assertEqual(source_rule["outcome"]["percentage_value"], 10.0)
        self.assertEqual(source_rule["conditions"]["vehicle_sub_class"], ["taxi"])


class MirrorNegativeCaseTests(TestCase):
    def test_other_pcv_subclasses_are_not_mirrored(self):
        for slug in (
            "greater-than-six-seater",
            "maxi-cab",
            "kaali-peeli-taxi",
            "auto-rickshaw",
            "e-rickshaw",
        ):
            with self.subTest(slug=slug):
                doc = _doc([_product([_rule("R1", [slug])])])
                materialize_pcv_taxi_mirror(doc)
                self.assertEqual(len(doc["products"]), 1)

    def test_taxi_bundled_with_another_subclass_is_not_mirrored(self):
        doc = _doc([_product([_rule("R1", ["taxi", "maxi-cab"])])])
        materialize_pcv_taxi_mirror(doc)
        self.assertEqual(len(doc["products"]), 1)

    def test_rule_with_no_subclass_is_not_mirrored(self):
        doc = _doc([_product([_rule("R1", None)])])
        materialize_pcv_taxi_mirror(doc)
        self.assertEqual(len(doc["products"]), 1)

    def test_non_pcv_products_are_never_mirrored(self):
        for subproduct in ("commercial-gcv", "car", "two-wheeler", "commercial-buses"):
            with self.subTest(subproduct=subproduct):
                doc = _doc([_product([_rule("R1", ["taxi"])], subproduct=subproduct)])
                materialize_pcv_taxi_mirror(doc)
                self.assertEqual(len(doc["products"]), 1)

    def test_non_dict_and_empty_docs_are_tolerated(self):
        self.assertEqual(materialize_pcv_taxi_mirror(None), None)
        self.assertEqual(materialize_pcv_taxi_mirror("nope"), "nope")
        self.assertEqual(materialize_pcv_taxi_mirror({}), {})
        self.assertEqual(
            materialize_pcv_taxi_mirror({"products": []}), {"products": []}
        )


class MirrorIdempotencyTests(TestCase):
    def test_running_twice_produces_one_mirror(self):
        doc = _doc([_product([_rule("R1", ["taxi"])])])
        materialize_pcv_taxi_mirror(doc)
        after_first = copy.deepcopy(doc)
        materialize_pcv_taxi_mirror(doc)
        self.assertEqual(doc, after_first)

    def test_an_existing_private_car_product_is_never_merged_into(self):
        """A real Private Car product must not absorb PCV rates — overlap
        detection is scoped by product_code."""
        real_pc = {
            "product_code": "PC1",
            "product_name": "Private Car",
            "sub_category": "Private Car",
            "product_subproduct": "car",
            "commission_rules": [_rule("PC_R1", ["private-car"], pct=20.0)],
        }
        doc = _doc([_product([_rule("R1", ["taxi"])]), real_pc])
        materialize_pcv_taxi_mirror(doc)
        self.assertEqual(len(real_pc["commission_rules"]), 1)
        self.assertEqual(len(_by_name(doc, "Private Car")), 2)


class MirrorSourceTraceTests(TestCase):
    def test_product_trace_is_cloned_onto_the_mirror(self):
        traces = [
            {"trace_id": "T1", "entity_type": "product", "entity_id": "P1"},
            {"trace_id": "T2", "entity_type": "rule", "entity_id": "R1"},
        ]
        doc = _doc([_product([_rule("R1", ["taxi"])])], traces=traces)
        materialize_pcv_taxi_mirror(doc)
        product_traces = {
            t["entity_id"]
            for t in doc["source_traces"]
            if t["entity_type"] == "product"
        }
        self.assertIn("P1" + MIRROR_SUFFIX, product_traces)
        self.assertIn("P1", product_traces)

    def test_missing_source_traces_key_is_tolerated(self):
        doc = _doc([_product([_rule("R1", ["taxi"])])])
        materialize_pcv_taxi_mirror(doc)
        self.assertNotIn("source_traces", doc)


class MirrorOrderingTests(TestCase):
    """The mirror runs AFTER materialize_cpa_split, so each mirrored row copies
    an already-split rule and is numerically identical to its commercial twin."""

    CPA_CLAUSE = {
        "clause_type": "conditional_deduction",
        "text": "if CPA not collected then 1.5% of Net Pay-out deducted",
        "when": {"cpa": "without-cpa"},
        "effect": {
            "deduction": {
                "value": 1.5,
                "value_type": "percentage",
                "applied_on": "Net Premium",
            }
        },
    }

    def test_cpa_split_then_mirror_gives_two_rows_on_each_side(self):
        rule = _rule("R1", ["taxi"], pct=10.0)
        rule["outcome"]["qualifying_clauses"] = [copy.deepcopy(self.CPA_CLAUSE)]
        doc = _doc([_product([rule])])

        materialize_cpa_split(doc)
        materialize_pcv_taxi_mirror(doc)
        normalize_rule_priorities(doc)

        cv_rules = doc["products"][0]["commission_rules"]
        pc_rules = _by_name(doc, "Private Car")[0]["commission_rules"]
        self.assertEqual(len(cv_rules), 2)
        self.assertEqual(len(pc_rules), 2)

        def by_cpa(rules):
            return {
                r["conditions"].get("cpa"): r["outcome"]["percentage_value"]
                for r in rules
            }

        # Identical numbers on both sides — that is the whole point of cloning
        # after the split rather than before it.
        self.assertEqual(by_cpa(cv_rules), by_cpa(pc_rules))

        # cpa_split deliberately leaves both CPA variants sharing one rule_id,
        # so the only thing to assert here is that the mirror's suffix keeps the
        # two products' id spaces disjoint.
        cv_ids = {r["rule_id"] for r in cv_rules}
        pc_ids = {r["rule_id"] for r in pc_rules}
        self.assertEqual(cv_ids & pc_ids, set())

    def test_priorities_are_renumbered_within_the_mirror(self):
        doc = _doc([_product([_rule("R1", ["taxi"]), _rule("R2", ["taxi"])])])
        materialize_pcv_taxi_mirror(doc)
        normalize_rule_priorities(doc)
        priorities = [
            r["priority"] for r in _by_name(doc, "Private Car")[0]["commission_rules"]
        ]
        self.assertEqual(len(priorities), len(set(priorities)))


class MirrorFlagTests(TestCase):
    def test_flag_off_disables_the_mirror(self):
        doc = _doc([_product([_rule("R1", ["taxi"])])])
        with mock.patch.dict(os.environ, {"CINS_PCV_TAXI_MIRROR": "false"}):
            materialize_pcv_taxi_mirror(doc)
        self.assertEqual(len(doc["products"]), 1)

    def test_flag_defaults_on(self):
        doc = _doc([_product([_rule("R1", ["taxi"])])])
        env = {k: v for k, v in os.environ.items() if k != "CINS_PCV_TAXI_MIRROR"}
        with mock.patch.dict(os.environ, env, clear=True):
            materialize_pcv_taxi_mirror(doc)
        self.assertEqual(len(doc["products"]), 2)
