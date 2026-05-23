"""
Regression tests for surfacing free-text other_conditions from raw extraction
into CICS CommissionOutcome.

The Chola commission documents publish a footnote of the form
"if CPA is not collected for individual name then 1.5% of OD Pay-out will be
deducted". The structured CICS schema cannot model a customer-type-dependent
percentage deduction off OD Pay-out (Deduction.conditions is plain string,
commission_value is non-negative, and the deduction parser is regex-bound to
"fee/charge"). To avoid silently dropping the clause, _transform_outcome
copies entry.other_conditions (and raw.global_other_conditions when distinct)
verbatim onto CommissionOutcome.other_conditions. The Excel export surfaces
it as the OtherConditions column.

This contract must not regress — losing this text loses real economic
information for downstream deal management.

Run from the backend container:
    python manage.py test cins.tests.test_other_conditions_surfacing -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
from cins.schema.raw_extraction import (
    RawCommissionEntry,
    RawExtraction,
    RawProviderInfo,
)


CHOLA_CPA_CLAUSE = (
    "if CPA is not collected for individual name then 1.5% of OD Pay-out "
    "will be deducted"
)


def _make_entry(**overrides) -> RawCommissionEntry:
    defaults = {
        "rule_text": "Private Car Comp 10%",
        "commission_value": "10%",
        "product_name": "Private Car",
        "product_type": "motor",
        "coverage_type_text": "comprehensive",
        "state_text": "Maharashtra",
        "applied_on_text": "Net Premium",
    }
    defaults.update(overrides)
    return RawCommissionEntry(**defaults)


def _make_raw(entries, **raw_overrides) -> RawExtraction:
    defaults = {
        "source_file": "chola.xlsx",
        "source_type": "excel",
        "provider": RawProviderInfo(name="Chola"),
        "start_date": "2026-01-01",
        "end_date": "2026-03-31",
        "commission_entries": entries,
    }
    defaults.update(raw_overrides)
    return RawExtraction(**defaults)


class OtherConditionsSurfacingTests(TestCase):
    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_entry_other_conditions_surfaced_on_outcome(self):
        entry = _make_entry(other_conditions=CHOLA_CPA_CLAUSE)
        outcome = self.transformer._transform_outcome(entry, _make_raw([entry]))
        self.assertEqual(outcome.other_conditions, CHOLA_CPA_CLAUSE)

    def test_global_other_conditions_surfaced_when_entry_empty(self):
        entry = _make_entry()
        raw = _make_raw([entry], global_other_conditions=CHOLA_CPA_CLAUSE)
        outcome = self.transformer._transform_outcome(entry, raw)
        self.assertEqual(outcome.other_conditions, CHOLA_CPA_CLAUSE)

    def test_entry_and_global_joined_when_distinct(self):
        entry = _make_entry(other_conditions="tourist permit only")
        raw = _make_raw([entry], global_other_conditions=CHOLA_CPA_CLAUSE)
        outcome = self.transformer._transform_outcome(entry, raw)
        self.assertIn("tourist permit only", outcome.other_conditions)
        self.assertIn(CHOLA_CPA_CLAUSE, outcome.other_conditions)
        self.assertEqual(
            outcome.other_conditions,
            f"tourist permit only | {CHOLA_CPA_CLAUSE}",
        )

    def test_duplicate_text_not_repeated(self):
        entry = _make_entry(other_conditions=CHOLA_CPA_CLAUSE)
        raw = _make_raw([entry], global_other_conditions=CHOLA_CPA_CLAUSE)
        outcome = self.transformer._transform_outcome(entry, raw)
        self.assertEqual(outcome.other_conditions, CHOLA_CPA_CLAUSE)

    def test_no_other_conditions_leaves_field_unset(self):
        entry = _make_entry()
        outcome = self.transformer._transform_outcome(entry, _make_raw([entry]))
        self.assertIsNone(outcome.other_conditions)

    def test_whitespace_only_text_leaves_field_unset(self):
        entry = _make_entry(other_conditions="   ")
        raw = _make_raw([entry], global_other_conditions="\t\n")
        outcome = self.transformer._transform_outcome(entry, raw)
        self.assertIsNone(outcome.other_conditions)

    def test_cpa_clause_does_not_leak_into_structured_fields(self):
        """The Chola CPA clause stays in other_conditions only. It must NOT
        be interpreted as a commission value, deduction, or condition."""
        entry = _make_entry(other_conditions=CHOLA_CPA_CLAUSE)
        outcome = self.transformer._transform_outcome(entry, _make_raw([entry]))
        self.assertEqual(outcome.percentage_value, 10.0)
        self.assertEqual(outcome.commission_type, "percentage")
        self.assertIsNone(outcome.deductions)


class QualifyingClausesSurfacingTests(TestCase):
    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_well_formed_clauses_surfaced_on_outcome(self):
        entry = _make_entry(
            qualifying_clauses=[
                {
                    "clause_type": "conditional_deduction",
                    "text": CHOLA_CPA_CLAUSE,
                    "when": {
                        "product_name": "Private Car",
                        "coverage_type_text": ["comprehensive", "saod", "third party"],
                        "school_bus_type": "on-individual-name",
                        "cpa": "without-cpa",
                    },
                    "effect": {
                        "deduction": {
                            "value": 1.5,
                            "value_type": "percentage",
                            "applied_on": "OD Pay-out",
                        }
                    },
                }
            ]
        )
        outcome = self.transformer._transform_outcome(entry, _make_raw([entry]))
        self.assertIsNotNone(outcome.qualifying_clauses)
        self.assertEqual(len(outcome.qualifying_clauses), 1)
        clause = outcome.qualifying_clauses[0]
        self.assertEqual(clause.clause_type, "conditional_deduction")
        self.assertEqual(clause.when["cpa"], "without-cpa")
        self.assertEqual(clause.effect["deduction"]["value"], 1.5)

    def test_minimal_clause_with_only_type_and_text(self):
        entry = _make_entry(
            qualifying_clauses=[{"clause_type": "other", "text": "tied agents only"}]
        )
        outcome = self.transformer._transform_outcome(entry, _make_raw([entry]))
        self.assertEqual(len(outcome.qualifying_clauses), 1)
        self.assertIsNone(outcome.qualifying_clauses[0].when)
        self.assertIsNone(outcome.qualifying_clauses[0].effect)

    def test_empty_list_leaves_field_unset(self):
        entry = _make_entry(qualifying_clauses=[])
        outcome = self.transformer._transform_outcome(entry, _make_raw([entry]))
        self.assertIsNone(outcome.qualifying_clauses)

    def test_malformed_clause_dropped_with_warning(self):
        entry = _make_entry(
            qualifying_clauses=[
                {"clause_type": "applied_on_override"},  # missing required `text`
                {"clause_type": "other", "text": "valid one"},
            ]
        )
        outcome = self.transformer._transform_outcome(entry, _make_raw([entry]))
        self.assertEqual(len(outcome.qualifying_clauses), 1)
        self.assertEqual(outcome.qualifying_clauses[0].text, "valid one")
        self.assertTrue(
            any("qualifying_clause" in w for w in self.transformer.warnings)
        )

    def test_clauses_independent_of_other_conditions_text(self):
        """qualifying_clauses surfaces even when other_conditions text is empty."""
        entry = _make_entry(
            qualifying_clauses=[{"clause_type": "other", "text": "standalone"}]
        )
        outcome = self.transformer._transform_outcome(entry, _make_raw([entry]))
        self.assertIsNone(outcome.other_conditions)
        self.assertEqual(len(outcome.qualifying_clauses), 1)
