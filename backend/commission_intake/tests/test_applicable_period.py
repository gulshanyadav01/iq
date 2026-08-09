"""
Tests for the "Period" column on the Commission Intake runs list.

The applicable period is not a model column — it lives inside the extracted
`CICSResult.cics_doc["commission_period"]` JSON. The list endpoint pulls it via
a DB-side JSON annotation (no blob load, single JOIN); the detail endpoint reads
it from the already-loaded relation. Both funnel through
`format_applicable_period`, which normalizes structured dates to "Jul 2025" and
falls back to the free-text label. These tests lock:

- the format contract (structured-first, month range, label fallback),
- that malformed JSON degrades instead of 500ing,
- list/detail parity, and
- the single-query guarantee that guards the `.only()` column list from drift.

Run from the backend container:
    python manage.py test commission_intake.tests.test_applicable_period -v 2
"""

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from commission_intake.models import CICSResult, CommissionDocument
from commission_intake.serializers.commission_document import (
    format_applicable_period,
)


class FormatApplicablePeriodTests(DjangoTestCase):
    """Unit tests for the shared formatter — every branch of the failure table."""

    def test_structured_single_month(self):
        # (a) structured dates present, same month → normalized "%b %Y".
        self.assertEqual(
            format_applicable_period("July'25", "2025-07-01", "2025-07-31"),
            "Jul 2025",
        )

    def test_structured_month_range(self):
        # (a) to-month differs → range form.
        self.assertEqual(
            format_applicable_period(None, "2025-07-01", "2025-09-30"),
            "Jul – Sep 2025",
        )

    def test_structured_wins_over_label(self):
        # Structured date is preferred even when a label is also present.
        self.assertEqual(
            format_applicable_period("Q3 2025", "2025-07-01", None), "Jul 2025"
        )

    def test_falls_back_to_label_without_structured(self):
        # (b) no structured date → raw free-text label.
        self.assertEqual(format_applicable_period("Q1 2024", None, None), "Q1 2024")

    def test_partial_date_falls_back_to_label(self):
        # (d) partial "2025-07" is not a full ISO date → label fallback.
        self.assertEqual(
            format_applicable_period("July'25", "2025-07", None), "July'25"
        )

    def test_partial_date_without_label_returns_raw_string(self):
        # (d) partial/non-ISO with no label → last-resort raw start string.
        self.assertEqual(format_applicable_period(None, "July'25", None), "July'25")

    def test_non_string_effective_from_does_not_crash(self):
        # detail-path guard: a numeric effective_from must not raise TypeError.
        # str() coerces it; 20250701 is valid basic-ISO → normalized month.
        self.assertEqual(format_applicable_period(None, 20250701, None), "Jul 2025")

    def test_cross_year_range_keeps_both_years(self):
        self.assertEqual(
            format_applicable_period(None, "2025-12-01", "2026-02-28"),
            "Dec 2025 – Feb 2026",
        )

    def test_empty_effective_from_and_label_is_none(self):
        # (e) empty / null everything → None.
        self.assertIsNone(format_applicable_period(None, None, None))
        self.assertIsNone(format_applicable_period("", "", None))

    def test_bad_effective_to_ignored_not_fatal(self):
        # A broken effective_to must not sink an otherwise-valid start month.
        self.assertEqual(
            format_applicable_period(None, "2025-07-01", "not-a-date"), "Jul 2025"
        )


def _period(effective_from, effective_to=None, label=None):
    doc = {"commission_period": {}}
    if effective_from is not None:
        doc["commission_period"]["effective_from"] = effective_from
    if effective_to is not None:
        doc["commission_period"]["effective_to"] = effective_to
    if label is not None:
        doc["commission_period"]["period_label"] = label
    return doc


class ApplicablePeriodApiTests(DjangoTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="period@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _make(self, filename, cics_doc=None):
        doc = CommissionDocument.objects.create(
            original_filename=filename,
            status=CommissionDocument.Status.COMPLETED,
        )
        if cics_doc is not None:
            CICSResult.objects.create(document=doc, cics_doc=cics_doc)
        return doc

    def _list_rows(self):
        resp = self.client.get(reverse("commission-documents-list"))
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))
        return {row["original_filename"]: row for row in resp.data}

    def _detail(self, doc):
        resp = self.client.get(reverse("commission-documents-detail", args=[doc.id]))
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))
        return resp.data

    def test_list_normalizes_structured_period(self):
        # (a) end-to-end: annotation → serializer → "Jul 2025".
        self._make("chola.xlsx", _period("2025-07-01", "2025-07-31", "July Grid"))
        self.assertEqual(
            self._list_rows()["chola.xlsx"]["applicable_period"], "Jul 2025"
        )

    def test_list_falls_back_to_label(self):
        # (b) only a free-text label survives the JSON pull.
        self._make("icici.xlsx", _period(None, None, "July'25"))
        self.assertEqual(
            self._list_rows()["icici.xlsx"]["applicable_period"], "July'25"
        )

    def test_list_null_without_cics_result(self):
        # (c) no CICSResult → LEFT JOIN NULLs → None.
        self._make("inflight.xlsx", cics_doc=None)
        self.assertIsNone(self._list_rows()["inflight.xlsx"]["applicable_period"])

    def test_list_partial_date_returns_raw_string(self):
        # (d) partial "2025-07" with no label → raw string, no crash.
        self._make("partial.xlsx", _period("2025-07", None, None))
        self.assertEqual(
            self._list_rows()["partial.xlsx"]["applicable_period"], "2025-07"
        )

    def test_list_null_when_period_key_absent(self):
        # (f) CICSResult present but no commission_period key → None.
        self._make("nokey.xlsx", {"provider": {"provider_name": "Acme"}})
        self.assertIsNone(self._list_rows()["nokey.xlsx"]["applicable_period"])

    def test_list_detail_parity(self):
        # (g) same doc → same value from both endpoints, incl. a bad-date row.
        good = self._make("good.xlsx", _period("2025-07-01", "2025-09-30", None))
        bad = self._make("bad.xlsx", _period("July'25", None, "July'25"))
        rows = self._list_rows()
        self.assertEqual(
            rows["good.xlsx"]["applicable_period"],
            self._detail(good)["applicable_period"],
        )
        self.assertEqual(
            rows["bad.xlsx"]["applicable_period"],
            self._detail(bad)["applicable_period"],
        )
        self.assertEqual(rows["good.xlsx"]["applicable_period"], "Jul – Sep 2025")

    def test_list_is_a_single_query(self):
        # (h) locks the single JOIN + guards the `.only()` column list from drift.
        self._make("a.xlsx", _period("2025-07-01", None, None))
        self._make("b.xlsx", cics_doc=None)
        with self.assertNumQueries(1):
            self.client.get(reverse("commission-documents-list"))

    def test_list_smoke_returns_rows(self):
        # (i) proves the `.only(*_LIST_MODEL_FIELDS)` rewrite does not FieldError.
        self._make("a.xlsx", _period("2025-07-01", None, None))
        rows = self._list_rows()
        self.assertIn("a.xlsx", rows)
        self.assertIn("applicable_period", rows["a.xlsx"])
