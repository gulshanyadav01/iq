"""
Tests for the Chola "NCB > 25%" parallel-table tier (defect 3).

Chola Private Car ships each CC-band grid twice: a base set and a
'… - NCB GT 25%' set at higher rates. Both previously landed ncb=None and
collided on an identical condition key (250/467 exported PC rows ambiguous).
The NCB>25% rows are now marked ncb=True from the marker in source_section /
rule_text; _pair_ncb_tiers flips the paired base twin to ncb=False. cv()
renders True->"Yes", False->"No", so the export needs no change.

Run from the backend container:
    python manage.py test cins.tests.test_ncb_tier -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
from cins.schema.cics_model import (
    CommissionOutcome,
    CommissionRule,
    RuleConditions,
)
from cins.schema.raw_extraction import (
    RawCommissionEntry,
    RawExtraction,
    RawProviderInfo,
)


def _raw(entries):
    return RawExtraction(
        source_file="test.xlsx",
        source_type="excel",
        provider=RawProviderInfo(name="Chola"),
        start_date="2024-04-01",
        end_date="2024-04-30",
        commission_entries=entries,
    )


def _rule(rule_id, ncb=None, rate=10.0, **cond_kwargs):
    return CommissionRule(
        rule_id=rule_id,
        priority=10,
        conditions=RuleConditions(ncb=ncb, **cond_kwargs),
        outcome=CommissionOutcome(commission_type="percentage", percentage_value=rate),
    )


class NcbMarkerTests(TestCase):
    """The NCB>25% marker in source_section or the row label sets ncb=True."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_marker_in_source_section(self):
        entry = RawCommissionEntry(
            rule_text="Private Car UPTO 1000 CC AP PACK",
            commission_value="20%",
            product_name="Private Car",
            source_section="Private Car Package - NCB GT 25%",
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertIs(cond.ncb, True)
        # "GT 25%" is STRICT: the band starts at 26, not 25.
        self.assertEqual((cond.ncb_start_range, cond.ncb_end_range), (26, 100))

    def test_marker_in_row_label(self):
        entry = RawCommissionEntry(
            rule_text="Private Car UPTO 1000 CC[ NCB GT 25%] AP PACK",
            commission_value="20%",
            product_name="Private Car",
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertIs(cond.ncb, True)
        self.assertEqual((cond.ncb_start_range, cond.ncb_end_range), (26, 100))

    def test_may_label_variant_gt_or_equal_to(self):
        # May'26 grid ships "[ NCB GT OR Equal to 25%]" — the "OR Equal to"
        # infix broke the old regex, leaving all 638 May PC rows ncb=None.
        # All three are inclusive forms, so the band starts AT 25.
        for label in (
            "Private Car UPTO 1000 CC[ NCB GT OR Equal to 25%] AP PACK",
            "Private Car UPTO 1000 CC[ NCB >= 25%] AP PACK",
            "Private Car UPTO 1000 CC[ NCB greater than or equal to 25%] AP PACK",
        ):
            entry = RawCommissionEntry(
                rule_text=label,
                commission_value="20%",
                product_name="Private Car",
            )
            cond = self.t._transform_conditions(entry, None, _raw([entry]))
            self.assertIs(cond.ncb, True, label)
            self.assertEqual(
                (cond.ncb_start_range, cond.ncb_end_range), (25, 100), label
            )

    def test_apr_label_variant_still_matches(self):
        # Regression guard: the Apr'26 "[ NCB GT 25%]" title form must keep
        # matching after the parser was generalised beyond the 25% threshold.
        entry = RawCommissionEntry(
            rule_text="Private Car UPTO 1000 CC AP PACK",
            commission_value="20%",
            product_name="Private Car",
            source_section="Private Car Package - NCB GT 25%",
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertIs(cond.ncb, True)
        self.assertEqual((cond.ncb_start_range, cond.ncb_end_range), (26, 100))

    def test_non_25_threshold_is_recovered(self):
        # The whole point of the band: a grid split anywhere other than 25%
        # used to leave BOTH tiers with no NCB dimension, so they collided on an
        # identical condition key and collapsed onto one rate.
        entry = RawCommissionEntry(
            rule_text="Private Car UPTO 1000 CC[ NCB GT OR Equal to 45%] AP PACK",
            commission_value="20%",
            product_name="Private Car",
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertIs(cond.ncb, True)
        self.assertEqual((cond.ncb_start_range, cond.ncb_end_range), (45, 100))

    def test_base_table_stays_none(self):
        entry = RawCommissionEntry(
            rule_text="Private Car UPTO 1000 CC AP PACK",
            commission_value="20%",
            product_name="Private Car",
            source_section="Private Car Package",
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertIsNone(cond.ncb)
        # Sentinel is null/null, NOT the open (0, 100) used by gvw/cc/seating —
        # an open band would make ncb a constrained dimension on every rule of
        # every document and shift the overlap checker's verdict globally.
        self.assertIsNone(cond.ncb_start_range)
        self.assertIsNone(cond.ncb_end_range)

    def test_unrelated_rule_text_produces_no_band(self):
        # The parser runs against the probe of EVERY rule of EVERY carrier, so
        # a stray "NCB" mention must not stamp a phantom band on a rule whose
        # source document has no NCB dimension.
        for text in (
            "Private Car UPTO 1000 CC AP PACK",
            "NCB Yes 2024 renewal",
            "applicable to NCB holders",
            "25% loading, no NCB",
            "NCB protection add-on",
        ):
            entry = RawCommissionEntry(
                rule_text=text,
                commission_value="20%",
                product_name="Private Car",
            )
            cond = self.t._transform_conditions(entry, None, _raw([entry]))
            self.assertIsNone(cond.ncb_start_range, text)
            self.assertIsNone(cond.ncb_end_range, text)

    def test_legacy_raw_yes_reconstructs_historic_split(self):
        # A raw "ncb": "Yes" with no numeric label predates the band. Rebuild
        # the historic 25% split so pre-band behaviour is preserved.
        entry = RawCommissionEntry(
            rule_text="Private Car UPTO 1000 CC AP PACK",
            commission_value="20%",
            product_name="Private Car",
            conditions={"ncb": "Yes"},
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertIs(cond.ncb, True)
        self.assertEqual((cond.ncb_start_range, cond.ncb_end_range), (26, 100))

    def test_band_still_derived_when_codegen_emits_no_ncb_key(self):
        """MANDATORY REGRESSION GUARD for the Pass 3 prompt change.

        The Chola overlay forbids codegen from setting conditions["ncb"], so
        today's real Chola output has NO ncb key at all — the marker reaches the
        transformer only through source_section / rule_text. The prompt edits in
        this change must not silently become load-bearing: if the band ever
        depended on the LLM emitting the key, every existing Chola document
        would regress the moment the prompt drifted.
        """
        entry = RawCommissionEntry(
            rule_text="Private Car UPTO 1000 CC[ NCB GT OR Equal to 25%] AP PACK",
            commission_value="20%",
            product_name="Private Car",
            source_section="Private Car Package - NCB GT OR Equal to 25%",
            conditions={},  # exactly what the Chola overlay instructs
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertEqual((cond.ncb_start_range, cond.ncb_end_range), (25, 100))
        self.assertIs(cond.ncb, True)

    def test_raw_band_label_beats_probe(self):
        entry = RawCommissionEntry(
            rule_text="Private Car UPTO 1000 CC AP PACK",
            commission_value="20%",
            product_name="Private Car",
            conditions={"ncb": "NCB 25%-45%"},
        )
        cond = self.t._transform_conditions(entry, None, _raw([entry]))
        self.assertEqual((cond.ncb_start_range, cond.ncb_end_range), (25, 45))
        self.assertIs(cond.ncb, True)

    def test_band_notice_is_aggregated_not_per_rule(self):
        # Notices reach the DB and the detail page unfiltered; a per-rule append
        # buries every other notice on a large grid.
        entries = [
            RawCommissionEntry(
                rule_text=f"Private Car UPTO {cc} CC[ NCB GT OR Equal to 25%]",
                commission_value="20%",
                product_name="Private Car",
            )
            for cc in (1000, 1500, 2000)
        ]
        raw = _raw(entries)
        for entry in entries:
            self.t._transform_conditions(entry, None, raw)
        self.assertEqual(len(self.t._ncb_bands_parsed), 3)
        # Nothing emitted per rule — the single line is appended in transform().
        self.assertEqual(
            [n for n in self.t.notices if "NCB band parsed" in n],
            [],
        )


class PairNcbTiersTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_base_twin_flipped_to_false(self):
        rules = [
            _rule("NCB", ncb=True, rate=25.0, cc_start_range=0, cc_end_range=1000),
            _rule("BASE", ncb=None, rate=15.0, cc_start_range=0, cc_end_range=1000),
        ]
        self.t._pair_ncb_tiers(rules)
        by_id = {r.rule_id: r for r in rules}
        self.assertIs(by_id["NCB"].conditions.ncb, True)
        self.assertIs(by_id["BASE"].conditions.ncb, False)
        # A legacy bool-only marked rule gets its implied band backfilled, and
        # the twin gets the complement.
        ncb_cond = by_id["NCB"].conditions
        base_cond = by_id["BASE"].conditions
        self.assertEqual((ncb_cond.ncb_start_range, ncb_cond.ncb_end_range), (26, 100))
        self.assertEqual((base_cond.ncb_start_range, base_cond.ncb_end_range), (0, 25))

    def test_banded_marked_rule_gives_twin_the_complement(self):
        rules = [
            _rule(
                "NCB",
                ncb=True,
                rate=25.0,
                ncb_start_range=25,
                ncb_end_range=100,
                cc_start_range=0,
                cc_end_range=1000,
            ),
            _rule("BASE", ncb=None, rate=15.0, cc_start_range=0, cc_end_range=1000),
        ]
        self.t._pair_ncb_tiers(rules)
        base = {r.rule_id: r for r in rules}["BASE"].conditions
        self.assertEqual((base.ncb_start_range, base.ncb_end_range), (0, 24))
        self.assertIs(base.ncb, False)

    def test_pairing_notice_is_aggregated(self):
        rules = [
            _rule(
                f"NCB{i}",
                ncb=True,
                rate=25.0,
                ncb_start_range=25,
                ncb_end_range=100,
                cc_start_range=i * 500,
                cc_end_range=(i + 1) * 500,
            )
            for i in range(3)
        ] + [
            _rule(
                f"BASE{i}",
                ncb=None,
                rate=15.0,
                cc_start_range=i * 500,
                cc_end_range=(i + 1) * 500,
            )
            for i in range(3)
        ]
        self.t._pair_ncb_tiers(rules)
        tier_notices = [n for n in self.t.notices if "NCB tier" in n]
        self.assertEqual(len(tier_notices), 1, tier_notices)
        self.assertIn("3 base twin(s)", tier_notices[0])

    def test_unpaired_rules_keep_none(self):
        # No marked member in the group -> ncb stays None (no NCB dimension).
        rules = [
            _rule("A", ncb=None, cc_start_range=0, cc_end_range=1000),
            _rule("B", ncb=None, cc_start_range=1000, cc_end_range=1500),
        ]
        self.t._pair_ncb_tiers(rules)
        self.assertTrue(all(r.conditions.ncb is None for r in rules))
        self.assertTrue(
            all(
                r.conditions.ncb_start_range is None
                and r.conditions.ncb_end_range is None
                for r in rules
            )
        )

    def test_sod_rule_not_flipped_by_marked_package(self):
        # Collision scope, not product scope: a marked Package rule must NOT
        # stamp ncb=False on a SOD rule with a different condition key.
        rules = [
            _rule(
                "PKG_NCB",
                ncb=True,
                rate=25.0,
                policy_type=["comprehensive"],
                cc_start_range=0,
                cc_end_range=1000,
            ),
            _rule(
                "PKG_BASE",
                ncb=None,
                rate=15.0,
                policy_type=["comprehensive"],
                cc_start_range=0,
                cc_end_range=1000,
            ),
            _rule(
                "SOD",
                ncb=None,
                rate=12.0,
                policy_type=["saod"],
                cc_start_range=0,
                cc_end_range=1000,
            ),
        ]
        self.t._pair_ncb_tiers(rules)
        by_id = {r.rule_id: r for r in rules}
        self.assertIs(by_id["PKG_BASE"].conditions.ncb, False)
        self.assertIsNone(by_id["SOD"].conditions.ncb)
        # The pair key now pops three NCB fields, not one — guard that the SOD
        # rule stays out of the Package group.
        self.assertIsNone(by_id["SOD"].conditions.ncb_start_range)
        self.assertIsNone(by_id["SOD"].conditions.ncb_end_range)

    def test_tiers_dedupe_independently(self):
        # ncb is a dedupe scalar field, so the True and False ladders normalize
        # their CC boundaries separately after pairing.
        rules = [
            _rule("N1", ncb=True, cc_start_range=0, cc_end_range=1000),
            _rule("N2", ncb=True, cc_start_range=1000, cc_end_range=1500),
            _rule("B1", ncb=None, cc_start_range=0, cc_end_range=1000),
            _rule("B2", ncb=None, cc_start_range=1000, cc_end_range=1500),
        ]
        self.t._pair_ncb_tiers(rules)
        self.t._dedupe_range_overlaps(rules)
        by_id = {r.rule_id: r for r in rules}
        # Each ladder bumps its own upper rung; they do not fuse.
        self.assertEqual(by_id["N2"].conditions.cc_start_range, 1001)
        self.assertEqual(by_id["B2"].conditions.cc_start_range, 1001)
        self.assertIs(by_id["B1"].conditions.ncb, False)


class NcbRangeParseTests(TestCase):
    """_parse_ncb_range: label -> inclusive (start, end) band, clamped 0-100."""

    def _assert(self, text, expected):
        self.assertEqual(RawToCICSTransformer._parse_ncb_range(text), expected, text)

    def test_prefix_inclusive(self):
        for text in (
            "NCB GT OR Equal to 25%",
            "UPTO 1000 CC[ NCB GT OR Equal to 25%]",
            "NCB >= 25",
            "NCB ≥ 25%",
            "NCB GTE 25%",
            "NCB greater than or equal to 25%",
            "NCB not less than 25%",
            "NCB at least 25%",
        ):
            self._assert(text, (25, 100))

    def test_prefix_strict_greater_than(self):
        # The one-off that _parse_range_field cannot express, and the reason it
        # is not reused: GT 25 excludes 25, GTE 25 includes it, and the
        # difference decides the complement handed to the base twin.
        for text in ("NCB GT 25%", "NCB > 25%", "NCB above 25%", "NCB more than 25%"):
            self._assert(text, (26, 100))

    def test_prefix_upper_bounded(self):
        for text in ("NCB UPTO 20%", "NCB <= 20%", "NCB up to 20%", "NCB max 20%"):
            self._assert(text, (0, 20))

    def test_prefix_strict_less_than(self):
        for text in ("NCB LESS THAN 20%", "NCB < 20", "NCB below 20%", "NCB LT 20%"):
            self._assert(text, (0, 19))

    def test_bounded_band(self):
        self._assert("NCB 25%-45%", (25, 45))
        self._assert("NCB 25 to 45", (25, 45))
        self._assert("NCB 45%-25%", (25, 45))  # reversed input is normalised

    def test_postfix_above(self):
        # Prior learning cins-seating-above6-parse: _parse_range_field needs the
        # qualifier BEFORE the number, so "6 AND ABOVE" silently falls to a
        # sentinel. NCB's version of that bug is worse — the bare-number arm
        # would return the point band (25, 25), which is well-formed, in range,
        # passes every validator, and matches almost no policy.
        for text in (
            "NCB 25% AND ABOVE",
            "NCB 25% & above",
            "NCB 25% ABOVE",
            "NCB 25% AND OVER",
            "NCB 25% OR MORE",
            "NCB 25% PLUS",
            "NCB 25%+",
            "NCB 25% and upwards",
        ):
            self._assert(text, (25, 100))

    def test_postfix_below(self):
        for text in ("NCB 20% AND BELOW", "NCB 20% OR LESS", "NCB 20% AND UNDER"):
            self._assert(text, (0, 20))

    def test_bare_percentage(self):
        self._assert("NCB 20%", (20, 20))
        self._assert("[ NCB 20%]", (20, 20))

    def test_bare_arm_declines_on_unknown_trailing_qualifier(self):
        # Guard 2: an unrecognised phrasing must be a VISIBLE MISS, never a
        # silently-wrong point band.
        self._assert("NCB 25% SOMETHINGUNKNOWN", None)
        self._assert("NCB 25% thereafter", None)

    def test_bare_arm_requires_percent_sign(self):
        # Guard 1: without a mandatory %, ordinary text stamps phantom bands.
        self._assert("NCB Yes 2024", None)
        self._assert("NCB 25", None)

    def test_clamped_to_0_100(self):
        self._assert("NCB GT 150%", (100, 100))
        self._assert("NCB LESS THAN 0%", (0, 0))

    def test_no_match(self):
        for text in (
            "",
            None,
            "   ",
            "Private Car UPTO 1000 CC",
            "NCB Yes",
            "applicable to NCB holders",
            "25% loading, no NCB",
        ):
            self._assert(text, None)

    def test_cc_band_in_same_label_does_not_leak(self):
        # The anchor is the literal "ncb" token, so a CC range sharing the label
        # must not be read as an NCB band.
        self._assert("UPTO 1000 CC", None)
        self._assert("1000-1500 CC[ NCB GT OR Equal to 25%]", (25, 100))


class NcbBoolFromRangeTests(TestCase):
    def test_truth_table(self):
        f = RawToCICSTransformer._ncb_bool_from_range
        self.assertIs(f(25, 100), True)
        self.assertIs(f(26, 100), True)
        self.assertIs(f(45, 100), True)
        self.assertIs(f(0, 24), False)
        self.assertIs(f(0, 20), False)
        self.assertIsNone(f(0, 100))
        self.assertIsNone(f(None, None))

    def test_fallback_used_only_when_unconstrained(self):
        f = RawToCICSTransformer._ncb_bool_from_range
        self.assertIs(f(None, None, fallback=True), True)
        self.assertIs(f(0, 100, fallback=False), False)
        # A real band always wins over the fallback.
        self.assertIs(f(25, 100, fallback=False), True)
        self.assertIs(f(0, 24, fallback=True), False)


class NcbThreeTierTests(TestCase):
    """A ladder with more than two NCB tiers.

    Both upper tiers derive ncb=True, so the boolean alone cannot separate
    them — this is what the ncb_start_range/ncb_end_range entries in
    _DEDUPE_SCALAR_FIELDS exist for.
    """

    def setUp(self):
        self.t = RawToCICSTransformer()

    def _ladder(self):
        return [
            _rule(
                "MID",
                ncb=True,
                rate=22.0,
                ncb_start_range=25,
                ncb_end_range=44,
                cc_start_range=0,
                cc_end_range=1000,
            ),
            _rule(
                "TOP",
                ncb=True,
                rate=28.0,
                ncb_start_range=45,
                ncb_end_range=100,
                cc_start_range=0,
                cc_end_range=1000,
            ),
            _rule(
                "BASE",
                ncb=None,
                rate=15.0,
                cc_start_range=0,
                cc_end_range=1000,
            ),
        ]

    def test_base_complements_against_lowest_marked_tier(self):
        rules = self._ladder()
        self.t._pair_ncb_tiers(rules)
        base = {r.rule_id: r for r in rules}["BASE"]
        # min(marked starts) - 1 = 24. Using max would wrongly give (0, 44) and
        # swallow the entire middle tier.
        self.assertEqual(
            (base.conditions.ncb_start_range, base.conditions.ncb_end_range), (0, 24)
        )
        self.assertIs(base.conditions.ncb, False)

    def test_tiers_do_not_fuse_in_the_cc_boundary_walk(self):
        # CRITICAL. _dedupe_group_key is built from EXPLICIT field tuples, not a
        # model dump, so the band must be in _DEDUPE_SCALAR_FIELDS. Without it
        # MID and TOP share a group key (both ncb=True) and their CC ladders get
        # bumped against each other.
        rules = self._ladder() + [
            _rule(
                "MID_HI",
                ncb=True,
                rate=20.0,
                ncb_start_range=25,
                ncb_end_range=44,
                cc_start_range=1000,
                cc_end_range=1500,
            ),
            _rule(
                "TOP_HI",
                ncb=True,
                rate=26.0,
                ncb_start_range=45,
                ncb_end_range=100,
                cc_start_range=1000,
                cc_end_range=1500,
            ),
        ]
        self.t._pair_ncb_tiers(rules)
        self.t._dedupe_range_overlaps(rules)
        by_id = {r.rule_id: r for r in rules}
        # Each NCB band bumps its OWN upper rung; the two bands stay separate.
        self.assertEqual(by_id["MID_HI"].conditions.cc_start_range, 1001)
        self.assertEqual(by_id["TOP_HI"].conditions.cc_start_range, 1001)
        # And the bands themselves are untouched: NCB is deliberately NOT in
        # _DEDUPE_RANGES, so grid-verbatim bands are never rewritten.
        self.assertEqual(by_id["MID"].conditions.ncb_end_range, 44)
        self.assertEqual(by_id["TOP"].conditions.ncb_start_range, 45)

    def test_dedupe_group_key_separates_the_two_marked_tiers(self):
        mid, top, _base = self._ladder()
        key = self.t._dedupe_group_key(mid.conditions, "cc_start_range", "cc_end_range")
        other = self.t._dedupe_group_key(
            top.conditions, "cc_start_range", "cc_end_range"
        )
        self.assertNotEqual(key, other)


class HasConditionsNcbTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_ncb_band_alone_counts_as_a_condition(self):
        # Without both keys in _has_conditions, a rule whose ONLY dimension is
        # the NCB band would serialise conditions=None and match everything.
        cond = RuleConditions(ncb_start_range=25, ncb_end_range=100)
        self.assertTrue(self.t._has_conditions(cond))

    def test_empty_conditions_still_false(self):
        self.assertFalse(self.t._has_conditions(RuleConditions()))


class ParseBoolFieldTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_warns_on_unknown(self):
        self.assertIsNone(self.t._parse_bool_field("maybe"))
        self.assertTrue(any("Unrecognised boolean" in w for w in self.t.warnings))

    def test_silent_on_blank(self):
        self.assertIsNone(self.t._parse_bool_field(""))
        self.assertIsNone(self.t._parse_bool_field("   "))
        self.assertIsNone(self.t._parse_bool_field(None))
        self.assertEqual(self.t.warnings, [])
