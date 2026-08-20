"""
Tests for the "rest of <state>" and multi-state group tokens Chola's grids use.

Client-reported defect: 2W and commercial (GCV / PCV / Buses / MISC-D) deals
exported with blank State AND blank RTO while Private Car was clean. Pass 3 had
extracted the state on every rule; ``_expand_state_names`` simply did not know
``ROM`` / ``ROTN`` / ``NE``, returned nothing, and the export -- which derives
the RTO column from the state -- blanked both columns together.

Source evidence for each token (Chola "PO Grid-Apr 26"):
  * ``ROM`` sits directly beside ``Mumbai/GA/Pune/Central MH`` in the CV header
  * ``ROTN`` sits directly beside ``TN-Chennai`` in the same header
  * ``NE`` and ``AS/ML/TR/ AR/NL`` are the SAME six-tier block (NOP / SOD /
    ANNUAL / ACT) in two tables of one Two-Wheeler sheet

No DB: these are pure functions of the module-level mapping tables.

Run from the backend container:
    python manage.py test cins.tests.test_state_zone_tokens -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
from cins.schema.raw_extraction import (
    RawCommissionEntry,
    RawExtraction,
    RawProviderInfo,
)

# The north-east block exactly as Chola spells it out. Manipur and Mizoram are
# deliberately absent -- the carrier's own group excludes them.
_NORTH_EAST = ["arunachal-pradesh", "assam", "meghalaya", "nagaland", "tripura"]


def _raw(entries, carrier="Chola"):
    return RawExtraction(
        source_file="test.xlsx",
        source_type="excel",
        provider=RawProviderInfo(name=carrier),
        start_date="2024-07-01",
        end_date="2024-07-31",
        commission_entries=entries,
    )


class RestOfStateTokenTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_rom_resolves_to_maharashtra(self):
        self.assertEqual(self.t._expand_state_names("ROM", None), ["maharashtra"])

    def test_rotn_resolves_to_tamil_nadu(self):
        self.assertEqual(self.t._expand_state_names("ROTN", None), ["tamil-nadu"])

    def test_combined_mh_rom_cell_stays_maharashtra(self):
        """The Act-Only sheet writes the pair as one cell; it is still one state."""
        self.assertEqual(self.t._expand_state_names("MH/ROM", None), ["maharashtra"])

    def test_rom_does_not_fire_inside_a_longer_token(self):
        """``ROMG`` is a GVW/label token on other carriers' sheets, not a state.

        ``rom`` is 3 characters, so it goes through the boundary-matched arm --
        this asserts that arm is actually protecting it.
        """
        self.assertIsNone(self.t._expand_state_names("ROMG", None))

    def test_rok_resolves_to_karnataka(self):
        self.assertEqual(self.t._expand_state_names("ROK", None), ["karnataka"])

    def test_tn_rok_is_a_multi_state_cell(self):
        """The Two-Wheeler grid keys a row ``TN/ROK`` -- all of Tamil Nadu PLUS
        the Karnataka remainder. Before ``rok`` was mapped only ``tn`` matched
        and the Karnataka half was dropped from the export entirely.
        """
        self.assertEqual(
            self.t._expand_state_names("TN/ROK", None),
            ["karnataka", "tamil-nadu"],
        )

    def test_rok_does_not_fire_inside_a_longer_token(self):
        """3 characters, so it takes the boundary-matched arm -- same protection
        ``rom`` gets in the test above."""
        self.assertIsNone(self.t._expand_state_names("ROKX", None))

    def test_karnatka_misspelling_resolves(self):
        """Chola spells it without the second 'a' in places. The 2-letter ``ka``
        fallback is boundary-anchored and cannot rescue it from inside a word.
        """
        self.assertEqual(
            self.t._expand_state_names("Karnatka (Bangalore)", None), ["karnataka"]
        )


class NorthEastGroupTokenTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_ne_expands_to_the_north_east_block(self):
        self.assertEqual(self.t._expand_state_names("NE", None), _NORTH_EAST)

    def test_ne_matches_its_spelled_out_sibling_exactly(self):
        """Same grid, two tables: one says ``NE``, the other spells it out."""
        self.assertEqual(
            self.t._expand_state_names("NE", None),
            self.t._expand_state_names("AS/ML/TR/ AR/NL", None),
        )

    def test_ne_excludes_manipur_and_mizoram(self):
        """Chola's north-east group omits both; do not 'helpfully' add them."""
        got = self.t._expand_state_names("NE", None)
        self.assertNotIn("manipur", got)
        self.assertNotIn("mizoram", got)

    def test_ne_does_not_fire_on_the_adjacent_tier_label(self):
        """``NEW(100-500 NOP)`` is the cell immediately right of the state cell.

        A substring match here would stamp five north-eastern states onto every
        new-business tier row in the grid.
        """
        self.assertIsNone(self.t._expand_state_names("NEW(100-500 NOP)", None))

    def test_ne_does_not_fire_on_other_ne_prefixed_words(self):
        for text in ("NET PREMIUM", "NEW", "RENEWAL"):
            with self.subTest(text=text):
                self.assertIsNone(self.t._expand_state_names(text, None))


class CarrierProductNameCanonicalizationTests(TestCase):
    """PRODUCT_NAME_MAPPINGS must canonicalize Chola's CV table spellings.

    This is the layer that stops the raw token reaching the export at all;
    product_bucket is only the backstop for when it does. ``misc-d`` is its own
    entry because the existing ``misc d`` key cannot match a hyphen.
    """

    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_carrier_cv_spellings_canonicalize(self):
        for key in ("GCCV", "PCCV", "PCCV < 6", "MSV", "Misc-D", "MISCD"):
            with self.subTest(key=key):
                self.assertEqual(
                    self.t._normalize_product_name(key, []), "Commercial Vehicle"
                )

    def test_existing_canonical_names_unchanged(self):
        for key, want in (
            ("Private Car", "Private Car"),
            ("Two Wheeler", "Two Wheeler"),
            ("Commercial Vehicle", "Commercial Vehicle"),
        ):
            with self.subTest(key=key):
                self.assertEqual(self.t._normalize_product_name(key, []), want)


class StateFromConditionsFallbackTests(TestCase):
    """``conditions['state']`` is a free-form dict key the transformer never read.

    Codegen is instructed to emit the state as the top-level ``state_text``
    field, but ``conditions`` is a ``Dict[str, str]`` so a misrouted key
    survives on the entry and was silently discarded. Recovering it (loudly)
    beats exporting a blank cell.
    """

    def _states(self, **entry_kwargs):
        """Run the real transform() and return (states, warnings)."""
        t = RawToCICSTransformer()
        doc, warnings, _ = t.transform(
            _raw(
                [
                    RawCommissionEntry(
                        rule_text="UPTO 1000 CC",
                        commission_value="10%",
                        product_name="Private Car",
                        **entry_kwargs,
                    )
                ]
            )
        )
        rule = doc.products[0].commission_rules[0]
        return (rule.conditions.state if rule.conditions else None), warnings

    def test_state_text_is_preferred_when_both_are_present(self):
        states, warnings = self._states(state_text="KA", conditions={"state": "AP"})
        self.assertEqual(states, ["karnataka"])
        self.assertFalse([w for w in warnings if "recovered from conditions" in w])

    def test_conditions_state_is_recovered_when_state_text_is_empty(self):
        states, _ = self._states(state_text="", conditions={"state": "AP"})
        self.assertEqual(states, ["andhra-pradesh"])

    def test_recovery_warns_rather_than_healing_silently(self):
        """The recovery hides a codegen bug unless it is surfaced."""
        _, warnings = self._states(state_text="", conditions={"state": "AP"})
        self.assertTrue(
            [w for w in warnings if "recovered from conditions['state']" in w],
            f"expected a recovery warning, got {warnings}",
        )

    def test_alias_keys_are_accepted(self):
        for key in ("states", "state_group", "region"):
            with self.subTest(key=key):
                states, _ = self._states(state_text="", conditions={key: "AP"})
                self.assertEqual(states, ["andhra-pradesh"])

    def test_no_state_anywhere_stays_empty(self):
        """No defaulting: a state-less rule must stay blank for the validator."""
        states, _ = self._states(state_text="")
        self.assertFalse(states)

    def test_recovery_warning_is_emitted_once_not_once_per_rule(self):
        """Warnings are persisted into the document's validation_notes.

        This fallback fires on every row of the grid it targets, so a per-rule
        warning bakes thousands of identical strings into the stored CICS.
        """
        t = RawToCICSTransformer()
        entries = [
            RawCommissionEntry(
                rule_text=f"UPTO {n}00 CC",
                commission_value=f"{n}%",
                product_name="Private Car",
                state_text="",
                conditions={"state": "AP"},
            )
            for n in range(1, 6)
        ]
        _, warnings, _ = t.transform(_raw(entries))
        recovery = [w for w in warnings if "recovered from conditions" in w]
        self.assertEqual(len(recovery), 1, f"expected 1 warning, got {recovery}")
