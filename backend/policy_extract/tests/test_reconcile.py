"""The stuck-request reconciler (`policy_extract.services.reconcile`).

`_run_parser` writes its own terminal status, so the only rows left in `processing`
are ones where no code survived to do it: a hard time-limit SIGKILL, an OOM child
recycle, a container restart mid-parse. Nothing else will ever resolve them, so the
beat sweep (and `unstick_extractions`) has to.

Run from the backend container:
    python manage.py test policy_extract.tests.test_reconcile -v 2
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils.timezone import now

from policy_extract.models import PolicyExtractionRequest
from policy_extract.services.reconcile import (
    STUCK_ERROR,
    find_stuck_requests,
    reconcile_stuck_requests,
)

STALE_AFTER = 1500


class ReconcileTests(TestCase):
    def _make(self, *, result=None, age_seconds=0, status=None):
        req = PolicyExtractionRequest.objects.create(
            original_filename="policy.pdf",
            source_url="policy_extractions/originals/abc/policy.pdf",
            status=status or PolicyExtractionRequest.Status.PROCESSING,
            result=result,
        )
        if age_seconds:
            # updated_at is auto_now, so age it with a queryset update.
            PolicyExtractionRequest.objects.filter(pk=req.pk).update(
                updated_at=now() - timedelta(seconds=age_seconds)
            )
            req.refresh_from_db()
        return req

    def test_stale_row_with_result_is_completed_and_calls_back(self):
        # The parse succeeded; only the status write was lost. The caller still
        # deserves the callback it never got.
        req = self._make(result={"extracted_fields": {}}, age_seconds=STALE_AFTER + 60)

        with patch("policy_extract.tasks._fire_completed_callback") as fire:
            counts = reconcile_stuck_requests(STALE_AFTER)

        req.refresh_from_db()
        self.assertEqual(req.status, PolicyExtractionRequest.Status.COMPLETED)
        self.assertIsNone(req.error)
        self.assertEqual(counts["completed"], 1)
        self.assertEqual(counts["failed"], 0)
        fire.assert_called_once()
        self.assertEqual(fire.call_args[0][0].pk, req.pk)

    def test_stale_row_without_result_is_failed_with_an_explanation(self):
        req = self._make(age_seconds=STALE_AFTER + 60)

        with patch("policy_extract.tasks._fire_completed_callback") as fire:
            counts = reconcile_stuck_requests(STALE_AFTER)

        req.refresh_from_db()
        self.assertEqual(req.status, PolicyExtractionRequest.Status.FAILED)
        self.assertEqual(req.error, STUCK_ERROR)
        self.assertEqual(counts["failed"], 1)
        fire.assert_not_called()

    def test_fresh_processing_row_is_left_alone(self):
        # A parse can legitimately run for minutes; touching it would clobber a
        # healthy task and double-fire its callback.
        req = self._make(age_seconds=60)

        counts = reconcile_stuck_requests(STALE_AFTER)

        req.refresh_from_db()
        self.assertEqual(req.status, PolicyExtractionRequest.Status.PROCESSING)
        self.assertEqual(counts["scanned"], 0)

    def test_terminal_rows_are_never_scanned(self):
        for status in (
            PolicyExtractionRequest.Status.COMPLETED,
            PolicyExtractionRequest.Status.FAILED,
            PolicyExtractionRequest.Status.INITIATED,
        ):
            self._make(age_seconds=STALE_AFTER + 60, status=status)

        self.assertEqual(reconcile_stuck_requests(STALE_AFTER)["scanned"], 0)

    def test_row_that_finishes_mid_sweep_is_not_clobbered(self):
        # The sweep runs on a different worker than the parse. If the real task lands
        # between the select and the write, the conditional update must miss.
        req = self._make(result={"extracted_fields": {}}, age_seconds=STALE_AFTER + 60)

        original = find_stuck_requests

        def _racing_find(*args, **kwargs):
            rows = list(original(*args, **kwargs))
            PolicyExtractionRequest.objects.filter(pk=req.pk).update(
                status=PolicyExtractionRequest.Status.COMPLETED
            )
            return rows

        with (
            patch(
                "policy_extract.services.reconcile.find_stuck_requests",
                side_effect=_racing_find,
            ),
            patch("policy_extract.tasks._fire_completed_callback") as fire,
        ):
            counts = reconcile_stuck_requests(STALE_AFTER)

        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(counts["completed"], 0)
        fire.assert_not_called()

    def test_dry_run_reports_without_writing(self):
        req = self._make(age_seconds=STALE_AFTER + 60)

        with patch("policy_extract.tasks._fire_completed_callback") as fire:
            counts = reconcile_stuck_requests(STALE_AFTER, dry_run=True)

        req.refresh_from_db()
        self.assertEqual(req.status, PolicyExtractionRequest.Status.PROCESSING)
        self.assertEqual(counts["failed"], 1)
        fire.assert_not_called()

    def test_explicit_ids_bypass_the_age_filter(self):
        # An operator who has already written a row off shouldn't have to wait.
        req = self._make(age_seconds=5)

        counts = reconcile_stuck_requests(STALE_AFTER, ids=[str(req.id)])

        req.refresh_from_db()
        self.assertEqual(req.status, PolicyExtractionRequest.Status.FAILED)
        self.assertEqual(counts["failed"], 1)


class UnstickCommandTests(TestCase):
    def setUp(self):
        self.req = PolicyExtractionRequest.objects.create(
            original_filename="policy.pdf",
            status=PolicyExtractionRequest.Status.PROCESSING,
        )
        PolicyExtractionRequest.objects.filter(pk=self.req.pk).update(
            updated_at=now() - timedelta(seconds=STALE_AFTER + 600)
        )

    def test_command_is_dry_run_by_default(self):
        out = StringIO()
        call_command("unstick_extractions", stdout=out)

        self.req.refresh_from_db()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.PROCESSING)
        self.assertIn("Dry run", out.getvalue())
        self.assertIn(str(self.req.id), out.getvalue())

    def test_command_with_apply_repairs_the_row(self):
        out = StringIO()
        call_command("unstick_extractions", "--apply", stdout=out)

        self.req.refresh_from_db()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.FAILED)
        self.assertEqual(self.req.error, STUCK_ERROR)
