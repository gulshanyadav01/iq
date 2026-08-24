"""
Regression tests for materialize_boundary_normalization.

Every boundary pass in RawToCICSTransformer._transform_product runs once per
SHEET. A range ladder split across sheets therefore keeps its shared boundary:
the sheet holding the upper rungs has no lower rung to use as evidence, and
pipeline._merge_sheet_jsons concatenates the sheets after every pass has run.
Chola "PO Grid-Apr 26.eml": 7 third-party GCV rules shipped GVW
(40000, 43000) against a 20T_TO_40T rung published on another sheet.

Run from the backend container:
    python manage.py test cins.tests.test_merged_boundary_normalization -v 2
"""

import copy
from unittest import TestCase

from cins.processors.core.boundary_normalize import (
    materialize_boundary_normalization,
)

_BASE_CONDITIONS = {
    "cc_start_range": 0,
    "cc_end_range": 99999,
    "seating_capacity_start_range": 0,
    "seating_capacity_end_range": 99,
    "policies_count_start_range": 0,
    "policies_count_end_range": 999999,
    "number_of_wheels": ["4w-and-above"],
    "deal_parameter": ["net"],
}


def _rule(rule_id, gvw_start, gvw_end, **overrides):
    conditions = dict(_BASE_CONDITIONS)
    conditions["gvw_start_range"] = gvw_start
    conditions["gvw_end_range"] = gvw_end
    conditions.update(overrides)
    return {
        "rule_id": rule_id,
        "priority": 10,
        "rule_description": f"{rule_id} band",
        "conditions": conditions,
        "outcome": {
            "commission_type": "percentage",
            "percentage_value": 25.0,
            "applied_on": "Net Premium",
        },
    }


def _doc(rules):
    return {
        "schema_version": "1.0",
        "products": [
            {
                "product_code": "COMMERCIAL_VEHICLE_GCV",
                "product_name": "Commercial Vehicle",
                "product_subproduct": "commercial-gcv",
                "commission_rules": rules,
            }
        ],
    }


def _bands(doc):
    return [
        (r["conditions"]["gvw_start_range"], r["conditions"]["gvw_end_range"])
        for r in doc["products"][0]["commission_rules"]
    ]


class MergedBoundaryNormalizationTests(TestCase):
    def test_cross_sheet_shared_edge_bumps_start(self):
        """The lower rung's sheet supplies evidence for the upper rung's."""
        doc = _doc(
            [
                # From the full CV sheet: fuel-tagged, another state. Both
                # fields fall away for evidence purposes — state is dropped
                # from the relaxed key, and the upper rung's absent fuel_type
                # is a wildcard.
                _rule(
                    "CV_SHEET_R1",
                    20001,
                    40000,
                    policy_type=["third-party"],
                    fuel_type=["petrol", "diesel", "cng", "lpg"],
                    state=["andhra-pradesh"],
                ),
                # From the Act-Only sheet: fuel-less, and the lowest rung of
                # its own partial ladder, so its sheet had nothing to bump it
                # against.
                _rule(
                    "ACT_SHEET_R1",
                    40000,
                    43000,
                    policy_type=["third-party"],
                    state=["telangana"],
                ),
            ]
        )
        notices = []
        materialize_boundary_normalization(doc, notices=notices)

        self.assertEqual(_bands(doc), [(20001, 40000), (40001, 43000)])
        self.assertEqual(len(notices), 1)
        self.assertIn("Merged-document Boundary bump gvw_start_range", notices[0])
        self.assertIn("40000 -> 40001", notices[0])

    def test_no_sibling_end_leaves_band_alone(self):
        """Without a band ending at 40000 the start is not a shared tier edge."""
        doc = _doc(
            [
                _rule("CV_SHEET_R1", 20001, 39000, policy_type=["third-party"]),
                _rule("ACT_SHEET_R1", 40000, 43000, policy_type=["third-party"]),
            ]
        )
        notices = []
        materialize_boundary_normalization(doc, notices=notices)

        self.assertEqual(_bands(doc), [(20001, 39000), (40000, 43000)])
        self.assertEqual(notices, [])

    def test_incompatible_ladder_is_not_evidence(self):
        """A disjoint list slot means the two bands describe different ladders."""
        doc = _doc(
            [
                _rule(
                    "CV_SHEET_R1",
                    20001,
                    40000,
                    number_of_wheels=["3w"],
                    policy_type=["third-party"],
                ),
                _rule(
                    "ACT_SHEET_R1",
                    40000,
                    43000,
                    number_of_wheels=["4w-and-above"],
                    policy_type=["third-party"],
                ),
            ]
        )
        notices = []
        materialize_boundary_normalization(doc, notices=notices)

        self.assertEqual(_bands(doc), [(20001, 40000), (40000, 43000)])
        self.assertEqual(notices, [])

    def test_idempotent(self):
        """A second run on an already-monotonic ladder bumps nothing."""
        doc = _doc(
            [
                _rule("CV_SHEET_R1", 20001, 40000, policy_type=["third-party"]),
                _rule("ACT_SHEET_R1", 40000, 43000, policy_type=["third-party"]),
            ]
        )
        materialize_boundary_normalization(doc)
        once = copy.deepcopy(doc)

        second_notices = []
        materialize_boundary_normalization(doc, notices=second_notices)

        self.assertEqual(doc, once)
        self.assertEqual(second_notices, [])

    def test_non_range_fields_survive_untouched(self):
        """Only the range ints move; the rest of the rule dict is verbatim."""
        doc = _doc(
            [
                _rule("CV_SHEET_R1", 20001, 40000, policy_type=["third-party"]),
                _rule("ACT_SHEET_R1", 40000, 43000, policy_type=["third-party"]),
            ]
        )
        # An extra key no schema knows about must not be dropped by the shim.
        doc["products"][0]["commission_rules"][1]["source_trace_id"] = "T-42"
        before = copy.deepcopy(doc)

        materialize_boundary_normalization(doc)

        bumped = doc["products"][0]["commission_rules"][1]
        original = before["products"][0]["commission_rules"][1]
        self.assertEqual(bumped["source_trace_id"], "T-42")
        self.assertEqual(bumped["outcome"], original["outcome"])
        self.assertEqual(bumped["rule_id"], original["rule_id"])
        self.assertEqual(bumped["priority"], original["priority"])
        self.assertEqual(bumped["rule_description"], original["rule_description"])
        # Conditions differ on the bumped start and nothing else.
        diff = {
            k
            for k in bumped["conditions"]
            if bumped["conditions"][k] != original["conditions"].get(k)
        }
        self.assertEqual(diff, {"gvw_start_range"})

    def test_non_dict_input_passes_through(self):
        self.assertIsNone(materialize_boundary_normalization(None))
        self.assertEqual(materialize_boundary_normalization([]), [])
