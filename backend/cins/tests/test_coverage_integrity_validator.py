"""
Coverage-integrity validator (Chola Private Car ACT/PACK regression).

Guards the incident on doc dbe5b4ea ("July pvt car"): the Package grid's ACT
(third-party) column was read but tagged comprehensive, so third-party came out
at 0 while the source carried 24 non-blank ACT cells. Row-coverage stayed silent
(every row produced a rule). Fixtures:
  - source has non-blank ACT cells but 0 third-party rules -> HIGH (check A)
  - third-party rules present -> clean (check A passes)
  - a third-party rule carrying a CPA split -> HIGH (check B)
  - blank / zero ACT cells only -> clean (no false positive)
  - transformer mapping act -> third-party is intact

Run from the backend container:
    python manage.py test cins.tests.test_coverage_integrity_validator -v 2
"""

import json
import tempfile
from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import POLICY_TYPE_MAPPINGS
from cins.validators.coverage_integrity_validator import CoverageIntegrityValidator
from cins.validators.schema_validator import Severity


def _write_doc(rules):
    doc = {
        "products": [
            {
                "product_name": "Private Car",
                "commission_rules": rules,
            }
        ]
    }
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(doc, tmp)
    tmp.close()
    return tmp.name


def _rule(rule_id, policy_type, cpa=None):
    cond = {"policy_type": [policy_type]}
    if cpa is not None:
        cond["cpa"] = [cpa]
    return {
        "rule_id": rule_id,
        "conditions": cond,
        "outcome": {"commission_type": "percentage", "percentage_value": 10},
    }


# Private Car Package grid: two states, each with ACT + PACK sub-columns.
# ACT cells: AP/TS band1 = 0.2, MH/GA band1 = 0.25  -> 2 non-blank ACT cells.
_PACKAGE_SHEET = """## Commission Data Sheet: July pvt car
| PROD | SUBCLASS | AP/TS > ACT | AP/TS > PACK | MH/GA > ACT | MH/GA > PACK |
| --- | --- | --- | --- | --- | --- |
| PC [PACK] | 1000 TO 1500 CC | 0.2 | 0.4 | 0.25 | 0.45 |
"""

# Same header shape but every ACT cell blank/zero (the SOD-grid case).
_PACKAGE_SHEET_BLANK_ACT = """## Commission Data Sheet: July pvt car SOD
| PROD | SUBCLASS | AP/TS > ACT | AP/TS > PACK | MH/GA > ACT | MH/GA > PACK |
| --- | --- | --- | --- | --- | --- |
| PC [SOD] | 1000 TO 1500 CC[P] |  | 0.4 | 0 | 0.45 |
"""


def _has_high(result):
    return any(i.severity == Severity.HIGH for i in result.errors)


class CoverageIntegrityTests(TestCase):
    def setUp(self):
        self.v = CoverageIntegrityValidator()

    def test_act_cells_but_zero_third_party_is_high(self):
        # Doc emitted only comprehensive rules — the ACT column was misfiled.
        path = _write_doc([_rule("r1", "comprehensive"), _rule("r2", "comprehensive")])
        result = self.v.validate(path, {"July pvt car": _PACKAGE_SHEET})
        self.assertTrue(_has_high(result))
        self.assertTrue(
            any(
                "ACT" in i.message and "third-party" in i.message for i in result.errors
            )
        )

    def test_third_party_present_is_clean(self):
        path = _write_doc([_rule("r1", "comprehensive"), _rule("r2", "third-party")])
        result = self.v.validate(path, {"July pvt car": _PACKAGE_SHEET})
        self.assertFalse(_has_high(result))

    def test_third_party_rule_with_cpa_split_is_clean(self):
        # Inverted from test_third_party_rule_with_cpa_split_is_high. A CPA-split
        # third-party pair is the INTENDED output of cpa_split.materialize_cpa_split
        # since pipeline 1.8.0, so check B was removed rather than downgraded: it
        # would fire on every affected document, and a HIGH additionally blocks
        # cics_cache.should_cache from pinning them — making every Chola document
        # carrying the CPA footnote permanently un-cacheable.
        path = _write_doc(
            [
                _rule("r1", "third-party", cpa="with-cpa"),
                _rule("r2", "third-party", cpa="without-cpa"),
            ]
        )
        result = self.v.validate(path, {"July pvt car": _PACKAGE_SHEET})
        self.assertFalse(_has_high(result))
        self.assertEqual(result.errors, [])
        self.assertEqual(result.warnings, [])

    def test_blank_act_cells_no_false_positive(self):
        # SOD-shaped grid: ACT sub-columns blank/zero -> no ACT-orphan issue even
        # with 0 third-party rules.
        path = _write_doc([_rule("r1", "saod")])
        result = self.v.validate(path, {"July pvt car SOD": _PACKAGE_SHEET_BLANK_ACT})
        self.assertFalse(_has_high(result))

    def test_mapping_act_to_third_party_intact(self):
        self.assertEqual(POLICY_TYPE_MAPPINGS.get("act"), "third-party")
