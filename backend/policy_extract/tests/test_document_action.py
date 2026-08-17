"""The `document` action on PolicyExtractionRequestViewSet.

`GET /api/policy-extract/requests/{id}/document/` streams the original uploaded
file inline so the browser can preview it next to the extracted result. These
tests pin the contract: a stored file streams back with an inline
Content-Disposition and a guessed content type, and every "no file" path returns
a clean 404 instead of a 500.

Run from the backend container:
    python manage.py test policy_extract.tests.test_document_action -v 2
"""

import io
from unittest.mock import MagicMock, patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from policy_extract.models import PolicyExtractionRequest


class DocumentActionTests(APITestCase):
    def setUp(self):
        self.req = PolicyExtractionRequest.objects.create(
            original_filename="policy.pdf",
            source_url="policy_extractions/originals/abc/policy.pdf",
            status=PolicyExtractionRequest.Status.COMPLETED,
        )

    def _url(self, pk):
        return reverse("policy-extract-requests-document", args=[pk])

    def _mock_storage(self, exists, contents=b"%PDF-1.4 fake"):
        storage = MagicMock()
        storage.exists.return_value = exists
        storage.open.return_value = io.BytesIO(contents)
        return storage

    def test_streams_stored_file_inline(self):
        # B3: source_url set + file present → 200 FileResponse, inline disposition,
        # content type guessed from the filename.
        storage = self._mock_storage(exists=True, contents=b"%PDF-1.4 body")
        with patch("django.core.files.storage.default_storage", storage):
            res = self.client.get(self._url(self.req.id))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/pdf")
        # Preview-safe type → inline, and nosniff so the browser can't re-type it.
        self.assertEqual(res["Content-Disposition"], 'inline; filename="policy.pdf"')
        self.assertEqual(res["X-Content-Type-Options"], "nosniff")
        self.assertEqual(b"".join(res.streaming_content), b"%PDF-1.4 body")
        storage.open.assert_called_once_with(self.req.source_url, "rb")

    def test_unsafe_type_is_forced_to_download(self):
        # B4 + XSS hardening: a type the browser could execute inline (or an
        # unknown one) is served as an attachment, never inline.
        self.req.original_filename = "evil.html"
        self.req.save(update_fields=["original_filename"])
        storage = self._mock_storage(exists=True, contents=b"<script>alert(1)</script>")
        with patch("django.core.files.storage.default_storage", storage):
            res = self.client.get(self._url(self.req.id))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res["Content-Disposition"].startswith("attachment"))
        self.assertEqual(res["X-Content-Type-Options"], "nosniff")

    def test_unknown_extension_falls_back_to_octet_stream(self):
        # B4: mimetypes can't guess the type and the bytes match no known
        # signature → application/octet-stream (and, being non-preview-safe, it
        # downloads).
        self.req.original_filename = "policy.weirdext"
        self.req.save(update_fields=["original_filename"])
        storage = self._mock_storage(exists=True, contents=b"not a known format")
        with patch("django.core.files.storage.default_storage", storage):
            res = self.client.get(self._url(self.req.id))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/octet-stream")
        self.assertTrue(res["Content-Disposition"].startswith("attachment"))

    def test_extensionless_filename_uses_metadata_file_type(self):
        # file_link uploads are named after the bare policy id (no extension);
        # the MIME hint captured at submission serves them inline as a PDF.
        self.req.original_filename = "PBP10356218"
        self.req.metadata = {"file_type": "application/pdf"}
        self.req.save(update_fields=["original_filename", "metadata"])
        storage = self._mock_storage(exists=True, contents=b"%PDF-1.4 body")
        with patch("django.core.files.storage.default_storage", storage):
            res = self.client.get(self._url(self.req.id))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertEqual(res["Content-Disposition"], 'inline; filename="PBP10356218"')
        self.assertEqual(b"".join(res.streaming_content), b"%PDF-1.4 body")

    def test_extensionless_filename_sniffs_pdf_magic_bytes(self):
        # No extension and no metadata hint → the %PDF- signature is sniffed so
        # the file still previews inline instead of downloading.
        self.req.original_filename = "PBP10356432"
        self.req.metadata = None
        self.req.save(update_fields=["original_filename", "metadata"])
        storage = self._mock_storage(exists=True, contents=b"%PDF-1.4 body")
        with patch("django.core.files.storage.default_storage", storage):
            res = self.client.get(self._url(self.req.id))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res["Content-Disposition"].startswith("inline"))

    def test_dangerous_metadata_file_type_is_forced_to_download(self):
        # metadata.file_type is user-controlled free text from the file_link
        # submission. Even when it resolves the content type, a non-preview-safe
        # value (e.g. HTML that could run script same-origin) must still download.
        self.req.original_filename = "PBP10356218"
        self.req.metadata = {"file_type": "text/html"}
        self.req.save(update_fields=["original_filename", "metadata"])
        storage = self._mock_storage(exists=True, contents=b"<script>alert(1)</script>")
        with patch("django.core.files.storage.default_storage", storage):
            res = self.client.get(self._url(self.req.id))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "text/html")
        self.assertTrue(res["Content-Disposition"].startswith("attachment"))
        self.assertEqual(res["X-Content-Type-Options"], "nosniff")

    def test_head_is_sniffed_then_stream_is_rewound(self):
        # The view reads the leading bytes to sniff the type, then must seek(0)
        # so the response streams the whole file from the start — not a body
        # truncated past the consumed header.
        pdf_bytes = b"%PDF-1.4 full body content here"
        fake_file = MagicMock(wraps=io.BytesIO(pdf_bytes))
        self.req.original_filename = "PBP10356432"
        self.req.metadata = None
        self.req.save(update_fields=["original_filename", "metadata"])
        storage = MagicMock()
        storage.exists.return_value = True
        storage.open.return_value = fake_file
        with patch("django.core.files.storage.default_storage", storage):
            res = self.client.get(self._url(self.req.id))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/pdf")
        fake_file.seek.assert_any_call(0)
        self.assertEqual(b"".join(res.streaming_content), pdf_bytes)

    def test_404_when_source_url_missing(self):
        # B1: link-based / never-uploaded request has no source_url → 404, and we
        # never touch storage.
        self.req.source_url = None
        self.req.save(update_fields=["source_url"])
        storage = self._mock_storage(exists=True)
        with patch("django.core.files.storage.default_storage", storage):
            res = self.client.get(self._url(self.req.id))

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(res.data["detail"], "No document available.")
        storage.exists.assert_not_called()

    def test_404_when_file_absent_from_storage(self):
        # B2: source_url set but the object is gone from storage → 404, not 500.
        storage = self._mock_storage(exists=False)
        with patch("django.core.files.storage.default_storage", storage):
            res = self.client.get(self._url(self.req.id))

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(res.data["detail"], "No document available.")
        storage.open.assert_not_called()

    def test_404_for_unknown_request_id(self):
        # DRF get_object → 404 for a pk that doesn't exist.
        res = self.client.get(self._url("00000000-0000-0000-0000-000000000000"))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
