"""
Tests for the commission-period fallback chain in `RawToCICSTransformer`.

Root cause of the "shows July 2026 but it's July 2025" bug (doc bf78a7d7): a
plain grid email carried no written period, so Pass 1 returned no date, and
`_transform_period` stamped `datetime.utcnow()` — the processing date — as
`effective_from`. That fabricated a confident-looking, always-wrong period that
now surfaces in the runs-list Period column.

The fix threads the document's own date (email `Date:` header) into the
transformer as a deterministic fallback. Order: extracted start_date → document
date → today (last resort). These tests pin every rung.

Run from the backend container:
    python manage.py test cins.tests.test_period_document_date_fallback -v 2
"""

from datetime import datetime
from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import (
    RawToCICSTransformer,
    parse_document_date,
)
from cins.schema.raw_extraction import RawExtraction, RawProviderInfo

EMAIL = (
    "Subject: SCV Up to 2.5 Ton_HR1 RTO\n"
    "From: Praveen Kumar <praveen.kumar5@icicilombard.com>\n"
    "To: Nadeem <nadeem2@pbpartners.com>\n"
    "Date: Wed, 02 Jul 2025 06:45:32 +0000\n"
    "\n"
    "HR1-SCV upto 2.5T\n"
)


def _raw(start_date=""):
    return RawExtraction(
        provider=RawProviderInfo(name="ICICI"),
        start_date=start_date,
        commission_entries=[],
    )


class ParseDocumentDateTests(TestCase):
    def test_parses_rfc2822_email_header(self):
        self.assertEqual(parse_document_date(EMAIL), "2025-07-02")

    def test_none_when_no_date_header(self):
        self.assertIsNone(parse_document_date("Subject: x\n\nbody only"))

    def test_none_on_empty_or_unparseable(self):
        self.assertIsNone(parse_document_date(""))
        self.assertIsNone(parse_document_date("Date: not-a-real-date\n"))


class TransformPeriodFallbackTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_document_date_used_when_no_start_date(self):
        # The reported bug: empty start_date + email date → email date, NOT today.
        doc, warnings, _ = self.t.transform(_raw(""), document_date="2025-07-02")
        self.assertEqual(doc.commission_period.effective_from, "2025-07-02")
        self.assertTrue(any("document date" in w for w in warnings))

    def test_extracted_start_date_wins_over_document_date(self):
        doc, _, _ = self.t.transform(_raw("2025-05-01"), document_date="2025-07-02")
        self.assertEqual(doc.commission_period.effective_from, "2025-05-01")

    def test_falls_back_to_today_only_without_document_date(self):
        doc, warnings, _ = self.t.transform(_raw(""), document_date=None)
        self.assertEqual(
            doc.commission_period.effective_from,
            datetime.utcnow().strftime("%Y-%m-%d"),
        )
        self.assertTrue(any("falling back to" in w for w in warnings))

    def test_does_not_stamp_today_when_document_date_present(self):
        today = datetime.utcnow().strftime("%Y-%m-%d")
        doc, _, _ = self.t.transform(_raw(""), document_date="2025-07-02")
        self.assertNotEqual(doc.commission_period.effective_from, today)
