"""
Tests for the email indexer's quoted-trail stripping logic
(`ContentIndexer._strip_quoted_trail`). The deterministic stripper is
load-bearing for Pass 1 metadata extraction — over-aggressive stripping
removes the carrier-bearing forwarded thread on broker-relayed emails,
under-aggressive stripping leaks duplicate noise into the LLM prompt.

Run from the backend container:
    python manage.py test cins.tests.test_indexer_email -v 2
"""

import tempfile
from unittest import TestCase

from cins.processors.content.indexer import ContentIndexer


class StripQuotedTrailGuardTests(TestCase):
    def setUp(self):
        # Indexer is constructed with cleaning disabled; we're exercising the
        # deterministic stripper, not Pass 0A's LLM cleaner.
        self.indexer = ContentIndexer(
            indexed_dir=tempfile.mkdtemp(),
            llm_provider="gemini",
            enable_cleaning=False,
        )

    def test_short_broker_preamble_preserves_forwarded_thread(self):
        # Mirrors the FG document failure mode: short broker forward preamble
        # ("Hi X, FYI") followed by the carrier's actual message. The
        # carrier-bearing thread MUST survive — it's where the provider
        # signal and commission grid live.
        body = (
            "Hi Nadeem,\n"
            "This is FYI.\n"
            "---------- Forwarded message ---------\n"
            "From: Derryl George <derryl@futuregenerali.in>\n"
            "Date: Mon, Jun 30, 2025\n"
            "Subject: RE: FG March Grid\n"
            "To: harsimran@pbpartners.com\n"
            "\n"
            "Pls note, starting 1st June 2025, we have made changes "
            "to the PVTP grid (Private Car Third Party Only). Petrol 30%, Diesel 20%."
        )
        out = self.indexer._strip_quoted_trail(body)
        # Whole body returned untouched — the inner thread is preserved.
        self.assertEqual(out, body)
        self.assertIn("futuregenerali.in", out)
        self.assertIn("PVTP grid", out)

    def test_long_original_message_strips_reply_trail(self):
        # The guard must not over-trigger: when the user wrote a substantial
        # original message, the trailing quoted reply IS noise and should be
        # stripped (existing behaviour).
        original = (
            "Hi Bob,\n\n"
            "Quick update on this quarter's commission structure. We have "
            "reviewed the latest grid from the carrier and noted the following "
            "changes that need your attention. Please review carefully and let "
            "us know your thoughts.\n\n"
            "The key changes are:\n"
            "1. Private Car TP rate increased from 25% to 30% for petrol "
            "vehicles.\n"
            "2. Diesel rates remain unchanged at 20%.\n"
            "3. New states added to the doable list.\n\n"
            "Please confirm receipt and acknowledge the new rates by EOD "
            "Friday.\n\n"
            "Thanks,\n"
            "Alice"
        )
        # Sanity guard: the test case must actually be long enough to fall
        # above the threshold, otherwise we'd be testing the wrong branch.
        self.assertGreaterEqual(len(original), 400)

        body = (
            original
            + "\n\nOn Mon, Jan 1, 2025, Bob wrote:\n"
            + "> earlier reply content here\n"
            + "> with more lines\n"
        )
        out = self.indexer._strip_quoted_trail(body)
        self.assertIn("Quick update on this quarter", out)
        self.assertNotIn("Bob wrote", out)
        self.assertNotIn("earlier reply content", out)

    def test_no_quoted_trail_returns_body_unchanged(self):
        body = (
            "Hi team,\n\nPlease confirm the rates for July as discussed. "
            "Private Car: 12%, TP: 8%. Thanks."
        )
        self.assertEqual(self.indexer._strip_quoted_trail(body), body)

    def test_empty_body_returns_empty(self):
        self.assertEqual(self.indexer._strip_quoted_trail(""), "")
