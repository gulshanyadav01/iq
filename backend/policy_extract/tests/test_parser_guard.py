"""The PDF guard inside `_run_parser` (covers the file_link ingestion path).

Multipart uploads are validated at the API (see test_upload_validation), but
file_link submissions bypass that serializer, so `_run_parser` re-checks the
downloaded bytes before handing them to the PDF-only MinerU endpoint. A non-PDF
must fail cleanly (row FAILED, MinerU never called); a real PDF must pass the
guard and proceed.

Run from the backend container:
    python manage.py test policy_extract.tests.test_parser_guard -v 2
"""

from unittest.mock import MagicMock, patch

from celery.exceptions import Ignore
from django.test import TestCase

from core.tests.pdf_fixtures import make_pdf
from policy_extract import tasks
from policy_extract.models import PolicyExtractionRequest


def _storage_returning(body: bytes) -> MagicMock:
    """A default_storage mock whose open(...).read() yields ``body``."""
    storage = MagicMock()
    handle = MagicMock()
    handle.__enter__.return_value.read.return_value = body
    storage.open.return_value = handle
    return storage


class ParserGuardTests(TestCase):
    def setUp(self):
        # Extensionless filename mimics a file_link submission (stored as the bare
        # policy_identifier), so the guard must decide on content, not extension.
        self.req = PolicyExtractionRequest.objects.create(
            original_filename="PBP10356218",
            source_url="policy_extractions/originals/abc/PBP10356218",
            status=PolicyExtractionRequest.Status.PROCESSING,
        )

    def test_non_pdf_download_fails_before_mineru(self):
        storage = _storage_returning(b"<!DOCTYPE html><html>error page</html>")
        with (
            patch("django.core.files.storage.default_storage", storage),
            patch(
                "policy_extract.services.mineru_gemini_parser.MineruGeminiParser"
            ) as parser_cls,
        ):
            # _run_parser signals a handled terminal failure with Ignore().
            with self.assertRaises(Ignore):
                tasks._run_parser(str(self.req.id))

        parser_cls.assert_not_called()
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.FAILED)
        self.assertIn("Unsupported file type", self.req.error)

    def test_expired_file_link_fails_before_mineru(self):
        # Legacy file_link branch (no source_url): an expired presigned link
        # returns the storage error envelope (HTTP 200) in place of the PDF. The
        # request must fail with the expired-link message — not "Unsupported file
        # type" — MinerU must never run, and nothing is stored.
        req = PolicyExtractionRequest.objects.create(
            original_filename="PBP99999999",
            source_url="",
            status=PolicyExtractionRequest.Status.PROCESSING,
            metadata={"file_link": "https://example.com/expired.pdf"},
        )
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.content = (
            b'{"statusCode":24,"statusMsg":"The validity of the URL you are '
            b'requesting has expired."}'
        )
        resp.headers = {"Content-Type": "application/json"}
        storage = MagicMock()
        with (
            patch("policy_extract.tasks.requests.get", return_value=resp),
            patch("django.core.files.storage.default_storage", storage),
            patch(
                "policy_extract.services.mineru_gemini_parser.MineruGeminiParser"
            ) as parser_cls,
        ):
            with self.assertRaises(Ignore):
                tasks._run_parser(str(req.id))

        parser_cls.assert_not_called()
        storage.save.assert_not_called()
        req.refresh_from_db()
        self.assertEqual(req.status, PolicyExtractionRequest.Status.FAILED)
        self.assertIn("expired", req.error.lower())
        self.assertNotIn("Unsupported file type", req.error)

    def test_real_pdf_passes_guard_and_runs_parser(self):
        storage = _storage_returning(make_pdf())
        with (
            patch("django.core.files.storage.default_storage", storage),
            patch(
                "policy_extract.services.mineru_gemini_parser.MineruGeminiParser"
            ) as parser_cls,
        ):
            parser_cls.return_value.parse_pdf.return_value = {"extracted_fields": {}}
            result = tasks._run_parser(str(self.req.id))

        self.assertEqual(result, str(self.req.id))
        parser_cls.return_value.parse_pdf.assert_called_once()
        self.req.refresh_from_db()
        self.assertEqual(self.req.result["extracted_fields"], {})
