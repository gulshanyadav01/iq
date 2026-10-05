"""PDF_TO_MD: transient Minerva failures retry; unparsable PDFs fail before the call.

Run from the backend container:
    python manage.py test pipelines.tests.test_pdf_to_md_retry -v 2
"""

from unittest.mock import MagicMock, patch

import requests
from celery.exceptions import Ignore, Retry
from django.test import TestCase

from core.tests.pdf_fixtures import make_pdf
from pipelines import tasks
from pipelines.models import Pipeline, PipelineRun, StepRun
from pipelines.steps.pdf_to_md import MinervaError, PageCapExceeded, PdfToMdExecutor


def _storage_returning(body: bytes) -> MagicMock:
    storage = MagicMock()
    handle = MagicMock()
    handle.__enter__.return_value.read.return_value = body
    storage.open.return_value = handle
    return storage


def _minerva_error(status: int) -> MinervaError:
    response = MagicMock()
    response.status_code = status
    try:
        raise requests.HTTPError(f"{status}", response=response)
    except requests.HTTPError as exc:
        try:
            raise MinervaError(f"Minerva request failed: {exc}") from exc
        except MinervaError as wrapped:
            return wrapped


class PdfToMdRetryTests(TestCase):
    def setUp(self):
        pipeline = Pipeline.objects.create(name="Test", slug="test-pdf-to-md-retry")
        self.run = PipelineRun.objects.create(
            pipeline=pipeline, inputs={"pdf_key": "pipelines/uploads/x.pdf"}
        )
        self.step = StepRun.objects.create(
            pipeline_run=self.run, order=1, step_type="PDF_TO_MD"
        )

    def _execute(self, error):
        executor = MagicMock()
        executor.return_value.run.side_effect = error
        with (
            patch("pipelines.steps.registry.get_executor", return_value=executor),
            patch.object(
                tasks._execute_step, "retry", side_effect=Retry("retry")
            ) as retry,
        ):
            try:
                tasks._execute_step(str(self.step.id))
            except (Retry, Ignore) as exc:
                raised = exc
        self.step.refresh_from_db()
        return raised, retry

    def test_transient_minerva_error_retries_and_resets_step(self):
        raised, retry = self._execute(_minerva_error(503))
        self.assertIsInstance(raised, Retry)
        retry.assert_called_once()
        self.assertEqual(self.step.status, StepRun.Status.PENDING)

    def test_permanent_minerva_error_fails_the_run(self):
        raised, retry = self._execute(_minerva_error(400))
        self.assertIsInstance(raised, Ignore)
        retry.assert_not_called()
        self.assertEqual(self.step.status, StepRun.Status.FAILED)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, PipelineRun.Status.FAILED)

    def test_page_cap_rejected_before_minerva_call(self):
        with (
            patch(
                "pipelines.steps.pdf_to_md.default_storage",
                _storage_returning(make_pdf(51)),
            ),
            patch("pipelines.steps.pdf_to_md.get_session") as session,
        ):
            with self.assertRaises(PageCapExceeded):
                PdfToMdExecutor().run(step_run=self.step, prev_outputs=[])
        session.assert_not_called()

    def test_corrupt_pdf_rejected_before_minerva_call(self):
        with (
            patch(
                "pipelines.steps.pdf_to_md.default_storage",
                _storage_returning(b"%PDF-1.5\n%no objects\n"),
            ),
            patch("pipelines.steps.pdf_to_md.get_session") as session,
        ):
            with self.assertRaisesMessage(MinervaError, "encrypted or unreadable"):
                PdfToMdExecutor().run(step_run=self.step, prev_outputs=[])
        session.assert_not_called()
