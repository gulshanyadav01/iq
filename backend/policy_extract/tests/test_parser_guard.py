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

    def test_real_pdf_passes_guard_and_runs_parser(self):
        storage = _storage_returning(b"%PDF-1.5\n%real pdf bytes\n")
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
