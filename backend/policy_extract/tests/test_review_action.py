"""The `review` action and review-queue list surfacing.

`POST /api/policy-extract/requests/{id}/review/` is the human-in-the-loop sign-off:
it overwrites edited field values (preserving the original), clears their
do_verification flag, and — when `approve` is true — moves the row to
review_status=approved with reviewed_at stamped. The list endpoint exposes a
`counts.needs_review` tally and a `review_status` drill-down filter.

Run from the backend container:
    python manage.py test policy_extract.tests.test_review_action -v 2
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from policy_extract.models import PolicyExtractionRequest

RS = PolicyExtractionRequest.ReviewStatus


def _completed(review_status, fields):
    return PolicyExtractionRequest.objects.create(
        original_filename="policy.pdf",
        source_url="policy_extractions/originals/abc/policy.pdf",
        status=PolicyExtractionRequest.Status.COMPLETED,
        review_status=review_status,
        result={"extracted_fields": fields},
    )


class ReviewActionTests(APITestCase):
    def setUp(self):
        self.req = _completed(
            RS.NEEDS_REVIEW,
            {
                "reg_num": {
                    "value": "MH12XY7788",
                    "confidence_score": 0.49,
                    "is_extracted": "TRUE",
                    "do_verification": "TRUE",
                },
                "cust_name": {
                    "value": "ROHIT",
                    "confidence_score": 0.9,
                    "is_extracted": "TRUE",
                    "do_verification": "FALSE",
                },
            },
        )

    def _url(self, pk):
        return reverse("policy-extract-requests-review", args=[pk])

    def test_edit_and_approve(self):
        res = self.client.post(
            self._url(self.req.id),
            {"fields": {"reg_num": "MH12XY7789"}, "approve": True},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["review_status"], RS.APPROVED)
        self.assertIsNotNone(res.data["reviewed_at"])

        self.req.refresh_from_db()
        reg = self.req.result["extracted_fields"]["reg_num"]
        self.assertEqual(reg["value"], "MH12XY7789")
        self.assertEqual(reg["original_value"], "MH12XY7788")
        self.assertEqual(reg["do_verification"], "FALSE")
        self.assertTrue(reg["edited"])
        self.assertEqual(self.req.review_status, RS.APPROVED)
        self.assertIsNotNone(self.req.reviewed_at)

    def test_approve_without_edits(self):
        res = self.client.post(self._url(self.req.id), {"approve": True}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["review_status"], RS.APPROVED)
        self.req.refresh_from_db()
        # Unedited value untouched.
        self.assertEqual(
            self.req.result["extracted_fields"]["reg_num"]["value"], "MH12XY7788"
        )

    def test_edit_without_approve_keeps_needs_review(self):
        res = self.client.post(
            self._url(self.req.id),
            {"fields": {"reg_num": "MH12XY7789"}, "approve": False},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["review_status"], RS.NEEDS_REVIEW)
        self.req.refresh_from_db()
        self.assertEqual(
            self.req.result["extracted_fields"]["reg_num"]["value"], "MH12XY7789"
        )

    def test_unknown_field_ignored(self):
        res = self.client.post(
            self._url(self.req.id),
            {"fields": {"not_a_field": "x"}, "approve": True},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.req.refresh_from_db()
        self.assertNotIn("not_a_field", self.req.result["extracted_fields"])

    def test_review_rejects_non_completed(self):
        self.req.status = PolicyExtractionRequest.Status.PROCESSING
        self.req.save(update_fields=["status"])
        res = self.client.post(self._url(self.req.id), {"approve": True}, format="json")
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)

    def test_retry_clears_review_fields(self):
        from django.utils.timezone import now

        self.req.review_status = RS.APPROVED
        self.req.reviewed_at = now()
        self.req.save(update_fields=["review_status", "reviewed_at"])

        from unittest.mock import patch

        with patch(
            "policy_extract.views.policy_extraction_request.enqueue_processing_chain"
        ):
            res = self.client.post(
                reverse("policy-extract-requests-retry", args=[self.req.id])
            )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.req.refresh_from_db()
        self.assertIsNone(self.req.review_status)
        self.assertIsNone(self.req.reviewed_at)


class ReviewListSurfacingTests(APITestCase):
    def setUp(self):
        _completed(RS.AUTO_APPROVED, {"a": {"value": "1"}})
        _completed(RS.NEEDS_REVIEW, {"a": {"value": "2"}})
        _completed(RS.NEEDS_REVIEW, {"a": {"value": "3"}})
        _completed(RS.APPROVED, {"a": {"value": "4"}})

    def test_counts_include_needs_review(self):
        res = self.client.get(reverse("policy-extract-requests-list"))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["counts"]["needs_review"], 2)

    def test_review_status_filter(self):
        res = self.client.get(
            reverse("policy-extract-requests-list"),
            {"review_status": "needs_review"},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 2)
        self.assertTrue(
            all(r["review_status"] == "needs_review" for r in res.data["results"])
        )
        # counts stay unscoped by the review filter so the card remains switchable.
        self.assertEqual(res.data["counts"]["needs_review"], 2)
