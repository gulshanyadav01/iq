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


class ParseCcRangeLtGtSlugTests(TestCase):
    """PCCV<6 4W-taxi CC-band slugs carry a leading ordinal + wheel token
    ('2_4W_LT_1500CC', '3_4W_GT_1500CC'). parse_cc_range must strip both and
    honour LT/GT as an explicit lower-/upper-open band — without folding the
    ordinal/wheel digits into the range. GVW/kW rejection stays intact."""

    @classmethod
    def setUpClass(cls):
        ns: dict = {}
        exec(compile(_SANDBOX_HELPERS, "<helpers>", "exec"), ns)
        cls.parse_cc_range = staticmethod(ns["parse_cc_range"])
        cls.parse_gvw_range = staticmethod(ns["parse_gvw_range"])

    def test_lt_band(self):
        self.assertEqual(self.parse_cc_range("2_4W_LT_1500CC"), "0-1500")

    def test_gt_band(self):
        self.assertEqual(self.parse_cc_range("3_4W_GT_1500CC"), ">1500")

    def test_electric_sibling_lt_band(self):
        self.assertEqual(self.parse_cc_range("2_4W_LT_1500CC[Electric]"), "0-1500")

    def test_plain_cc_range_untouched(self):
        # No underscore-slug shape → ordinal/wheel strip must not fire.
        self.assertEqual(self.parse_cc_range("150-350cc"), "150-350")
        self.assertEqual(self.parse_cc_range("ABOVE 1500 CC"), ">1500")
        # UPTO now yields an explicit lower-open band '0-1000' (was the bare
        # '1000', which the transformer read as the single point 1000cc). See
        # ParseCcRangeBracketQualifierTests / the UPTO branch in parse_cc_range.
        self.assertEqual(self.parse_cc_range("UPTO 1000 CC"), "0-1000")

    def test_gvw_guard_reciprocal(self):
        # The CC slug must be rejected by parse_gvw_range (belongs to CC).
        self.assertEqual(self.parse_gvw_range("2_4W_LT_1500CC"), "")

    def test_underscore_slug_without_lt_gt_or_number(self):
        # An underscore slug with neither LT/GT nor a CC number falls through
        # to the join path and yields no CC range (stable, not garbage).
        self.assertEqual(self.parse_cc_range("1_SCHOOL_BUS"), "")
        self.assertEqual(self.parse_cc_range("1_3W_AUTO"), "")


class ParseCcRangeBracketQualifierTests(TestCase):
    """Chola NCB>25% grids suffix CC labels with a bracketed qualifier
    ('UPTO 1000 CC[ NCB GT 25%]'). The bracket must be stripped before the digit
    scan; otherwise the trailing '25%]' token leaked in and produced
    cc=(1000,25) on 82 rules. The strip runs before the GVW/kW/slug guards, so
    tonnage and LT-slug regressions stay intact."""

    @classmethod
    def setUpClass(cls):
        ns: dict = {}
        exec(compile(_SANDBOX_HELPERS, "<helpers>", "exec"), ns)
        cls.parse_cc_range = staticmethod(ns["parse_cc_range"])
        cls.parse_gvw_range = staticmethod(ns["parse_gvw_range"])

    def test_upto_with_ncb_suffix(self):
        self.assertEqual(self.parse_cc_range("UPTO 1000 CC[ NCB GT 25%]"), "0-1000")

    def test_range_with_ncb_suffix(self):
        self.assertEqual(
            self.parse_cc_range("1000 TO 1500 CC[ NCB GT 25%]"), "1000-1500"
        )

    def test_above_with_ncb_suffix(self):
        self.assertEqual(self.parse_cc_range("ABOVE 1500 CC[ NCB GT 25%]"), ">1500")

    def test_bracket_p_parity(self):
        # A '[P]' (petrol) qualifier is stripped just like the NCB one.
        self.assertEqual(self.parse_cc_range("1000 TO 1500 CC[P]"), "1000-1500")

    def test_electric_slug_regression_guard(self):
        # Strip runs before the LT-slug branch, so the electric CV slug still
        # resolves to a lower-open CC band, not garbage.
        self.assertEqual(self.parse_cc_range("2_4W_LT_1500CC[Electric]"), "0-1500")
        # And the tonnage guard still fires for the electric GVW slug.
        self.assertEqual(self.parse_cc_range("2_UPTO_3.5T[Electric]"), "")

    def test_gvw_rejects_bracketed_cc_label(self):
        # parse_gvw_range must reject the bracketed CC label (belongs to CC),
        # not read it as a kilogram range via the qualifier fallback.
        self.assertEqual(self.parse_gvw_range("UPTO 1000 CC[ NCB GT 25%]"), "")

    def test_bracket_only_label(self):
        # A label that is nothing but a bracketed qualifier yields no CC range.
        self.assertEqual(self.parse_cc_range("[ NCB GT 25%]"), "")


class ParseCcRangeUnderscoreBandTests(TestCase):
    """An underscore-separated CC BAND is not an ordinal-prefixed slug.

    The Chola TW grid spells its middle column "150_350CC" — the sheet uses "_"
    where other grids write "-" — so the leading "150" is the band's LOWER
    BOUND, not an enumeration index. The ordinal strip used to eat it, leaving
    the point "350", which collided with the "350cc" column and left the ladder
    as two non-adjacent singletons the de-overlap passes correctly refuse to
    touch: the "150cc to 150cc" / "350cc to 350cc" the client reported.

    A real CC band never carries more than two numbers, which is what tells the
    two shapes apart."""

    @classmethod
    def setUpClass(cls):
        ns: dict = {}
        exec(compile(_SANDBOX_HELPERS, "<helpers>", "exec"), ns)
        cls.parse_cc_range = staticmethod(ns["parse_cc_range"])

    def test_underscore_band_keeps_its_lower_bound(self):
        self.assertEqual(self.parse_cc_range("150_350CC"), "150-350")
        self.assertEqual(self.parse_cc_range("150_350cc"), "150-350")

    def test_ordinal_prefixed_band_still_strips_the_ordinal(self):
        # Three numbers is one too many for a band, so the first is an index.
        self.assertEqual(self.parse_cc_range("1_150_350CC"), "150-350")

    def test_non_numeric_qualifier_still_strips_the_ordinal(self):
        # The wheel/LT/GT tokens are the unambiguous ordinal-slug signal.
        self.assertEqual(self.parse_cc_range("2_4W_LT_1500CC"), "0-1500")
        self.assertEqual(self.parse_cc_range("3_4W_GT_1500CC"), ">1500")

    def test_bare_single_column_labels_unchanged(self):
        self.assertEqual(self.parse_cc_range("150cc"), "150")
        self.assertEqual(self.parse_cc_range("350cc"), "350")

    def test_full_tw_ladder_is_three_distinct_rungs(self):
        # The three columns must parse to three DIFFERENT bands — the whole
        # point. Collapsing any two is what removed the ladder's evidence.
        bands = [self.parse_cc_range(h) for h in ("150cc", "150_350CC", "350cc")]
        self.assertEqual(bands, ["150", "150-350", "350"])
        self.assertEqual(len(set(bands)), 3)
