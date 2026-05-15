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
