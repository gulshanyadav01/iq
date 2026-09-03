"""Upload-time file-type validation on the create endpoint.

`POST /api/policy-extract/requests/` accepts only files the extraction pipeline
can parse (PDF today). A non-PDF — the `.jsonl` that produced the observed MinerU
`400 Unsupported file type: jsonl` — must be rejected with 400 *before* the file
is stored or the processing chain is enqueued, so junk never reaches the queue.
The file_link JSON path is unaffected by this serializer and is guarded in the
task instead (see test_parser_guard).

Run from the backend container:
    python manage.py test policy_extract.tests.test_upload_validation -v 2
"""

from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from policy_extract.models import PolicyExtractionRequest

_URL = reverse("policy-extract-requests-list")


class UploadValidationTests(APITestCase):
    def _post(self, upload):
        # Patch both side effects so a rejected upload can be asserted to touch
        # neither storage nor the queue, and an accepted one doesn't hit MinIO.
        with (
            patch(
                "policy_extract.serializers.policy_extraction_request.default_storage.save"
            ) as save,
            patch(
                "policy_extract.views.policy_extraction_request.enqueue_processing_chain"
            ) as enqueue,
        ):
            res = self.client.post(_URL, {"file": upload}, format="multipart")
        return res, save, enqueue

    def test_jsonl_upload_rejected_before_storage_and_queue(self):
        upload = SimpleUploadedFile(
            "data.jsonl", b'{"a": 1}\n', content_type="application/x-jsonlines"
        )
        res, save, enqueue = self._post(upload)

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("file", res.data)
        save.assert_not_called()
        enqueue.assert_not_called()
        self.assertEqual(PolicyExtractionRequest.objects.count(), 0)

    def test_pdf_named_but_non_pdf_bytes_rejected(self):
        # A non-PDF renamed to .pdf is caught by the magic-byte sniff.
        upload = SimpleUploadedFile(
            "policy.pdf", b"totally not a pdf", content_type="application/pdf"
        )
        res, save, enqueue = self._post(upload)

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        save.assert_not_called()
        enqueue.assert_not_called()
        self.assertEqual(PolicyExtractionRequest.objects.count(), 0)

    def test_valid_pdf_accepted_and_enqueued(self):
        upload = SimpleUploadedFile(
            "policy.pdf", b"%PDF-1.4\n%stuff\n", content_type="application/pdf"
        )
        res, save, enqueue = self._post(upload)

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["status"], PolicyExtractionRequest.Status.INITIATED)
        req = PolicyExtractionRequest.objects.get()
        self.assertEqual(req.original_filename, "policy.pdf")
        save.assert_called_once()
        enqueue.assert_called_once_with(str(req.id))
