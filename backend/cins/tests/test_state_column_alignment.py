"""
State-column alignment validator (Chola "PO Grid-Apr 26" defect 1).

A Private Car grid row can trace to a correctly-labelled rule yet carry a rate
read from a NEIGHBOURING state's column (a generated parser filtered blank
spacer headers before indexing, so columns after a spacer drifted). The
validator asserts MEMBERSHIP: every rate emitted for state S must appear in S's
own source column. Membership under-reports and never over-reports.

Run from the backend container:
    python manage.py test cins.tests.test_state_column_alignment -v 2
"""

import json
import tempfile
from unittest import TestCase

from cins.validators.schema_validator import Severity
from cins.validators.state_column_alignment_validator import (
    StateColumnAlignmentValidator,
    _norm_pct,
)

_TITLE = "Private Car Package"

# AP / TN / JH columns, three CC bands. Each column's own rates are distinct.
_SHEET = """## Private Car Package
| PROD | SUBCLASS | AP > PACK | TN > PACK | JH > PACK |
| --- | --- | --- | --- | --- |
| PC [PACK] | UPTO 1000 CC | 0.30 | 0.32 | 0.28 |
| PC [PACK] | 1000 TO 1500 CC | 0.25 | 0.27 | 0.22 |
| PC [PACK] | ABOVE 1500 CC | 0.20 | 0.24 | 0.18 |
"""

_OWN = {
    "andhra-pradesh": {30.0, 25.0, 20.0},
    "tamil-nadu": {32.0, 27.0, 24.0},
    "jharkhand": {28.0, 22.0, 18.0},
}


def _rule(rid, state, values):
    # One rule per (state, value) for simplicity.
    return {
        "rule_id": rid,
        "priority": 1,
        "conditions": {"state": [state]},
        "outcome": {"commission_type": "percentage", "percentage_value": values},
    }


def _write(rules, traces=None, title=_TITLE):
    if traces is None:
        traces = [
            {
                "entity_type": "rule",
                "entity_id": r["rule_id"],
                "source_location": {"section": title},
            }
            for r in rules
        ]
    doc = {
        "products": [{"product_name": "Private Car", "commission_rules": rules}],
        "source_traces": traces,
    }
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(doc, tmp)
    tmp.close()
    return tmp.name


def _emit(state, values):
    """One rule per value for a state, distinct ids."""
    slug = state.replace("-", "")[:4]
    return [_rule(f"{slug}{i}", state, v) for i, v in enumerate(values)]


def _has_high(res):
    return any(i.severity == Severity.HIGH for i in res.errors)


def _has_medium(res):
    return any(i.severity == Severity.MEDIUM for i in res.warnings)


class StateColumnAlignmentTests(TestCase):
    def setUp(self):
        self.v = StateColumnAlignmentValidator()

    def _run(self, rules, sheet=_SHEET, traces=None):
        path = _write(rules, traces)
        return self.v.validate(path, {"Grid": sheet})

    def test_aligned_grid_clean(self):
        rules = (
            _emit("andhra-pradesh", _OWN["andhra-pradesh"])
            + _emit("tamil-nadu", _OWN["tamil-nadu"])
            + _emit("jharkhand", _OWN["jharkhand"])
        )
        res = self._run(rules)
        self.assertFalse(_has_high(res))
        self.assertFalse(_has_medium(res))

    def test_off_by_one_column_detected(self):
        # jharkhand emits tamil-nadu's column values -> 3 distinct missing, all
        # present in exactly one other column -> HIGH.
        rules = _emit("jharkhand", _OWN["tamil-nadu"])
        res = self._run(rules)
        self.assertTrue(_has_high(res))

    def test_states_sharing_rates_no_false_positive(self):
        # AP and TN share identical rates; emitting AP's own (== TN's) is clean.
        sheet = """## Private Car Package
| PROD | SUBCLASS | AP > PACK | TN > PACK | JH > PACK |
| --- | --- | --- | --- | --- |
| PC [PACK] | UPTO 1000 CC | 0.30 | 0.30 | 0.28 |
| PC [PACK] | 1000 TO 1500 CC | 0.25 | 0.25 | 0.22 |
| PC [PACK] | ABOVE 1500 CC | 0.20 | 0.20 | 0.18 |
"""
        rules = _emit("andhra-pradesh", {30.0, 25.0, 20.0})
        res = self._run(rules, sheet=sheet)
        self.assertFalse(_has_high(res))
        self.assertFalse(_has_medium(res))

    def test_without_cpa_variant_ignored(self):
        # A without-cpa rule carrying a rate absent everywhere must be filtered
        # out (CPA split subtracts the deduction). Fails without the filter.
        rule = _rule("W1", "jharkhand", 99.0)
        rule["conditions"]["cpa"] = ["without-cpa"]
        res = self._run([rule])
        self.assertFalse(_has_high(res))
        self.assertFalse(_has_medium(res))

    def test_zero_and_blank_cells_ignored(self):
        sheet = """## Private Car Package
| PROD | SUBCLASS | AP > PACK | TN > PACK | JH > PACK |
| --- | --- | --- | --- | --- |
| PC [PACK] | UPTO 1000 CC | 0.30 |  | 0 |
| PC [PACK] | 1000 TO 1500 CC | 0.25 | 0.27 | 0.22 |
| PC [PACK] | ABOVE 1500 CC | 0.20 | 0.24 | 0.18 |
"""
        rules = _emit("andhra-pradesh", {30.0, 25.0, 20.0})
        res = self._run(rules, sheet=sheet)
        self.assertFalse(_has_high(res))
        self.assertFalse(_has_medium(res))

    def test_single_missing_rate_is_medium(self):
        # One rate absent from jharkhand's column but present elsewhere -> MEDIUM,
        # not HIGH (needs >=3 distinct missing for HIGH).
        rules = _emit("jharkhand", {32.0})  # tamil-nadu's band-1 rate
        res = self._run(rules)
        self.assertFalse(_has_high(res))
        self.assertTrue(_has_medium(res))

    def test_tw_oem_grid_ignored_without_crash(self):
        # A TW "Hero OEM > 150cc" header is not a state x coverage header (150cc
        # is not a coverage token) -> sheet has no grid -> skipped, no crash.
        sheet = """## Two Wheeler
| PROD | Hero OEM > 150cc | Bajaj OEM > 150cc | TVS OEM > 150cc |
| --- | --- | --- | --- |
| TW | 0.30 | 0.32 | 0.28 |
"""
        rules = _emit("andhra-pradesh", {30.0})
        res = self._run(rules, sheet=sheet)
        self.assertFalse(_has_high(res))
        self.assertFalse(_has_medium(res))

    def test_untraceable_rules_skip_the_sheet(self):
        # No source_traces -> <half resolve -> INFO, no checks even though the
        # rates are misaligned.
        rules = _emit("jharkhand", _OWN["tamil-nadu"])
        path = _write(rules, traces=[])
        res = self.v.validate(path, {"Grid": _SHEET})
        self.assertFalse(_has_high(res))
        self.assertFalse(_has_medium(res))
        self.assertTrue(any(i.severity == Severity.INFO for i in res.info))

    def test_up_east_resolved_verbatim(self):
        # "UP- East > PACK" maps to uttar-pradesh (state segment taken verbatim,
        # not whitespace-split to "East"). Aligned emission is clean; the column
        # is proven mapped by _state_column_values.
        sheet = """## Private Car Package
| PROD | SUBCLASS | UP- East > PACK | TN > PACK | JH > PACK |
| --- | --- | --- | --- | --- |
| PC [PACK] | UPTO 1000 CC | 0.30 | 0.32 | 0.28 |
| PC [PACK] | 1000 TO 1500 CC | 0.25 | 0.27 | 0.22 |
| PC [PACK] | ABOVE 1500 CC | 0.20 | 0.24 | 0.18 |
"""
        own = self.v._state_column_values(sheet)
        self.assertIn("uttar-pradesh", own)
        self.assertEqual(own["uttar-pradesh"], {30.0, 25.0, 20.0})
        rules = _emit("uttar-pradesh", {30.0, 25.0, 20.0})
        res = self._run(rules, sheet=sheet)
        self.assertFalse(_has_high(res))
        self.assertFalse(_has_medium(res))

    def test_commercial_product_not_checked(self):
        # CV rates go through GVW dedupe / bucket bifurcation, so the emitted
        # rate no longer equals the raw cell — membership would false-positive.
        # The validator is Private-Car-scoped; a Commercial Vehicle product with
        # rates absent from every column must produce NO findings.
        rules = _emit("jharkhand", _OWN["tamil-nadu"])  # off-by-one shape
        doc = {
            "products": [
                {
                    "product_name": "Commercial Vehicle",
                    "sub_category": "GCV",
                    "commission_rules": rules,
                }
            ],
            "source_traces": [
                {
                    "entity_type": "rule",
                    "entity_id": r["rule_id"],
                    "source_location": {"section": _TITLE},
                }
                for r in rules
            ],
        }
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(doc, tmp)
        tmp.close()
        res = self.v.validate(tmp.name, {"Grid": _SHEET})
        self.assertFalse(_has_high(res))
        self.assertFalse(_has_medium(res))


class NormPctTests(TestCase):
    def test_fraction(self):
        self.assertAlmostEqual(_norm_pct("0.325"), 32.5)

    def test_already_percent(self):
        self.assertAlmostEqual(_norm_pct("32.5"), 32.5)

    def test_percent_suffix(self):
        self.assertAlmostEqual(_norm_pct("30%"), 30.0)

    def test_blank_and_zero(self):
        self.assertIsNone(_norm_pct(""))
        self.assertEqual(_norm_pct("0"), 0.0)
