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
        # B4: mimetypes can't guess the type → application/octet-stream (and,
        # being non-preview-safe, it downloads).
        self.req.original_filename = "policy.weirdext"
        self.req.save(update_fields=["original_filename"])
        storage = self._mock_storage(exists=True)
        with patch("django.core.files.storage.default_storage", storage):
            res = self.client.get(self._url(self.req.id))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/octet-stream")
        self.assertTrue(res["Content-Disposition"].startswith("attachment"))

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
