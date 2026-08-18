"""The `retry` action on PolicyExtractionRequestViewSet.

`POST /api/policy-extract/requests/{id}/retry/` re-runs the extraction pipeline
for a completed or failed request: the row is reset to `initiated` with its prior
error/result/callback cleared and the processing chain re-enqueued exactly once.
An in-flight request (initiated/processing) is rejected with 409 so a second chain
can't race the running one.

Run from the backend container:
    python manage.py test policy_extract.tests.test_retry_action -v 2
"""

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from policy_extract.models import PolicyExtractionRequest


class RetryActionTests(APITestCase):
    def setUp(self):
        self.req = PolicyExtractionRequest.objects.create(
            original_filename="policy.pdf",
            source_url="policy_extractions/originals/abc/policy.pdf",
            status=PolicyExtractionRequest.Status.FAILED,
            error="boom during parse",
            result={"extracted_fields": {"policy_number": {"value": "X"}}},
            callback_response={"ok": False, "status_code": 500},
        )

    def _url(self, pk):
        return reverse("policy-extract-requests-retry", args=[pk])

    def test_retry_failed_request_resets_and_reenqueues(self):
        # A failed request is reset to initiated, its prior error/result/callback
        # are cleared, and the processing chain is dispatched once for its id.
        with patch(
            "policy_extract.views.policy_extraction_request.enqueue_processing_chain"
        ) as enqueue:
            res = self.client.post(self._url(self.req.id))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], PolicyExtractionRequest.Status.INITIATED)
        self.assertIsNone(res.data["error"])
        self.assertIsNone(res.data["result"])

        self.req.refresh_from_db()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.INITIATED)
        self.assertIsNone(self.req.error)
        self.assertIsNone(self.req.result)
        self.assertIsNone(self.req.callback_response)
        enqueue.assert_called_once_with(str(self.req.id))

    def test_retry_completed_request_resets_and_reenqueues(self):
        # A completed request can be re-run too: it is reset to initiated, its
        # prior result is cleared, and the chain is dispatched once.
        self.req.status = PolicyExtractionRequest.Status.COMPLETED
        self.req.error = None
        self.req.save(update_fields=["status", "error"])

        with patch(
            "policy_extract.views.policy_extraction_request.enqueue_processing_chain"
        ) as enqueue:
            res = self.client.post(self._url(self.req.id))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], PolicyExtractionRequest.Status.INITIATED)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.INITIATED)
        self.assertIsNone(self.req.result)
        self.assertIsNone(self.req.callback_response)
        enqueue.assert_called_once_with(str(self.req.id))

    def test_retry_rejects_processing_request(self):
        # An in-flight (processing) request is rejected with 409 and never
        # re-enqueued, so a second chain can't race the running one.
        self.req.status = PolicyExtractionRequest.Status.PROCESSING
        self.req.save(update_fields=["status"])

        with patch(
            "policy_extract.views.policy_extraction_request.enqueue_processing_chain"
        ) as enqueue:
            res = self.client.post(self._url(self.req.id))

        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        enqueue.assert_not_called()
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, PolicyExtractionRequest.Status.PROCESSING)

    def test_retry_rejects_initiated_request(self):
        # A queued-but-not-yet-started request is also in flight → 409.
        self.req.status = PolicyExtractionRequest.Status.INITIATED
        self.req.save(update_fields=["status"])

        with patch(
            "policy_extract.views.policy_extraction_request.enqueue_processing_chain"
        ) as enqueue:
            res = self.client.post(self._url(self.req.id))

        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        enqueue.assert_not_called()

    def test_retry_404_for_unknown_request_id(self):
        with patch(
            "policy_extract.views.policy_extraction_request.enqueue_processing_chain"
        ) as enqueue:
            res = self.client.post(self._url("00000000-0000-0000-0000-000000000000"))

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        enqueue.assert_not_called()
