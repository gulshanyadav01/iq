"""The split OCR / LLM pipeline (POLICY_EXTRACT_SPLIT_LLM).

`_run_ocr` (queue policy_extract) checkpoints MinerU's markdown on the row and hands
off to `_run_llm_map` (queue policy_extract_llm), so an OCR child holds its page-gate
reservation for nearly its whole life and an LLM failure never re-burns GPU work.

Run from the backend container:
    python manage.py test policy_extract.tests.test_split_pipeline -v 2
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from celery.exceptions import Ignore
from django.test import TestCase, override_settings
from django.utils.timezone import now

from core.services.mineru import GateWaitTimeout
from core.tests.pdf_fixtures import make_pdf
from policy_extract import tasks
from policy_extract.models import PolicyExtractionRequest
from policy_extract.services.reconcile import reconcile_stuck_requests

PARSER = "policy_extract.services.mineru_gemini_parser.MineruGeminiParser"


def _storage_returning(body: bytes) -> MagicMock:
    storage = MagicMock()
    handle = MagicMock()
    handle.__enter__.return_value.read.return_value = body
    storage.open.return_value = handle
    return storage


class _Base(TestCase):
    def setUp(self):
        self.req = PolicyExtractionRequest.objects.create(
            original_filename="policy.pdf",
            source_url="policy_extractions/originals/abc/policy.pdf",
            status=PolicyExtractionRequest.Status.INITIATED,
        )
        self.storage = _storage_returning(make_pdf(pages=3))


class RunOcrTests(_Base):
    def _run(self, markdown="# Policy\nNo. 123"):
        with (
            patch("django.core.files.storage.default_storage", self.storage),
            patch(PARSER) as parser_cls,
            patch.object(tasks._run_llm_map, "apply_async") as handoff,
        ):
            parser_cls.return_value.ocr.return_value = markdown
            try:
                tasks._run_ocr(str(self.req.id))
            except Ignore:
                pass
        self.req.refresh_from_db()
        return parser_cls, handoff

    def test_checkpoints_markdown_and_hands_off(self):
        parser_cls, handoff = self._run()

        parser_cls.return_value.ocr.assert_called_once()
        # The precheck's page count sizes the gate reservation.
        self.assertEqual(parser_cls.return_value.ocr.call_args.kwargs["page_count"], 3)
        parser_cls.return_value.parse_pdf.assert_not_called()
        self.assertEqual(self.req.ocr_markdown, "# Policy\nNo. 123")
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.PROCESSING)
        handoff.assert_called_once_with(
            args=[str(self.req.id)], queue=tasks.POLICY_EXTRACT_LLM_QUEUE
        )

    def test_redelivery_after_checkpoint_skips_mineru(self):
        self.req.ocr_markdown = "already done"
        self.req.save(update_fields=["ocr_markdown"])

        parser_cls, handoff = self._run()

        parser_cls.return_value.ocr.assert_not_called()
        handoff.assert_called_once()

    def test_empty_markdown_fails_instead_of_looping(self):
        _, handoff = self._run(markdown="  \n")

        handoff.assert_not_called()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.FAILED)
        self.assertIn("no text", self.req.error)

    def test_gate_timeout_requeues_fresh_without_spending_retries(self):
        # Workers are sized for the max GPU fleet, so waiting out the gate is
        # normal with fewer GPUs up; it must never count toward failing the doc.
        with (
            patch("django.core.files.storage.default_storage", self.storage),
            patch(PARSER) as parser_cls,
            patch.object(tasks._run_ocr, "retry") as retry,
            patch.object(tasks._run_ocr, "apply_async") as requeue,
        ):
            parser_cls.return_value.ocr.side_effect = GateWaitTimeout("full")
            with self.assertRaises(Ignore):
                tasks._run_ocr(str(self.req.id))
        retry.assert_not_called()
        requeue.assert_called_once()
        self.assertEqual(requeue.call_args.kwargs["args"], [str(self.req.id)])
        self.assertEqual(requeue.call_args.kwargs["queue"], "policy_extract")
        self.assertGreaterEqual(requeue.call_args.kwargs["countdown"], 5)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.INITIATED)
        self.assertIsNone(self.req.error)


class RunLlmMapTests(_Base):
    def test_maps_checkpoint_and_completes(self):
        self.req.ocr_markdown = "# Policy"
        self.req.save(update_fields=["ocr_markdown"])

        with (
            patch("django.core.files.storage.default_storage", self.storage),
            patch(PARSER) as parser_cls,
            patch("policy_extract.tasks._fire_completed_callback") as fire,
        ):
            parser_cls.return_value.parse_markdown.return_value = {
                "extracted_fields": {}
            }
            tasks._run_llm_map(str(self.req.id))

        parser_cls.return_value.parse_markdown.assert_called_once()
        self.assertEqual(
            parser_cls.return_value.parse_markdown.call_args.args[0], "# Policy"
        )
        # The markdown is uploaded with the other intermediates, as before the split.
        saved = [c.args[0] for c in self.storage.save.call_args_list]
        self.assertIn(
            f"policy_extractions/intermediates/{self.req.id}/policy.md", saved
        )
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.COMPLETED)
        fire.assert_called_once()

    def test_completed_row_is_a_no_op(self):
        self.req.status = PolicyExtractionRequest.Status.COMPLETED
        self.req.result = {"extracted_fields": {"x": 1}}
        self.req.ocr_markdown = "# Policy"
        self.req.save(update_fields=["status", "result", "ocr_markdown"])

        with patch(PARSER) as parser_cls:
            tasks._run_llm_map(str(self.req.id))
        parser_cls.return_value.parse_markdown.assert_not_called()

    def test_missing_checkpoint_restarts_ocr(self):
        with (
            patch(PARSER) as parser_cls,
            patch.object(tasks._run_ocr, "apply_async") as ocr,
        ):
            tasks._run_llm_map(str(self.req.id))
        parser_cls.return_value.parse_markdown.assert_not_called()
        ocr.assert_called_once_with(
            args=[str(self.req.id)], queue=tasks.POLICY_EXTRACT_QUEUE
        )


class EnqueueTests(TestCase):
    @override_settings(POLICY_EXTRACT_SPLIT_LLM=True)
    def test_flag_on_enqueues_ocr(self):
        with (
            patch.object(tasks._run_ocr, "apply_async") as ocr,
            patch.object(tasks._run_parser, "apply_async") as legacy,
        ):
            tasks.enqueue_processing_chain("abc")
        ocr.assert_called_once_with(args=["abc"], queue="policy_extract")
        legacy.assert_not_called()

    @override_settings(POLICY_EXTRACT_SPLIT_LLM=False)
    def test_flag_off_enqueues_legacy_parser(self):
        with (
            patch.object(tasks._run_ocr, "apply_async") as ocr,
            patch.object(tasks._run_parser, "apply_async") as legacy,
        ):
            tasks.enqueue_processing_chain("abc")
        legacy.assert_called_once_with(args=["abc"], queue="policy_extract")
        ocr.assert_not_called()


class ReconcileCheckpointTests(TestCase):
    def test_stale_row_with_checkpoint_is_requeued_not_failed(self):
        # OCR finished but the LLM stage was lost; failing it would waste the GPU work.
        req = PolicyExtractionRequest.objects.create(
            original_filename="policy.pdf",
            status=PolicyExtractionRequest.Status.PROCESSING,
            ocr_markdown="# Policy",
        )
        PolicyExtractionRequest.objects.filter(pk=req.pk).update(
            updated_at=now() - timedelta(seconds=3600)
        )

        with patch.object(tasks._run_llm_map, "apply_async") as llm:
            counts = reconcile_stuck_requests(1500)

        req.refresh_from_db()
        self.assertEqual(counts["requeued"], 1)
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(req.status, PolicyExtractionRequest.Status.INITIATED)
        self.assertIsNone(req.error)
        llm.assert_called_once_with(
            args=[str(req.pk)], queue=tasks.POLICY_EXTRACT_LLM_QUEUE
        )


class LegacyParserPageCountTests(_Base):
    def test_run_parser_passes_page_count_to_the_gate(self):
        with (
            patch("django.core.files.storage.default_storage", self.storage),
            patch(PARSER) as parser_cls,
        ):
            parser_cls.return_value.parse_pdf.return_value = {"extracted_fields": {}}
            tasks._run_parser(str(self.req.id))
        self.assertEqual(
            parser_cls.return_value.parse_pdf.call_args.kwargs["page_count"], 3
        )
