"""
Row-coverage validator (Step 7).

Asserts every rate-bearing grid data row traces to >=1 emitted rule (the inverse
of SchemaValidator's forward trace check). Fixtures:
  - a known-lost rate-bearing body row → ERROR
  - a fully-covered sheet → clean
  - a declined 4_OTHERS row that emitted no 'others' rule → ERROR

Run from the backend container:
    python manage.py test cins.tests.test_row_coverage_validator -v 2
"""

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from cins.validators.row_coverage_validator import RowCoverageValidator


def _write_doc(rules):
    doc = {
        "products": [
            {
                "product_name": "Commercial Vehicle",
                "sub_category": "MISC-D",
                "commission_rules": rules,
            }
        ]
    }
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(doc, tmp)
    tmp.close()
    return tmp.name


def _rule(sub_class):
    return {
        "rule_id": f"r_{sub_class}",
        "conditions": {"vehicle_sub_class": [sub_class]},
        "outcome": {"commission_type": "percentage", "percentage_value": 10},
    }


# A minimal MSV grid: four body rows, one with a rate.
_MSV_SHEET = """## Commission Data Sheet: MSV
| PROD | SUBCLASS | ACT | PACK |
| --- | --- | --- | --- |
| MSV | 1_TRAC[RENEWAL] | 0.10 | 0.12 |
| MSV | 2_EXCAVATOR | 0.11 | 0.13 |
| MSV | 3_HARVESTOR | 0.14 | 0.15 |
| MSV | 4_OTHERS |  |  |
"""


class RowCoverageTests(TestCase):
    def setUp(self):
        self.v = RowCoverageValidator()

    def test_fully_covered_sheet_is_clean(self):
        path = _write_doc(
            [
                _rule("tractor"),
                _rule("excavators"),
                _rule("harvester"),
                _rule("others"),
            ]
        )
        result = self.v.validate(path, {"MSV": _MSV_SHEET})
        self.assertEqual(result.errors, [], [e.message for e in result.errors])

    def test_lost_rate_bearing_row_errors(self):
        # Harvestor rule missing → its rate-bearing row traces to nothing.
        path = _write_doc([_rule("tractor"), _rule("excavators"), _rule("others")])
        result = self.v.validate(path, {"MSV": _MSV_SHEET})
        msgs = " ".join(e.message for e in result.errors)
        self.assertIn("3_HARVESTOR", msgs)

    def test_declined_others_row_missing_rule_errors(self):
        # 4_OTHERS is an all-empty-rate DECLINED row that must still emit a rule.
        path = _write_doc([_rule("tractor"), _rule("excavators"), _rule("harvester")])
        result = self.v.validate(path, {"MSV": _MSV_SHEET})
        msgs = " ".join(e.message for e in result.errors)
        self.assertIn("4_OTHERS", msgs)

    def test_unreadable_cics_returns_warning_not_error(self):
        # A bad CICS path must degrade to a MEDIUM warning, never an ERROR
        # (these issues merge into the pipeline schema result).
        result = self.v.validate(
            "/nonexistent/does-not-exist.json", {"MSV": _MSV_SHEET}
        )
        self.assertTrue(result.is_valid)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.warnings), 1)

    def test_pure_weight_band_row_not_flagged(self):
        # A GCCV weight band has no verifiable body token → never flagged even
        # if unmatched (conservative, avoids false ERRORs).
        sheet = """## Commission Data Sheet: GCCV
| PROD | SUBCLASS | ACT |
| --- | --- | --- |
| GCCV | 2_UPTO_3.5T | 0.20 |
"""
        path = _write_doc([_rule("tractor")])
        result = self.v.validate(path, {"GCCV": sheet})
        self.assertEqual(result.errors, [])


class _StubIndexedDoc:
    def __init__(self, sheets):
        self._sheets = sheets

    def get_all_sheet_contents(self):
        return self._sheets


class _RaisingIndexedDoc:
    def get_all_sheet_contents(self):
        raise RuntimeError("boom")


class PipelinePlumbingTests(TestCase):
    """pipeline._run_validators threads indexed_doc into row-coverage and
    merges its issues into schema_result — without ever breaking the pipeline."""

    def test_row_coverage_merged_into_schema_result(self):
        from cins.processors.core.pipeline import _run_validators

        # Harvestor rate-bearing row present in the grid but no rule emitted.
        path = _write_doc([_rule("tractor"), _rule("excavators"), _rule("others")])
        schema_result, _ = _run_validators(
            Path(path), _StubIndexedDoc({"MSV": _MSV_SHEET})
        )
        self.assertIsNotNone(schema_result)
        msgs = " ".join(
            i.message for i in (schema_result.errors + schema_result.warnings)
        )
        self.assertIn("3_HARVESTOR", msgs)

    def test_row_coverage_failure_never_breaks_pipeline(self):
        from cins.processors.core.pipeline import _run_validators

        path = _write_doc([_rule("tractor")])
        # get_all_sheet_contents raises — _run_validators must still return.
        schema_result, overlap_result = _run_validators(
            Path(path), _RaisingIndexedDoc()
        )
        self.assertIsNotNone(schema_result)
