"""
Regression tests for format_other_conditions used by the CICS Excel export.

The free-text other_conditions blob arrives as one long line concatenated
from notes/master tables. The export renders it as a pretty-printed JSON
array — each clause on its own line, quoted, machine-parseable. This is
the primary human-review surface for Chola CPA-style footnotes; the format
contract must not regress.

Run from the backend container:
    python manage.py test commission_intake.tests.test_format_other_conditions -v 2
"""

import json
from unittest import TestCase

from commission_intake.views.commission_document import (
    format_other_conditions,
    format_qualifying_clauses,
    split_other_conditions,
)


CHOLA_BLOB = (
    "Please note that the above pay-out grid - "
    " For Private Car Package policy pay-out  is on OD Cover Premium and "
    "Private Car Liability policy  Pay-out is on TP/PA Premium - "
    " For Private Car Package,SAOD & Liability policy CPA should be "
    "collected for individual Name Mandatorily if CPA is not collected "
    "for individual name then 1.5% of OD Pay-out will be deducted - "
    " For Private Car Electric Vehicle or LPG/CNG pay-out grid is same "
    "as Private Car Petrol grid"
)


class SplitOtherConditionsTests(TestCase):
    def test_empty_and_none(self):
        self.assertEqual(split_other_conditions(None), [])
        self.assertEqual(split_other_conditions(""), [])
        self.assertEqual(split_other_conditions("   "), [])

    def test_single_clause_no_bullets(self):
        self.assertEqual(
            split_other_conditions("tourist permit only"),
            ["tourist permit only"],
        )

    def test_dash_separator(self):
        self.assertEqual(
            split_other_conditions("clause one - clause two - clause three"),
            ["clause one", "clause two", "clause three"],
        )

    def test_pipe_separator(self):
        self.assertEqual(
            split_other_conditions("alpha | beta | gamma"),
            ["alpha", "beta", "gamma"],
        )

    def test_bullet_char_separator(self):
        self.assertEqual(
            split_other_conditions("alpha • beta • gamma"),
            ["alpha", "beta", "gamma"],
        )

    def test_whitespace_collapsed(self):
        self.assertEqual(
            split_other_conditions("alpha  \t bravo - charlie\n\ndelta"),
            ["alpha bravo", "charlie delta"],
        )

    def test_chola_blob_splits_into_four_clauses(self):
        clauses = split_other_conditions(CHOLA_BLOB)
        self.assertEqual(len(clauses), 4)
        self.assertIn("Please note that the above pay-out grid", clauses[0])
        self.assertIn("OD Cover Premium", clauses[1])
        self.assertIn(
            "if CPA is not collected for individual name then 1.5% of OD "
            "Pay-out will be deducted",
            clauses[2],
        )
        self.assertIn("Electric Vehicle or LPG/CNG", clauses[3])


class FormatOtherConditionsTests(TestCase):
    def test_empty_returns_empty_string(self):
        self.assertEqual(format_other_conditions(None), "")
        self.assertEqual(format_other_conditions(""), "")
        self.assertEqual(format_other_conditions("   "), "")

    def test_returns_pretty_printed_json_array(self):
        result = format_other_conditions("clause one - clause two")
        parsed = json.loads(result)
        self.assertEqual(parsed, ["clause one", "clause two"])
        # Pretty-printed: contains newlines between elements
        self.assertIn("\n", result)
        self.assertTrue(result.startswith("["))
        self.assertTrue(result.endswith("]"))

    def test_single_clause_still_valid_json_array(self):
        result = format_other_conditions("tourist permit only")
        self.assertEqual(json.loads(result), ["tourist permit only"])

    def test_chola_blob_is_machine_parseable(self):
        result = format_other_conditions(CHOLA_BLOB)
        clauses = json.loads(result)
        self.assertEqual(len(clauses), 4)
        self.assertIn(
            "if CPA is not collected for individual name then 1.5% of OD "
            "Pay-out will be deducted",
            clauses[2],
        )

    def test_non_ascii_preserved(self):
        result = format_other_conditions("café - naïve")
        self.assertEqual(json.loads(result), ["café", "naïve"])
        # ensure_ascii=False keeps the literal characters in the cell value
        self.assertIn("café", result)


class FormatQualifyingClausesTests(TestCase):
    def test_empty_and_none_and_non_list(self):
        self.assertEqual(format_qualifying_clauses(None), "")
        self.assertEqual(format_qualifying_clauses([]), "")
        self.assertEqual(format_qualifying_clauses("not a list"), "")
        self.assertEqual(format_qualifying_clauses({"clause_type": "x"}), "")

    def test_renders_pretty_json_array(self):
        clauses = [
            {
                "clause_type": "conditional_deduction",
                "text": "if CPA not collected for individual name, 1.5% OD Pay-out deducted",
                "when": {
                    "cpa": "without-cpa",
                    "school_bus_type": "on-individual-name",
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
        result = format_qualifying_clauses(clauses)
        parsed = json.loads(result)
        self.assertEqual(parsed, clauses)
        self.assertIn("\n", result)
        self.assertTrue(result.startswith("["))
        self.assertTrue(result.endswith("]"))

    def test_multiple_clauses_preserved_in_order(self):
        clauses = [
            {"clause_type": "applied_on_override", "text": "first"},
            {"clause_type": "conditional_deduction", "text": "second"},
            {"clause_type": "grid_alias", "text": "third"},
        ]
        parsed = json.loads(format_qualifying_clauses(clauses))
        self.assertEqual([c["text"] for c in parsed], ["first", "second", "third"])

    def test_non_ascii_preserved(self):
        clauses = [{"clause_type": "other", "text": "café & naïve"}]
        result = format_qualifying_clauses(clauses)
        self.assertIn("café", result)
