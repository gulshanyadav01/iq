"""Search filtering on the extraction-request list endpoint.

``GET /api/policy-extract/requests/?search=<term>`` narrows the list with a
case-insensitive substring match over the partner form number
(``metadata.form_num``), the original filename and ``policy_extraction_id``,
plus an exact match on the internal request id (UUID). The ``counts`` breakdown
is scoped to the search filter. These tests pin that contract.

Run from the backend container:
    python manage.py test policy_extract.tests.test_list_search -v 2
"""

from django.urls import reverse
from rest_framework.test import APITestCase

from policy_extract.models import PolicyExtractionRequest


class ListSearchTests(APITestCase):
    def setUp(self):
        self.url = reverse("policy-extract-requests-list")
        self.with_form = PolicyExtractionRequest.objects.create(
            original_filename="motor_policy.pdf",
            status=PolicyExtractionRequest.Status.COMPLETED,
            metadata={"form_num": "PB123456", "RequestId": "abc"},
        )
        self.without_form = PolicyExtractionRequest.objects.create(
            original_filename="health_policy.pdf",
            status=PolicyExtractionRequest.Status.COMPLETED,
            metadata=None,
        )

    def _ids(self, response):
        return {row["id"] for row in response.data["results"]}

    def test_search_by_form_num_returns_only_matching(self):
        response = self.client.get(self.url, {"search": "PB123456"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.with_form.id)})

    def test_search_is_case_insensitive_substring(self):
        response = self.client.get(self.url, {"search": "pb1234"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.with_form.id)})

    def test_search_by_filename(self):
        response = self.client.get(self.url, {"search": "health"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.without_form.id)})

    def test_search_by_policy_extraction_id(self):
        response = self.client.get(
            self.url, {"search": self.with_form.policy_extraction_id}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.with_form.id)})

    def test_search_by_request_uuid_exact(self):
        response = self.client.get(self.url, {"search": str(self.with_form.id)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.with_form.id)})

    def test_non_uuid_term_does_not_error(self):
        # A non-UUID term must not raise a cast error; it just misses the id branch.
        response = self.client.get(self.url, {"search": "not-a-uuid"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), set())

    def test_like_wildcards_are_treated_literally(self):
        # Django escapes %, _ for icontains, so a term of pure wildcards must
        # match literally (nothing here) rather than matching every row.
        response = self.client.get(self.url, {"search": "%"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), set())

    def test_search_matches_numeric_form_num(self):
        numeric = PolicyExtractionRequest.objects.create(
            original_filename="numeric.pdf",
            status=PolicyExtractionRequest.Status.COMPLETED,
            metadata={"form_num": 987654},
        )
        response = self.client.get(self.url, {"search": "98765"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(numeric.id)})

    def test_blank_search_returns_all(self):
        response = self.client.get(self.url, {"search": "   "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._ids(response),
            {str(self.with_form.id), str(self.without_form.id)},
        )

    def test_counts_are_scoped_to_search(self):
        response = self.client.get(self.url, {"search": "PB123456"})
        self.assertEqual(response.status_code, 200)
        # Counts move with the search filter: only the matching row is counted.
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["counts"]["total"], 1)
        self.assertEqual(response.data["counts"]["completed"], 1)
