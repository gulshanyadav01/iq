"""
Tests for `RawToCICSTransformer._normalize_list_field`.

The function maps free-text values to canonical enum lists via a mappings dict.
Sorted-by-length matching alone is not enough: shorter keys that are substrings
of longer keys (e.g. "petrol" inside "non petrol") would double-match without
span masking, producing semantically wrong output like
`["non-petrol", "petrol"]` for the input `"Non Petrol"`. That bug surfaced on
Chola intake d81925b1 where Standalone OD `[D]` rules emitted
`fuel_type = ["cng", "diesel", "lpg", "petrol"]` after the non-petrol
expansion in the caller — petrol leaked in via the normalisation step.

Run from the backend container:
    python manage.py test cins.tests.test_normalize_list_field -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import (
    FUEL_TYPE_MAPPINGS,
    RawToCICSTransformer,
)


class NormalizeListFieldFuelTypeTests(TestCase):
    """Fuel-type normalisation must not double-match overlapping keys."""

    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def _normalize(self, text):
        return self.transformer._normalize_list_field(
            text, FUEL_TYPE_MAPPINGS, "fuel_type"
        )

    def test_non_petrol_does_not_include_petrol(self):
        result = self._normalize("Non Petrol")
        self.assertEqual(result, ["non-petrol"])
        self.assertNotIn("petrol", result)

    def test_non_petrol_hyphen_form(self):
        result = self._normalize("non-petrol")
        self.assertEqual(result, ["non-petrol"])
        self.assertNotIn("petrol", result)

    def test_plain_petrol_unaffected(self):
        result = self._normalize("Petrol")
        self.assertEqual(result, ["petrol"])

    def test_diesel_unaffected(self):
        result = self._normalize("Diesel")
        self.assertEqual(result, ["diesel"])

    def test_comma_separated_petrol_diesel(self):
        result = self._normalize("Petrol, Diesel")
        self.assertEqual(sorted(result), ["diesel", "petrol"])

    def test_electric_alias_ev(self):
        result = self._normalize("EV")
        self.assertEqual(result, ["electric"])

    def test_unknown_returns_none_and_warns(self):
        result = self._normalize("Hydrogen")
        self.assertIsNone(result)
        self.assertTrue(
            any("fuel_type" in w for w in self.transformer.warnings),
            "Expected an 'Unknown fuel_type' warning for unrecognised input",
        )
