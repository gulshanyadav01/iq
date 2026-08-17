"""
Smoke tests for prompt files. Pure text assertions — no LLM calls.

Their job is to detect accidental regressions where someone "tidies up" a
load-bearing instruction without realising it's load-bearing. Each test names
the failure mode it guards against, so a future editor knows what they'd
reintroduce by removing the wording.

Run from the backend container:
    python manage.py test cins.tests.test_prompts -v 2
"""

from pathlib import Path
from unittest import TestCase


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class Pass0AEmailCleaningPromptTests(TestCase):
    """
    Pass 0A's email cleaning prompt MUST instruct the LLM to preserve `From:`
    lines. When this rule is missing, Pass 0A strips the carrier's email
    address along with the rest of the forwarded-message header block — and
    the carrier identity disappears from `primary_content`, breaking Pass 1
    provider identification on broker-forwarded emails (the FG document
    failure mode).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.prompt_text = (PROMPTS_DIR / "pass0a_email_cleaning_prompt.md").read_text()

    def test_preserves_from_lines_rule_present(self):
        # Lower-cased substring match — phrasing can drift, semantics cannot.
        self.assertIn(
            "preserve `from:` lines",
            self.prompt_text.lower(),
            "Pass 0A prompt must explicitly instruct preserving `From:` lines "
            "(carrier identity often only appears in From: domains).",
        )

    def test_carrier_domain_examples_present(self):
        # The prompt anchors the rule with concrete carrier-domain examples
        # so the LLM doesn't generalise the From-preservation to broker
        # domains. If these go away, the rule becomes ambiguous.
        for example in ("futuregenerali.in", "bajajallianz.com", "icicilombard.com"):
            with self.subTest(example=example):
                self.assertIn(example, self.prompt_text)


class Pass3CholaFuelTypeSuffixTests(TestCase):
    """
    The Chola `[P]` / `[D]` fuel-type suffix instruction MUST reach the Pass-3
    extractor. Without it, all Chola Private Car rules emit `fuel_type=null`
    (bug observed on intake d81925b1, July Grid: 201/201 rules null fuel_type).

    Architecture: the codegen path keeps carrier-specific rules OUT of the
    shared base prompt and in a per-provider overlay
    (`carrier_overlays/chola_pass3.md`) injected at runtime by provider. The
    LLM path still carries it inline in `pass3_rules_prompt.md`.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.llm_prompt = (PROMPTS_DIR / "pass3_rules_prompt.md").read_text()
        cls.codegen_prompt = (PROMPTS_DIR / "pass3_rules_codegen_prompt.md").read_text()
        cls.chola_overlay = (
            PROMPTS_DIR / "carrier_overlays" / "chola_pass3.md"
        ).read_text()

    def test_llm_prompt_mentions_chola_fuel_suffix(self):
        lowered = self.llm_prompt.lower()
        self.assertIn("chola", lowered)
        self.assertIn("[p]", lowered)
        self.assertIn("[d]", lowered)
        self.assertIn("petrol", lowered)
        self.assertIn("non petrol", lowered)

    def test_chola_overlay_carries_fuel_suffix(self):
        # Codegen path: the suffix rule lives in the Chola overlay, not the base.
        lowered = self.chola_overlay.lower()
        self.assertIn("[p]", lowered)
        self.assertIn("[d]", lowered)
        self.assertIn("petrol", lowered)
        self.assertIn("non petrol", lowered)

    def test_codegen_base_prompt_has_no_carrier_specifics(self):
        # The shared codegen base must NOT bake in carrier rules — it carries
        # only the {carrier_overlay} placeholder. Prevents prompt bloat /
        # cross-carrier instruction bleed.
        base = self.codegen_prompt
        self.assertIn("{carrier_overlay}", base)
        self.assertNotIn("[P]", base)
        self.assertNotIn("[D]", base)

    def test_chola_overlay_injected_by_provider(self):
        # The runtime injection wires the overlay in for Chola and leaves the
        # prompt carrier-free for unknown providers.
        from cins.processors.core.llm_extract import (
            get_pass3_rules_codegen_prompt,
            _inject_carrier_overlay,
        )

        base = get_pass3_rules_codegen_prompt()
        chola = _inject_carrier_overlay(base, "Cholamandalam MS General Insurance")
        self.assertIn("[P]", chola)
        self.assertNotIn("{carrier_overlay}", chola)

        unknown = _inject_carrier_overlay(base, "Some Unknown Carrier")
        self.assertNotIn("[P]", unknown)
        self.assertNotIn("{carrier_overlay}", unknown)

    def test_rule_scoped_to_chola_only(self):
        # The rule must explicitly restrict to Chola so other carriers'
        # Private Car grids (which do not use [P]/[D]) aren't mis-parsed.
        self.assertIn("only", self.llm_prompt.lower())
        self.assertIn("only", self.chola_overlay.lower())


class Pass3CholaNcbTierTests(TestCase):
    """
    The Chola "NCB > 25%" parallel-table instruction MUST reach the Pass-3
    codegen extractor via the overlay, and MUST forbid the generated code from
    setting `conditions["ncb"]` itself — the transformer derives the tier from
    the marker, and a codegen that guesses `"ncb": "Yes"` on the base table
    would invert the whole product (defect 3). The shared base prompt must stay
    carrier-free.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.codegen_prompt = (PROMPTS_DIR / "pass3_rules_codegen_prompt.md").read_text()
        cls.chola_overlay = (
            PROMPTS_DIR / "carrier_overlays" / "chola_pass3.md"
        ).read_text()

    def test_overlay_carries_ncb_tier_section(self):
        lowered = self.chola_overlay.lower()
        self.assertIn("ncb > 25%", lowered)
        self.assertIn("ncb gt 25%", lowered)
        # Both table sets must be emitted (rates differ — not duplicates).
        self.assertIn("both table sets", lowered)

    def test_overlay_forbids_setting_ncb_in_codegen(self):
        lowered = self.chola_overlay.lower()
        self.assertIn("do not set", lowered)
        self.assertIn('conditions["ncb"]', self.chola_overlay)

    def test_codegen_base_prompt_has_no_ncb_tier(self):
        # Carrier-specific NCB tier rule must NOT bleed into the shared base.
        self.assertNotIn("NCB GT 25%", self.codegen_prompt)

    def test_ncb_instruction_is_internally_consistent(self):
        """MANDATORY REGRESSION GUARD.

        The general Pass 3 prompt tells the LLM to copy the NCB band label into
        `conditions["ncb"]`. The Chola overlay tells it NOT to set that key at
        all. Both are correct for their own scope, but if they ever read as
        UNCONDITIONAL OPPOSITES the model has no way to resolve them and the
        behaviour becomes a coin flip on a path that works today.

        One of them must state precedence. Guard that it still does.
        """
        rules_prompt = (PROMPTS_DIR / "pass3_rules_prompt.md").read_text().lower()
        overlay = self.chola_overlay.lower()

        # The general prompt asks for the verbatim band label...
        self.assertIn("verbatim", rules_prompt)
        self.assertIn("ncb gt or equal to 25%", rules_prompt)
        # ...and explicitly defers to a carrier overlay that says otherwise.
        self.assertIn("overlay", rules_prompt)
        self.assertTrue(
            "overlays override" in rules_prompt or "follow the overlay" in rules_prompt,
            "pass3_rules_prompt.md must state that a carrier overlay overrides "
            "the general 'copy the ncb label' instruction — otherwise it and "
            "chola_pass3.md are unconditional opposites",
        )
        # The overlay states which instruction wins, and why.
        self.assertIn("overrides the general pass 3 instruction", overlay)

    def test_codegen_whitelist_forbids_invented_ncb_keys(self):
        # The band is parsed by the transformer from ONE verbatim label. A
        # codegen that invents ncb_start/ncb_end would bypass the parser and
        # emit keys RuleConditions silently drops (extra='ignore'), so the
        # whitelist must name and forbid them explicitly.
        self.assertIn("Do NOT invent keys outside this list", self.codegen_prompt)
        self.assertIn('no "ncb_start"/"ncb_end"', self.codegen_prompt)
        self.assertIn("VERBATIM band label", self.codegen_prompt)
        # The approved key list still carries plain "ncb" and nothing else.
        approved = self.codegen_prompt.split("Do NOT invent keys outside")[0]
        self.assertIn('"ncb"', approved)
        self.assertNotIn("ncb_start", approved)
        self.assertNotIn("ncb_end", approved)


class RawConditionKeysSharedBlockTests(TestCase):
    """The raw `conditions` key vocabulary is defined ONCE.

    It used to be duplicated verbatim in two docstrings (raw_extraction.py and
    extraction_passes.py) kept in sync by hand — the same manual-multi-site
    pattern that lets changes half-apply silently elsewhere in this app. Pin
    them equal so drift is impossible rather than merely discouraged.
    """

    def test_both_models_render_the_same_description(self):
        from cins.schema.extraction_passes import RuleEntry
        from cins.schema.raw_extraction import RawCommissionEntry

        a = RawCommissionEntry.model_fields["conditions"].description
        b = RuleEntry.model_fields["conditions"].description
        self.assertEqual(a, b)

    def test_shared_block_documents_the_verbatim_ncb_label(self):
        from cins.prompts._shared import RAW_CONDITION_KEYS_BLOCK

        self.assertIn("verbatim", RAW_CONDITION_KEYS_BLOCK)
        self.assertIn("NCB GT OR Equal to 25%", RAW_CONDITION_KEYS_BLOCK)
        # Legacy Yes/No must stay accepted — old grids still use it.
        self.assertIn("Yes", RAW_CONDITION_KEYS_BLOCK)


class Pass3ColumnIndexLiteralTests(TestCase):
    """Defect 1: a generated parser that filters blank spacer headers before
    indexing reads a neighbouring state's column. Both the shared codegen base
    and the Chola overlay MUST instruct that column indices are LITERAL and the
    state segment is taken verbatim before the last '>'."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.codegen_prompt = (PROMPTS_DIR / "pass3_rules_codegen_prompt.md").read_text()
        cls.chola_overlay = (
            PROMPTS_DIR / "carrier_overlays" / "chola_pass3.md"
        ).read_text()

    def test_base_prompt_says_indices_are_literal(self):
        lowered = self.codegen_prompt.lower()
        self.assertIn("literal", lowered)
        self.assertIn("enumerate", lowered)
        self.assertIn("never filter", lowered.replace("`", ""))

    def test_base_prompt_says_state_segment_verbatim(self):
        lowered = self.codegen_prompt.lower()
        self.assertIn("verbatim", lowered)
        self.assertIn("last", lowered)  # "before the last >"
        self.assertIn("up- east", lowered)

    def test_overlay_says_indices_are_literal_and_state_verbatim(self):
        lowered = self.chola_overlay.lower()
        self.assertIn("literal", lowered)
        self.assertIn("enumerate", lowered)
        self.assertIn("verbatim", lowered)
        self.assertIn("up- east", lowered)

    def test_overlay_emits_col_map_self_check(self):
        # The generated parser must assert one col_map entry per ">"-header cell.
        self.assertIn("assert len(col_map)", self.chola_overlay)
