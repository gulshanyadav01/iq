"""
Tests for Chola UW-zone expansion in ``_expand_rto_codes`` /
``_expand_state_names`` (RTO + product-hierarchy masters change).

No DB: ``_carrier_zone_map`` is injected directly onto a plain
``RawToCICSTransformer()`` instance, mirroring the scoped-query shape
``transform()`` builds from ``CarrierRTOZoneMaster`` — these methods are
pure functions of that map plus ``_carrier_code``.

Run from the backend container:
    python manage.py test cins.tests.test_chola_zone_expansion -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer

_ZONE_MAP = {
    "mumbai": {"rto": ["MH01", "MH02"], "state": ["maharashtra"]},
    "central-mh": {"rto": ["MH06", "MH08"], "state": ["maharashtra"]},
    "up-east": {"rto": ["UP28", "UP32"], "state": ["uttar-pradesh"]},
    "ncr": {"rto": ["HR26", "UP14"], "state": ["haryana", "uttar-pradesh"]},
}


def _chola_transformer(zone_map=None):
    t = RawToCICSTransformer()
    t._carrier_code = "Chola"
    t._carrier_zone_map = zone_map if zone_map is not None else dict(_ZONE_MAP)
    t._zone_warned = set()
    t.warnings = []
    return t


class ZoneRtoExpansionTests(TestCase):
    def test_mumbai_resolves_mh_codes(self):
        t = _chola_transformer()
        self.assertEqual(t._expand_rto_codes("Mumbai", None), ["MH01", "MH02"])

    def test_mumbai_resolves_state(self):
        t = _chola_transformer()
        self.assertEqual(t._expand_state_names("Mumbai", None), ["maharashtra"])

    def test_ncr_resolves_both_states(self):
        t = _chola_transformer()
        self.assertEqual(
            t._expand_state_names("NCR", None), ["haryana", "uttar-pradesh"]
        )

    def test_ncr_resolves_mixed_prefix_codes(self):
        t = _chola_transformer()
        self.assertEqual(t._expand_rto_codes("NCR", None), ["HR26", "UP14"])

    def test_hyphenated_up_east_resolves_rto_and_state(self):
        t = _chola_transformer()
        self.assertEqual(t._expand_rto_codes("UP- East", None), ["UP28", "UP32"])
        t2 = _chola_transformer()
        self.assertEqual(t2._expand_state_names("UP- East", None), ["uttar-pradesh"])

    def test_underscore_and_extra_whitespace_variants_also_resolve(self):
        for variant in ("UP_East", "up   east", "UP-EAST"):
            t = _chola_transformer()
            self.assertEqual(
                t._expand_rto_codes(variant, None),
                ["UP28", "UP32"],
                f"variant={variant!r}",
            )


class EmptyZoneMapRegressionTests(TestCase):
    """Empty map (DB unavailable / unseeded) must be a true no-op — i.e.
    identical to pre-change behaviour, not a new failure mode."""

    def test_empty_map_no_zone_rto(self):
        t = _chola_transformer(zone_map={})
        self.assertIsNone(t._expand_rto_codes("Central MH", None))

    def test_empty_map_state_falls_back_to_existing_mapping_only(self):
        # "Central MH" still resolves to maharashtra via the PRE-EXISTING
        # STATE_NAME_MAPPINGS "mh" abbreviation — unrelated to zone expansion.
        t = _chola_transformer(zone_map={})
        self.assertEqual(t._expand_state_names("Central MH", None), ["maharashtra"])

    def test_empty_map_emits_no_new_warnings(self):
        t = _chola_transformer(zone_map={})
        t._expand_rto_codes("Central MH", None)
        self.assertEqual(t.warnings, [])


class NonCholaCarrierIgnoresZoneWordsTests(TestCase):
    def test_non_chola_ignores_zone_map_even_if_present(self):
        t = RawToCICSTransformer()
        t._carrier_code = "Bajaj"
        t._carrier_zone_map = dict(_ZONE_MAP)
        t._zone_warned = set()
        t.warnings = []
        self.assertIsNone(t._expand_rto_codes("Mumbai", None))


class UnmatchedZoneWarningTests(TestCase):
    def test_unmatched_zone_like_text_warns_once_deduped(self):
        t = _chola_transformer()
        t._expand_state_names("Zone Q", None)
        t._expand_rto_codes("Zone Q", None)
        zone_warnings = [w for w in t.warnings if "not found in master" in w]
        self.assertEqual(len(zone_warnings), 1)

    def test_ordinary_unresolvable_text_without_zone_word_no_extra_warning(self):
        t = _chola_transformer()
        t._expand_rto_codes("Some Random Text", None)
        zone_warnings = [w for w in t.warnings if "not found in master" in w]
        self.assertEqual(zone_warnings, [])


class UpEastUpWestCriticalRegressionTests(TestCase):
    """Closes TODOS.md:92: UP-East and plain-UP must land on DISTINCT
    condition keys instead of colliding on the bare 'uttar-pradesh' slug."""

    def test_up_east_and_plain_up_produce_different_rto(self):
        t = _chola_transformer()
        up_east_rto = t._expand_rto_codes("UP- East", None)
        t2 = _chola_transformer()
        plain_up_rto = t2._expand_rto_codes("UP", None)

        self.assertEqual(up_east_rto, ["UP28", "UP32"])
        self.assertIsNone(plain_up_rto)  # no explicit rto -> export fills from state
        self.assertNotEqual(up_east_rto, plain_up_rto)

    def test_up_east_and_plain_up_both_resolve_to_uttar_pradesh_state(self):
        # Both are genuinely uttar-pradesh's own columns (the export
        # complement, not the state expansion, is what disambiguates them).
        t = _chola_transformer()
        t2 = _chola_transformer()
        self.assertEqual(t._expand_state_names("UP- East", None), ["uttar-pradesh"])
        self.assertEqual(t2._expand_state_names("UP", None), ["uttar-pradesh"])
