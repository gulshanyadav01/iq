"""Transient MinerU failures are retried; permanent ones fail the request.

A 502 (container restarting), 503 (limiter full), 504 or network error used to fail
the document for good. `_run_parser` now re-queues those with backoff and leaves the
row `initiated` while it waits; a 4xx/500, a PDF that fails the local pre-check, or
an exhausted retry budget still takes the terminal FAILED path.

Run from the backend container:
    python manage.py test policy_extract.tests.test_mineru_retry -v 2
"""

from unittest.mock import MagicMock, patch

import requests
from celery.exceptions import Ignore, Retry
from django.test import TestCase, override_settings

from core.tests.pdf_fixtures import make_pdf
from policy_extract import tasks
from policy_extract.models import PolicyExtractionRequest


def _storage_returning(body: bytes) -> MagicMock:
    storage = MagicMock()
    handle = MagicMock()
    handle.__enter__.return_value.read.return_value = body
    storage.open.return_value = handle
    return storage


def _mineru_error(status: int) -> requests.HTTPError:
    response = MagicMock()
    response.status_code = status
    return requests.HTTPError(f"MinerU API error {status}", response=response)


@override_settings(POLICY_EXTRACT_CALLBACK_URL="https://example.com/hook")
class MineruRetryTests(TestCase):
    def setUp(self):
        self.req = PolicyExtractionRequest.objects.create(
            original_filename="policy.pdf",
            source_url="policy_extractions/originals/abc/policy.pdf",
            status=PolicyExtractionRequest.Status.INITIATED,
        )

    def _run(self, *, body=None, parse_error=None):
        storage = _storage_returning(body if body is not None else make_pdf())
        with (
            patch("django.core.files.storage.default_storage", storage),
            patch(
                "policy_extract.services.mineru_gemini_parser.MineruGeminiParser"
            ) as parser_cls,
            patch("policy_extract.tasks._fire_callback") as fire,
            # Called directly (not via a worker), Task.retry re-raises the original
            # error; stand in for the worker path, which raises Retry.
            patch.object(
                tasks._run_parser, "retry", side_effect=Retry("retry")
            ) as retry,
        ):
            parser_cls.return_value.parse_pdf.side_effect = parse_error
            parser_cls.return_value.parse_pdf.return_value = {"extracted_fields": {}}
            try:
                tasks._run_parser(str(self.req.id))
            except (Retry, Ignore) as exc:
                raised = exc
            else:
                raised = None
        self.req.refresh_from_db()
        return raised, parser_cls, fire, retry

    def test_transient_statuses_are_retried(self):
        for status in (502, 503, 504):
            with self.subTest(status=status):
                raised, _, fire, retry = self._run(parse_error=_mineru_error(status))
                self.assertIsInstance(raised, Retry)
                retry.assert_called_once()
                self.assertGreaterEqual(retry.call_args.kwargs["countdown"], 0)
                self.assertEqual(
                    self.req.status, PolicyExtractionRequest.Status.INITIATED
                )
                self.assertFalse(self.req.error)
                fire.assert_not_called()

    def test_connection_error_is_retried(self):
        raised, *_ = self._run(parse_error=requests.ConnectionError("reset"))
        self.assertIsInstance(raised, Retry)

    def test_bad_request_fails_without_retry(self):
        raised, _, fire, retry = self._run(parse_error=_mineru_error(400))
        self.assertIsInstance(raised, Ignore)
        retry.assert_not_called()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.FAILED)
        fire.assert_called_once()
        self.assertEqual(fire.call_args.args[2], "policy_extract.failed")

    def test_exhausted_retries_fail(self):
        with patch.object(tasks._run_parser, "max_retries", 0):
            raised, _, fire, retry = self._run(parse_error=_mineru_error(502))
        self.assertIsInstance(raised, Ignore)
        retry.assert_not_called()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.FAILED)
        self.assertIn("502", self.req.error)

    def test_over_page_cap_fails_before_mineru(self):
        raised, parser_cls, _, retry = self._run(body=make_pdf(51))
        self.assertIsInstance(raised, Ignore)
        parser_cls.assert_not_called()
        retry.assert_not_called()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.FAILED)
        self.assertIn("51 pages", self.req.error)
        self.assertEqual(self.req.error_category, "unsupported_file")

    def test_corrupt_pdf_fails_before_mineru(self):
        raised, parser_cls, *_ = self._run(body=b"%PDF-1.5\n%no objects\n")
        self.assertIsInstance(raised, Ignore)
        parser_cls.assert_not_called()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.FAILED)
        self.assertEqual(self.req.error_category, "unsupported_file")
