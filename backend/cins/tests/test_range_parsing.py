"""
Regression tests for RawToCICSTransformer._parse_range_field.

These exercises target the GVW commercial-vehicle bug where labels like
"UPTO 3.5T", "3.5T TO 7.5T", and indexer slugs "2_UPTO_3.5T",
"3_3.5T_TO_7.5T" silently produced (0, 0) or treated "3.5T" as 3 kg.
The parser must:
  - Handle decimal magnitudes (3.5, 7.5, 12).
  - Convert tonnage units (T, TON, TONNE, MT) to kilograms (×1000).
  - Normalise underscore-separated slug tokens to whitespace.
  - Preserve existing CC-style behaviour ("UPTO 1000 CC", "1000 TO 1500").

Run from the backend container:
    python manage.py test cins.tests.test_range_parsing -v 2
"""

from unittest import TestCase

from cins.processors.core.code_gen_executor import _SANDBOX_HELPERS
from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer


class ParseRangeFieldTonneTests(TestCase):
    """GVW labels expressed in tonnes must convert to kilograms."""

    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_upto_decimal_tonne(self):
        self.assertEqual(
            self.transformer._parse_range_field("UPTO 3.5T"),
            (None, 3500),
        )

    def test_decimal_tonne_range(self):
        self.assertEqual(
            self.transformer._parse_range_field("3.5T TO 7.5T"),
            (3500, 7500),
        )

    def test_mixed_decimal_and_integer_tonne_range(self):
        self.assertEqual(
            self.transformer._parse_range_field("7.5T TO 12T"),
            (7500, 12000),
        )

    def test_integer_tonne_range(self):
        self.assertEqual(
            self.transformer._parse_range_field("12T TO 16T"),
            (12000, 16000),
        )

    def test_above_tonne_open_ended(self):
        self.assertEqual(
            self.transformer._parse_range_field("ABOVE 40T"),
            (40000, None),
        )

    def test_slug_token_upto(self):
        self.assertEqual(
            self.transformer._parse_range_field("2_UPTO_3.5T"),
            (None, 3500),
        )

    def test_slug_token_range(self):
        self.assertEqual(
            self.transformer._parse_range_field("3_3.5T_TO_7.5T"),
            (3500, 7500),
        )

    def test_slug_token_last_bucket(self):
        self.assertEqual(
            self.transformer._parse_range_field("7_20T_TO_40T"),
            (20000, 40000),
        )

    def test_tonne_unit_variants(self):
        self.assertEqual(
            self.transformer._parse_range_field("UPTO 3.5 TONNES"),
            (None, 3500),
        )
        self.assertEqual(
            self.transformer._parse_range_field("3.5 MT TO 7.5 MT"),
            (3500, 7500),
        )


class ParseRangeFieldBackwardCompatTests(TestCase):
    """Existing CC / plain-number behaviour must remain unchanged."""

    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_upto_plain_integer(self):
        self.assertEqual(
            self.transformer._parse_range_field("UPTO 1000 CC"),
            (None, 1000),
        )

    def test_to_separated_plain_integer_range(self):
        self.assertEqual(
            self.transformer._parse_range_field("1000 TO 1500 CC"),
            (1000, 1500),
        )

    def test_dash_separated_range(self):
        self.assertEqual(
            self.transformer._parse_range_field("3500-7500"),
            (3500, 7500),
        )

    def test_above_plain_integer(self):
        self.assertEqual(
            self.transformer._parse_range_field("ABOVE 1500 CC"),
            (1500, None),
        )

    def test_single_integer(self):
        self.assertEqual(
            self.transformer._parse_range_field("150"),
            (150, 150),
        )

    def test_none_returns_none_pair(self):
        self.assertEqual(
            self.transformer._parse_range_field(None),
            (None, None),
        )

    def test_empty_string_returns_none_pair(self):
        self.assertEqual(
            self.transformer._parse_range_field(""),
            (None, None),
        )

    def test_unparseable_text_returns_none_pair(self):
        self.assertEqual(
            self.transformer._parse_range_field("SCOOTER"),
            (None, None),
        )

    def test_dict_passthrough(self):
        self.assertEqual(
            self.transformer._parse_range_field({"min": 100, "max": 500}),
            (100, 500),
        )

    def test_tonne_keyword_does_not_trigger_on_to_substring(self):
        # "TO" inside a range separator must NOT trigger tonnage multiplier.
        self.assertEqual(
            self.transformer._parse_range_field("1000 TO 1500 CC"),
            (1000, 1500),
        )


class GvwSlugParityTests(TestCase):
    """`_gvw_slug_or_empty` (host) and the sandbox `parse_gvw_range` must agree
    on every label — their docstrings claim parity, so the two copies of the
    bracket-neutralisation and shape guards cannot be allowed to drift."""

    HEADERS = [
        "UPTO 3.5T",
        "3.5T TO 7.5T",
        "12T TO 16T",
        "ABOVE 40T",
        "3500-7500 KG",
        "GVW < 2000",
        "2_UPTO_3.5T",
        "2_UPTO_3.5T[Electric]",
        "GCCV",
        "SCOOTER",
        "150cc",
        "1000 TO 1500 CC",
        "UPTO 1000 CC[ NCB GT 25%]",
        "1000CC[Electric]",
        "7 KW",
        "> 16 KW",
        "[ NCB GT 25%]",
        "",
        "   ",
    ]

    @classmethod
    def setUpClass(cls):
        ns: dict = {}
        exec(compile(_SANDBOX_HELPERS, "<helpers>", "exec"), ns)
        cls.parse_gvw_range = staticmethod(ns["parse_gvw_range"])
        cls.transformer = RawToCICSTransformer()

    def test_parity(self):
        for header in self.HEADERS:
            with self.subTest(header=header):
                self.assertEqual(
                    self.transformer._gvw_slug_or_empty(header),
                    self.parse_gvw_range(header),
                )


class StrictBoundsTests(TestCase):
    """`strict_bounds=True` makes an EXCLUSIVE operator inclusive.

    Both ends of a range are inclusive everywhere downstream (the export,
    `rule_overlap_checker._ranges_overlap`), so ">150" has to become 151 or a
    150cc vehicle matches both it and the "UPTO 150 CC" rule that ends there.
    That +1 used to happen only in the de-overlap passes, which need a sibling
    rung as evidence — so an isolated ">150cc" rule shipped overlapping.
    """

    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_strict_gt_bumps_start(self):
        self.assertEqual(
            self.transformer._parse_range_field(">150", strict_bounds=True),
            (151, None),
        )

    def test_strict_gte_does_not_bump(self):
        # ">=" already includes its boundary — bumping it would LOSE a value.
        self.assertEqual(
            self.transformer._parse_range_field(">=150", strict_bounds=True),
            (150, None),
        )

    def test_strict_above_word_bumps_start(self):
        self.assertEqual(
            self.transformer._parse_range_field("ABOVE 1500 CC", strict_bounds=True),
            (1501, None),
        )

    def test_strict_above_tonnage_bumps_after_conversion(self):
        # The +1 is applied in the converted unit (kg), not to the tonnage.
        self.assertEqual(
            self.transformer._parse_range_field("ABOVE 3.5T", strict_bounds=True),
            (3501, None),
        )

    def test_strict_lt_drops_end(self):
        self.assertEqual(
            self.transformer._parse_range_field("< 1500", strict_bounds=True),
            (None, 1499),
        )

    def test_strict_lte_and_upto_unchanged(self):
        self.assertEqual(
            self.transformer._parse_range_field("<= 1500", strict_bounds=True),
            (None, 1500),
        )
        self.assertEqual(
            self.transformer._parse_range_field("UPTO 1500 CC", strict_bounds=True),
            (None, 1500),
        )

    def test_strict_does_not_touch_closed_bands(self):
        self.assertEqual(
            self.transformer._parse_range_field("150-350", strict_bounds=True),
            (150, 350),
        )

    def test_strict_does_not_touch_bare_number(self):
        # A bare "150cc" header is genuinely ambiguous (open low or open high);
        # only a sibling column resolves it, so parsing must not guess.
        self.assertEqual(
            self.transformer._parse_range_field("150", strict_bounds=True),
            (150, 150),
        )

    def test_default_is_off_so_seating_and_ncb_are_untouched(self):
        # Seating deliberately reads "ABOVE 6" as (6, 99) — the 6-seater belongs
        # to the upper band (see _recover_seating_range). The flag is opt-in
        # precisely so that call site keeps its grid-verbatim behaviour.
        self.assertEqual(
            self.transformer._parse_range_field("ABOVE 6"),
            (6, None),
        )
        self.assertEqual(
            self.transformer._parse_range_field("ABOVE 40T"),
            (40000, None),
        )
