"""
Tests for the carrier vocabulary (`cins.schema.carriers`) and the transformer's
`_transform_provider` step. Covers the failure mode that motivated B2: an LLM
or upstream returning a free-text provider name that the system must either
normalise to one of the 20 valid short codes or flag as UNKNOWN_PROVIDER.

Run from the backend container:
    python manage.py test cins.tests.test_carriers -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
from cins.schema.carriers import VALID_CARRIERS, normalize_carrier
from cins.schema.raw_extraction import RawProviderInfo


class NormalizeCarrierTests(TestCase):
    def test_carrier_count(self):
        # Sanity guard — must stay in sync with `VALID_CARRIERS` and the
        # prompt. Bump the literal here when adding/removing a carrier.
        self.assertEqual(len(VALID_CARRIERS), 20)

    def test_canonical_short_codes_pass_through(self):
        for code in ("FG", "Bajaj", "ICICI", "New India", "Zuno"):
            with self.subTest(code=code):
                self.assertEqual(normalize_carrier(code), code)

    def test_canonical_codes_case_insensitive(self):
        self.assertEqual(normalize_carrier("fg"), "FG")
        self.assertEqual(normalize_carrier("ICICI"), "ICICI")
        self.assertEqual(normalize_carrier("new india"), "New India")

    def test_full_names_resolve(self):
        cases = {
            "Future Generali": "FG",
            "Future Generali India Insurance": "FG",
            "ICICI Lombard": "ICICI",
            "HDFC Ergo": "HDFC",
            "Bajaj Allianz": "Bajaj",
            "New India Assurance": "New India",
            "Cholamandalam MS General Insurance": "Chola",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(normalize_carrier(name), expected)

    def test_abbreviations_resolve(self):
        self.assertEqual(normalize_carrier("FGI"), "FG")
        self.assertEqual(normalize_carrier("FGII"), "FG")
        self.assertEqual(normalize_carrier("BAGIC"), "Bajaj")
        self.assertEqual(normalize_carrier("RGICL"), "Reliance")
        self.assertEqual(normalize_carrier("NIACL"), "New India")
        self.assertEqual(normalize_carrier("ITGI"), "Iffco")

    def test_email_domains_resolve(self):
        cases = {
            "futuregenerali.in": "FG",
            "icicilombard.com": "ICICI",
            "bajajallianz.com": "Bajaj",
            "hdfcergo.com": "HDFC",
            "newindia.co.in": "New India",
        }
        for domain, expected in cases.items():
            with self.subTest(domain=domain):
                self.assertEqual(normalize_carrier(domain), expected)

    def test_rsgi_aliases_resolve(self):
        # Regression: intake 597b697c-... had Cc'd royalsundaram.in addresses
        # but the carrier (short code RSGI per business convention) had no
        # Royal Sundaram aliases — RSGI was previously misattributed to
        # Raheja QBE. Cover the canonical name, the misspelling, the email
        # domain, the bare short code, and the RSAI abbreviation.
        self.assertEqual(normalize_carrier("RSGI"), "RSGI")
        self.assertEqual(normalize_carrier("Royal Sundaram"), "RSGI")
        self.assertEqual(normalize_carrier("royal sundram"), "RSGI")
        self.assertEqual(normalize_carrier("royalsundaram.in"), "RSGI")
        self.assertEqual(normalize_carrier("RSAI"), "RSGI")

    def test_rebrand_resolves(self):
        # Edelweiss General was renamed to Zuno; both forms should map.
        self.assertEqual(normalize_carrier("edelweiss general"), "Zuno")
        self.assertEqual(normalize_carrier("Edelweiss General Insurance"), "Zuno")

    def test_longest_substring_fallback(self):
        # LLM occasionally appends "Company Limited" / "(India)" suffixes that
        # aren't in the alias map verbatim. The substring fallback should still
        # map the embedded canonical alias.
        self.assertEqual(
            normalize_carrier("Future Generali India Insurance Company Limited"),
            "FG",
        )
        self.assertEqual(
            normalize_carrier("ICICI Lombard General Insurance Co. Ltd."),
            "ICICI",
        )

    def test_brokers_and_aggregators_do_not_resolve(self):
        # Regression: the bug B2 fixes is the LLM extracting broker domains
        # like pbpartners.com as the carrier. These must NOT match.
        self.assertIsNone(normalize_carrier("pbpartners.com"))
        self.assertIsNone(normalize_carrier("policybazaar.com"))

    def test_garbage_and_empty_return_none(self):
        self.assertIsNone(normalize_carrier(""))
        self.assertIsNone(normalize_carrier("   "))
        self.assertIsNone(normalize_carrier(None))
        self.assertIsNone(normalize_carrier("random nonsense"))


class TransformProviderTests(TestCase):
    def setUp(self):
        self.xform = RawToCICSTransformer()
        # Mirror Pipeline.process_from_indexed which calls .transform() —
        # warnings/errors lists are cleared there. We do the same per-test.
        self.xform.warnings = []
        self.xform.errors = []

    def test_hit_emits_canonical_short_code_and_full_name(self):
        prov = self.xform._transform_provider(RawProviderInfo(name="FG"))
        self.assertEqual(prov.provider_id, "FG")
        self.assertEqual(prov.provider_name, "Future Generali")
        self.assertEqual(self.xform.warnings, [])

    def test_multi_word_short_code_uses_underscore_in_id(self):
        # CICS Provider.provider_id pattern is ^[A-Za-z0-9_-]+$ — spaces aren't
        # allowed, so "New India" -> "New_India" for the ID. The full name in
        # provider_name still uses the human-readable form.
        prov = self.xform._transform_provider(
            RawProviderInfo(name="New India Assurance")
        )
        self.assertEqual(prov.provider_id, "New_India")
        self.assertEqual(prov.provider_name, "New India Assurance")

    def test_miss_flags_unknown_and_warns(self):
        prov = self.xform._transform_provider(
            RawProviderInfo(name="pbpartners.com")
        )
        self.assertEqual(prov.provider_id, "UNKNOWN_PROVIDER")
        self.assertEqual(prov.provider_name, "pbpartners.com")
        self.assertEqual(len(self.xform.warnings), 1)
        self.assertIn("pbpartners.com", self.xform.warnings[0])
        self.assertIn("UNKNOWN_PROVIDER", self.xform.warnings[0])

    def test_empty_input_is_silent(self):
        # Pass 1 is allowed to return "" when it can't confidently identify
        # the carrier — we should NOT spam warnings for that prescribed path.
        prov = self.xform._transform_provider(RawProviderInfo(name=""))
        self.assertEqual(prov.provider_id, "UNKNOWN_PROVIDER")
        self.assertEqual(self.xform.warnings, [])
