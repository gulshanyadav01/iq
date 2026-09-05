"""`_run_parser` owns the terminal status of an extraction request.

The status write used to live in a separate chain link (`_set_completed_status`), so a
successful parse whose successor message was never published or delivered left the row
in `processing` forever — no result surfaced, no error, and `retry` refused it. The
completion write now happens in the same task that produced the result.

Run from the backend container:
    python manage.py test policy_extract.tests.test_completion_status -v 2
"""

from unittest.mock import MagicMock, patch

from celery.exceptions import Ignore
from django.test import TestCase, override_settings

from policy_extract import tasks
from policy_extract.models import PolicyExtractionRequest


def _storage_returning(body: bytes) -> MagicMock:
    storage = MagicMock()
    handle = MagicMock()
    handle.__enter__.return_value.read.return_value = body
    storage.open.return_value = handle
    return storage


class CompletionStatusTests(TestCase):
    def setUp(self):
        self.req = PolicyExtractionRequest.objects.create(
            original_filename="policy.pdf",
            source_url="policy_extractions/originals/abc/policy.pdf",
            status=PolicyExtractionRequest.Status.INITIATED,
        )
        self.storage = _storage_returning(b"%PDF-1.5\n%real pdf bytes\n")

    def _run(self):
        with (
            patch("django.core.files.storage.default_storage", self.storage),
            patch(
                "policy_extract.services.mineru_gemini_parser.MineruGeminiParser"
            ) as parser_cls,
        ):
            parser_cls.return_value.parse_pdf.return_value = {"extracted_fields": {}}
            result = tasks._run_parser(str(self.req.id))
        return result, parser_cls

    def test_successful_parse_marks_completed(self):
        # The whole point: one task writes both the result and `completed`.
        result, parser_cls = self._run()

        self.assertEqual(result, str(self.req.id))
        parser_cls.return_value.parse_pdf.assert_called_once()
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.COMPLETED)
        self.assertEqual(self.req.result["extracted_fields"], {})
        self.assertIsNone(self.req.error)

    def test_already_completed_request_is_not_reparsed(self):
        # acks_late redelivers the message if the child dies after the completion
        # write; re-running a multi-minute parse and re-firing the callback would be
        # actively harmful, so a finished request is a no-op.
        self.req.status = PolicyExtractionRequest.Status.COMPLETED
        self.req.result = {"extracted_fields": {"policy_number": {"value": "X"}}}
        self.req.save(update_fields=["status", "result"])

        with patch("policy_extract.tasks._fire_completed_callback") as fire:
            result, parser_cls = self._run()

        self.assertEqual(result, str(self.req.id))
        parser_cls.return_value.parse_pdf.assert_not_called()
        fire.assert_not_called()
        self.req.refresh_from_db()
        self.assertEqual(
            self.req.result["extracted_fields"]["policy_number"]["value"], "X"
        )

    def test_completed_before_processing_is_visible_mid_run(self):
        # The row must read `processing` while the parser actually runs — not while it
        # merely sits in the queue — so the UI distinguishes queued from in-flight.
        seen = {}

        def _capture(*args, **kwargs):
            seen["status"] = PolicyExtractionRequest.objects.get(id=self.req.id).status
            return {"extracted_fields": {}}

        with (
            patch("django.core.files.storage.default_storage", self.storage),
            patch(
                "policy_extract.services.mineru_gemini_parser.MineruGeminiParser"
            ) as parser_cls,
        ):
            parser_cls.return_value.parse_pdf.side_effect = _capture
            tasks._run_parser(str(self.req.id))

        self.assertEqual(seen["status"], PolicyExtractionRequest.Status.PROCESSING)

    def test_parse_failure_still_marks_failed(self):
        with (
            patch("django.core.files.storage.default_storage", self.storage),
            patch(
                "policy_extract.services.mineru_gemini_parser.MineruGeminiParser"
            ) as parser_cls,
        ):
            parser_cls.return_value.parse_pdf.side_effect = RuntimeError("mineru down")
            with self.assertRaises(Ignore):
                tasks._run_parser(str(self.req.id))

        self.req.refresh_from_db()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.FAILED)
        self.assertIn("mineru down", self.req.error)

    @override_settings(POLICY_EXTRACT_CALLBACK_URL="https://example.com/hook")
    def test_callback_failure_does_not_undo_completion(self):
        # A raising callback must not fall through to the except branch and flip a
        # good extraction to FAILED.
        with (
            patch("django.core.files.storage.default_storage", self.storage),
            patch(
                "policy_extract.services.mineru_gemini_parser.MineruGeminiParser"
            ) as parser_cls,
            patch(
                "policy_extract.tasks._fire_callback",
                side_effect=RuntimeError("serializer blew up"),
            ) as fire,
        ):
            parser_cls.return_value.parse_pdf.return_value = {"extracted_fields": {}}
            result = tasks._run_parser(str(self.req.id))

        self.assertEqual(result, str(self.req.id))
        fire.assert_called_once()
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.COMPLETED)
        self.assertIsNone(self.req.error)

    @override_settings(POLICY_EXTRACT_CALLBACK_URL="https://example.com/hook")
    def test_completion_callback_fires_after_status_write(self):
        # Status is persisted before the callback so a slow receiver can never leave
        # the row stuck in `processing` — and so the payload reports `completed`.
        seen = {}

        def _capture(url, req, event_type):
            seen["event_type"] = event_type
            seen["db_status"] = PolicyExtractionRequest.objects.get(
                id=self.req.id
            ).status

        with (
            patch("django.core.files.storage.default_storage", self.storage),
            patch(
                "policy_extract.services.mineru_gemini_parser.MineruGeminiParser"
            ) as parser_cls,
            patch("policy_extract.tasks._fire_callback", side_effect=_capture),
        ):
            parser_cls.return_value.parse_pdf.return_value = {"extracted_fields": {}}
            tasks._run_parser(str(self.req.id))

        self.assertEqual(seen["event_type"], "policy_extract.completed")
        self.assertEqual(seen["db_status"], PolicyExtractionRequest.Status.COMPLETED)


class EnqueueTests(TestCase):
    def test_enqueue_dispatches_a_single_self_contained_task(self):
        # Deliberately not a chain: no separate status message that can go missing.
        with patch("policy_extract.tasks._run_parser.apply_async") as apply_async:
            tasks.enqueue_processing_chain("abc")

        apply_async.assert_called_once_with(args=["abc"], queue="policy_extract")
