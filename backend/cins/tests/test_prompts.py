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
