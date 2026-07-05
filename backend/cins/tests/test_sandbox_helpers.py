"""
Tests for codegen sandbox helpers exposed to LLM-generated parsers.

parse_nop_range extracts the number-of-policies tier qualifier off
business-type row labels seen on Chola TW grids:

    "NEW(UPTO 30 NOP)"  -> "UPTO 30"
    "NEW(30-100 NOP)"   -> "30-100"
    "NEW(100-500 NOP)"  -> "100-500"

Helpers live as a string literal in code_gen_executor._SANDBOX_HELPERS
because they're exec'd into a restricted namespace. We exec the same
string here against a permissive namespace so each helper can be
asserted as a pure function.

Run from the backend container:
    python manage.py test cins.tests.test_sandbox_helpers -v 2
"""

from unittest import TestCase

from cins.processors.core.code_gen_executor import _SANDBOX_HELPERS


class ParseNopRangeTests(TestCase):
    @classmethod
    def setUpClass(cls):
        ns: dict = {}
        exec(compile(_SANDBOX_HELPERS, "<helpers>", "exec"), ns)
        cls.parse_nop_range = staticmethod(ns["parse_nop_range"])

    def test_upto_with_parens(self):
        self.assertEqual(self.parse_nop_range("NEW(UPTO 30 NOP)"), "UPTO 30")

    def test_range_with_parens(self):
        self.assertEqual(self.parse_nop_range("NEW(30-100 NOP)"), "30-100")

    def test_higher_range_with_parens(self):
        self.assertEqual(self.parse_nop_range("NEW(100-500 NOP)"), "100-500")

    def test_above_with_parens(self):
        self.assertEqual(self.parse_nop_range("NEW(ABOVE 500 NOP)"), "ABOVE 500")

    def test_without_parens(self):
        self.assertEqual(self.parse_nop_range("NEW UPTO 30 NOP"), "UPTO 30")

    def test_label_without_nop_returns_empty(self):
        self.assertEqual(self.parse_nop_range("NEW"), "")
        self.assertEqual(self.parse_nop_range("ANNUAL"), "")
        self.assertEqual(self.parse_nop_range("ACT"), "")
        self.assertEqual(self.parse_nop_range(""), "")

    def test_to_separator_normalised_to_hyphen(self):
        self.assertEqual(self.parse_nop_range("NEW(30 TO 100 NOP)"), "30-100")

    def test_case_insensitive(self):
        self.assertEqual(self.parse_nop_range("new(upto 30 nop)"), "UPTO 30")

    def test_returns_empty_for_none(self):
        self.assertEqual(self.parse_nop_range(None), "")


class ParseCcRangeGvwGuardTests(TestCase):
    """parse_cc_range used to strip alpha chars indiscriminately, mangling
    GVW slugs like '2_UPTO_3.5T[Electric]' into junk CC ranges ('2-3.5'
    -> cc=(2,4) downstream). The guard makes parse_cc_range reciprocally
    reject tonnage / KG / GVW shapes so parse_gvw_range handles them.
    """

    @classmethod
    def setUpClass(cls):
        ns: dict = {}
        exec(compile(_SANDBOX_HELPERS, "<helpers>", "exec"), ns)
        cls.parse_cc_range = staticmethod(ns["parse_cc_range"])

    def test_existing_cc_headers_unchanged(self):
        self.assertEqual(self.parse_cc_range("150cc"), "150")
        self.assertEqual(self.parse_cc_range("150-350cc"), "150-350")
        self.assertEqual(self.parse_cc_range("SCOOTER"), "")
        self.assertEqual(self.parse_cc_range("Hero OEM"), "")

    def test_rejects_kw_power_bands(self):
        # kW rates an electric vehicle's motor power, not engine displacement.
        # Mapping it into CC produced garbage ranges (e.g. "7 KW" -> cc=(7,7))
        # that collapsed every EV band. kW headers must return "".
        self.assertEqual(self.parse_cc_range("> 16 KW"), "")
        self.assertEqual(self.parse_cc_range("7 KW Bike"), "")
        self.assertEqual(self.parse_cc_range("7 KW SCOOTER"), "")
        self.assertEqual(self.parse_cc_range("7 KW - 16 KW"), "")
        self.assertEqual(self.parse_cc_range("7KW"), "")

    def test_rejects_tonnage_slug(self):
        # Smoking gun from commission doc d81925b1:
        # '2_UPTO_3.5T[Electric]' -> '2-3.5' -> cc=(2,4) downstream.
        self.assertEqual(self.parse_cc_range("2_UPTO_3.5T[Electric]"), "")
        self.assertEqual(self.parse_cc_range("3_3.5T_TO_7.5T"), "")
        self.assertEqual(self.parse_cc_range("UPTO 3.5T"), "")
        self.assertEqual(self.parse_cc_range("3.5T TO 7.5T"), "")
        self.assertEqual(self.parse_cc_range("ABOVE 40T"), "")
        self.assertEqual(self.parse_cc_range("12T TO 16T"), "")

    def test_rejects_kg_units(self):
        self.assertEqual(self.parse_cc_range("3500 KG"), "")
        self.assertEqual(self.parse_cc_range("3500-7500 KG"), "")

    def test_rejects_explicit_gvw_marker(self):
        self.assertEqual(self.parse_cc_range("GVW < 2000"), "")
        self.assertEqual(self.parse_cc_range("GVW UPTO 3500"), "")

    def test_to_keyword_between_digits_not_tonnage(self):
        # 'TO' inside a CC range like '1000 TO 1500 CC' must not trigger
        # the tonnage guard. The 'T' is part of a word, not after a digit.
        self.assertEqual(self.parse_cc_range("1000 TO 1500 CC"), "1000-1500")

    def test_handles_none_and_empty(self):
        self.assertEqual(self.parse_cc_range(None), "")
        self.assertEqual(self.parse_cc_range(""), "")
        self.assertEqual(self.parse_cc_range("   "), "")
