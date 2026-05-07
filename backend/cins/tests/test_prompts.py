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
