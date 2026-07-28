"""
Tests for `RawToCICSTransformer._expand_state_names` and the two-letter
state-abbreviation tables.

Chola grids label states with two-letter tokens (``BH``, ``AP``, ``TS`` …).
``STATE_NAME_MAPPINGS`` was missing ``bh``, so Bihar silently vanished on 30
rules. The parameterized token sweep below is the test that would have caught
that: every token in the Chola set must resolve to exactly one canonical state.

The sync test mechanizes what `master_data/services/rto.py`'s docstring only
states in prose — the two-letter blocks of the two tables must not drift.

Run from the backend container:
    python manage.py test cins.tests.test_state_expansion -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import (
    STATE_NAME_MAPPINGS,
    RawToCICSTransformer,
)
from cins.schema.raw_extraction import RawMasterDataEntry
from master_data.services.rto import RTO_PREFIX_TO_STATE_SLUG

# The full set of two-letter tokens Chola's Apr grid uses, plus BH.
CHOLA_STATE_TOKENS = [
    "ap",
    "ts",
    "tn",
    "ka",
    "mh",
    "ga",
    "gj",
    "jh",
    "od",
    "wb",
    "cg",
    "as",
    "ml",
    "tr",
    "ar",
    "nl",
    "sk",
    "dl",
    "pb",
    "hp",
    "jk",
    "up",
    "uk",
    "an",
    "rj",
    "mp",
    "bh",
]


class ExpandStateNamesTests(TestCase):
    def setUp(self):
        self.transformer = RawToCICSTransformer()

    def test_bh_resolves_to_bihar(self):
        self.assertEqual(self.transformer._expand_state_names("BH", None), ["bihar"])

    def test_chola_token_sweep_each_resolves_to_one_state(self):
        """Every Chola two-letter token resolves to exactly one canonical state."""
        for token in CHOLA_STATE_TOKENS:
            with self.subTest(token=token):
                result = self.transformer._expand_state_names(token, None)
                self.assertIsNotNone(result, f"{token} did not resolve")
                self.assertEqual(
                    len(result), 1, f"{token} resolved to {result} (expected one)"
                )

    def test_delhi_or_ncr_does_not_acquire_orissa(self):
        """`or` is deliberately absent so "Delhi or NCR" stays delhi-ncr only."""
        self.assertEqual(
            self.transformer._expand_state_names("Delhi or NCR", None),
            ["delhi-ncr"],
        )

    def test_unresolved_text_warns(self):
        self.assertIsNone(self.transformer._expand_state_names("Atlantis", None))
        self.assertIn("Unknown state: Atlantis", self.transformer.warnings)

    def test_single_mapping_stateless_rule_inherits(self):
        """One state_mapping entry: a state-less rule inherits its states."""
        md = [
            RawMasterDataEntry(entry_type="state_mapping", key="CAT A", values="AP,TS")
        ]
        self.assertEqual(
            self.transformer._expand_state_names("", md),
            ["andhra-pradesh", "telangana"],
        )

    def test_multi_mapping_stateless_rule_returns_none_and_warns(self):
        """Multiple state_mapping entries: no silent inheritance of the first."""
        md = [
            RawMasterDataEntry(entry_type="state_mapping", key="CAT A", values="AP,TS"),
            RawMasterDataEntry(entry_type="state_mapping", key="CAT B", values="MH,GJ"),
        ]
        self.assertIsNone(self.transformer._expand_state_names("", md))
        self.assertTrue(
            any("Ambiguous state fallback" in w for w in self.transformer.warnings)
        )


class AbbreviationTableSyncTests(TestCase):
    """The two-letter blocks of the cins and master_data tables must agree."""

    # Two-letter keys in the RTO table with no cins equivalent, by design.
    ALLOWED_RTO_ONLY = {"or"}  # "MH or GJ" would over-expand; see cins comment.

    @staticmethod
    def _two_letter(mapping):
        return {k: v for k, v in mapping.items() if len(k) == 2 and k.isalpha()}

    def test_shared_keys_map_to_same_slug(self):
        cins = self._two_letter(STATE_NAME_MAPPINGS)
        rto = self._two_letter(RTO_PREFIX_TO_STATE_SLUG)
        for key in cins.keys() & rto.keys():
            with self.subTest(key=key):
                self.assertEqual(cins[key], rto[key])

    def test_no_undocumented_drift(self):
        cins = set(self._two_letter(STATE_NAME_MAPPINGS))
        rto = set(self._two_letter(RTO_PREFIX_TO_STATE_SLUG))
        self.assertEqual(cins - rto, set(), "cins has two-letter keys rto lacks")
        self.assertEqual(rto - cins, self.ALLOWED_RTO_ONLY)
