"""Request-time fetch of file_link (presigned URL) documents.

Presigned URLs are short-lived, so the linked document is downloaded and stored
while the request is handled — not lazily in the (possibly backlogged) Celery
task, which could dequeue after the URL has expired. A bad/expired link therefore
fails the submission synchronously (502), and a non-PDF fails it (400), rather
than being hidden behind an async failure callback. On success the row is created
with source_url pointing at the stored copy and the chain is enqueued.

Run from the backend container:
    python manage.py test policy_extract.tests.test_link_ingestion -v 2
"""

from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from policy_extract.models import PolicyExtractionRequest
from policy_extract.services.remote_fetch import (
    RemoteDocumentUnavailable,
    UnsupportedDocument,
    fetch_document_to_storage,
)

_URL = reverse("policy-extract-requests-list")
# The serializer imports this lazily inside create(), so patch it at its source
# module (that's the object the late `from ... import` binds to at call time).
_FETCH = "policy_extract.services.remote_fetch.fetch_document_to_storage"


def _response(content, headers=None):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.content = content
    # Real dict so remote_fetch's `resp.headers.get("Content-Type", "")` returns a
    # str, not a MagicMock (which storage_error_message can't scan for "json").
    resp.headers = headers or {}
    return resp


class FetchDocumentToStorageTests(SimpleTestCase):
    """Unit tests for the synchronous download helper (no DB, no network)."""

    def test_pdf_is_stored(self):
        resp = _response(b"%PDF-1.4 body bytes")
        with (
            patch(
                "policy_extract.services.remote_fetch.requests.get", return_value=resp
            ),
            patch("policy_extract.services.remote_fetch.default_storage.save") as save,
        ):
            fetch_document_to_storage("https://x/pdf", "key/PBP1", "PBP1")

        save.assert_called_once()
        self.assertEqual(save.call_args[0][0], "key/PBP1")

    def test_expired_or_unreachable_link_raises_502(self):
        # A presigned URL past its window / an unreachable host.
        with (
            patch(
                "policy_extract.services.remote_fetch.requests.get",
                side_effect=requests.ConnectionError("boom"),
            ),
            patch("policy_extract.services.remote_fetch.default_storage.save") as save,
        ):
            with self.assertRaises(RemoteDocumentUnavailable):
                fetch_document_to_storage("https://x/pdf", "key/PBP1", "PBP1")
        save.assert_not_called()

    def test_http_error_raises_502(self):
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.HTTPError("403 expired")
        with (
            patch(
                "policy_extract.services.remote_fetch.requests.get", return_value=resp
            ),
            patch("policy_extract.services.remote_fetch.default_storage.save") as save,
        ):
            with self.assertRaises(RemoteDocumentUnavailable):
                fetch_document_to_storage("https://x/pdf", "key/PBP1", "PBP1")
        save.assert_not_called()

    def test_expired_envelope_raises_502_and_is_not_stored(self):
        # An expired presigned link returns the storage service's JSON error body
        # (with HTTP 200, so raise_for_status passes) in place of the PDF. It must
        # surface as an unavailable link, not "unsupported file type".
        resp = _response(
            b'{"statusCode":24,"statusMsg":"The validity of the URL you are '
            b'requesting has expired."}',
            headers={"Content-Type": "application/json"},
        )
        with (
            patch(
                "policy_extract.services.remote_fetch.requests.get", return_value=resp
            ),
            patch("policy_extract.services.remote_fetch.default_storage.save") as save,
        ):
            with self.assertRaises(RemoteDocumentUnavailable) as ctx:
                fetch_document_to_storage("https://x/expired", "key/PBP1", "PBP1")
        self.assertIn("expired", str(ctx.exception.detail).lower())
        save.assert_not_called()

    def test_non_pdf_raises_400_and_is_not_stored(self):
        resp = _response(b"<!DOCTYPE html><body>nope</body>")
        with (
            patch(
                "policy_extract.services.remote_fetch.requests.get", return_value=resp
            ),
            patch("policy_extract.services.remote_fetch.default_storage.save") as save,
        ):
            with self.assertRaises(UnsupportedDocument):
                fetch_document_to_storage("https://x/html", "key/PBP1", "PBP1")
        save.assert_not_called()


class LinkIngestionViewTests(APITestCase):
    def _post(self):
        return self.client.post(
            _URL,
            {
                "file_link": "https://example.com/presigned.pdf",
                "policy_identifier": "PBP1",
            },
            format="json",
        )

    def test_valid_link_creates_row_with_source_url_and_enqueues(self):
        with (
            patch(_FETCH) as fetch,
            patch(
                "policy_extract.views.policy_extraction_request.enqueue_processing_chain"
            ) as enqueue,
        ):
            res = self._post()

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        fetch.assert_called_once()
        req = PolicyExtractionRequest.objects.get()
        self.assertTrue(req.source_url)  # stored up front, not left to the task
        self.assertEqual(
            req.metadata.get("file_link"), "https://example.com/presigned.pdf"
        )
        enqueue.assert_called_once_with(str(req.id))

    def test_expired_link_returns_502_and_creates_nothing(self):
        with (
            patch(_FETCH, side_effect=RemoteDocumentUnavailable()),
            patch(
                "policy_extract.views.policy_extraction_request.enqueue_processing_chain"
            ) as enqueue,
        ):
            res = self._post()

        self.assertEqual(res.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(PolicyExtractionRequest.objects.count(), 0)
        enqueue.assert_not_called()

    def test_non_pdf_link_returns_400_and_creates_nothing(self):
        with (
            patch(_FETCH, side_effect=UnsupportedDocument()),
            patch(
                "policy_extract.views.policy_extraction_request.enqueue_processing_chain"
            ) as enqueue,
        ):
            res = self._post()

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(PolicyExtractionRequest.objects.count(), 0)
        enqueue.assert_not_called()
