"""Status filtering, ordering and date-range filtering on the list endpoint.

``GET /api/policy-extract/requests/`` accepts:

- ``?status=`` — grouped to match the UI metric cards. ``in_progress`` spans
  both ``initiated`` and ``processing``; ``completed``/``failed`` and the raw
  ``initiated``/``processing`` values are also accepted. Unknown values are
  ignored (no error), and the all-time ``counts`` breakdown stays unaffected.
- ``?ordering=`` — one of ``created_at``, ``updated_at``, ``original_filename``,
  ``status`` (prefix ``-`` for descending). Defaults to ``-created_at``; an
  unknown field falls back to that default.

Run from the backend container:
    python manage.py test policy_extract.tests.test_list_filter_sort -v 2
"""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from policy_extract.models import PolicyExtractionRequest


class ListStatusFilterTests(APITestCase):
    def setUp(self):
        self.url = reverse("policy-extract-requests-list")
        self.initiated = PolicyExtractionRequest.objects.create(
            original_filename="a.pdf",
            status=PolicyExtractionRequest.Status.INITIATED,
        )
        self.processing = PolicyExtractionRequest.objects.create(
            original_filename="b.pdf",
            status=PolicyExtractionRequest.Status.PROCESSING,
        )
        self.completed = PolicyExtractionRequest.objects.create(
            original_filename="c.pdf",
            status=PolicyExtractionRequest.Status.COMPLETED,
        )
        self.failed = PolicyExtractionRequest.objects.create(
            original_filename="d.pdf",
            status=PolicyExtractionRequest.Status.FAILED,
        )

    def _ids(self, response):
        return {row["id"] for row in response.data["results"]}

    def test_status_completed(self):
        response = self.client.get(self.url, {"status": "completed"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.completed.id)})

    def test_status_failed(self):
        response = self.client.get(self.url, {"status": "failed"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.failed.id)})

    def test_status_in_progress_groups_initiated_and_processing(self):
        response = self.client.get(self.url, {"status": "in_progress"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._ids(response),
            {str(self.initiated.id), str(self.processing.id)},
        )

    def test_status_raw_initiated(self):
        response = self.client.get(self.url, {"status": "initiated"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.initiated.id)})

    def test_status_raw_processing(self):
        response = self.client.get(self.url, {"status": "processing"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.processing.id)})

    def test_status_is_case_insensitive(self):
        response = self.client.get(self.url, {"status": "Completed"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.completed.id)})

    def test_unknown_status_is_ignored_and_returns_all(self):
        response = self.client.get(self.url, {"status": "bogus"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 4)

    def test_counts_are_not_scoped_to_status(self):
        # The status cards double as the switcher, so selecting one must leave
        # every card's count intact (otherwise the others would zero out).
        response = self.client.get(self.url, {"status": "completed"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        counts = response.data["counts"]
        self.assertEqual(counts["total"], 4)
        self.assertEqual(counts["completed"], 1)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["in_progress"], 2)

    def test_status_and_search_compose(self):
        # Both filters apply (AND): only the completed row also matching the
        # filename term survives.
        PolicyExtractionRequest.objects.create(
            original_filename="c.pdf",
            status=PolicyExtractionRequest.Status.FAILED,
        )
        response = self.client.get(self.url, {"status": "completed", "search": "c.pdf"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.completed.id)})


class ListOrderingTests(APITestCase):
    def setUp(self):
        self.url = reverse("policy-extract-requests-list")
        # Created oldest → newest so created_at ordering is deterministic.
        self.first = PolicyExtractionRequest.objects.create(
            original_filename="charlie.pdf",
            status=PolicyExtractionRequest.Status.FAILED,
        )
        self.second = PolicyExtractionRequest.objects.create(
            original_filename="alpha.pdf",
            status=PolicyExtractionRequest.Status.COMPLETED,
        )
        self.third = PolicyExtractionRequest.objects.create(
            original_filename="bravo.pdf",
            status=PolicyExtractionRequest.Status.INITIATED,
        )

    def _ordered_ids(self, response):
        return [row["id"] for row in response.data["results"]]

    def test_default_is_newest_first(self):
        response = self.client.get(self.url)
        self.assertEqual(
            self._ordered_ids(response),
            [str(self.third.id), str(self.second.id), str(self.first.id)],
        )

    def test_ordering_created_at_ascending(self):
        response = self.client.get(self.url, {"ordering": "created_at"})
        self.assertEqual(
            self._ordered_ids(response),
            [str(self.first.id), str(self.second.id), str(self.third.id)],
        )

    def test_ordering_filename_ascending(self):
        response = self.client.get(self.url, {"ordering": "original_filename"})
        self.assertEqual(
            self._ordered_ids(response),
            [str(self.second.id), str(self.third.id), str(self.first.id)],
        )

    def test_ordering_filename_descending(self):
        response = self.client.get(self.url, {"ordering": "-original_filename"})
        self.assertEqual(
            self._ordered_ids(response),
            [str(self.first.id), str(self.third.id), str(self.second.id)],
        )

    def test_ordering_updated_at(self):
        # updated_at is auto_now; override it directly for a deterministic order
        # distinct from creation order (second oldest-updated, third newest).
        now = timezone.now()
        for obj, delta in ((self.first, 2), (self.second, 10), (self.third, 1)):
            PolicyExtractionRequest.objects.filter(pk=obj.pk).update(
                updated_at=now - timedelta(minutes=delta)
            )
        asc = self.client.get(self.url, {"ordering": "updated_at"})
        self.assertEqual(
            self._ordered_ids(asc),
            [str(self.second.id), str(self.first.id), str(self.third.id)],
        )
        desc = self.client.get(self.url, {"ordering": "-updated_at"})
        self.assertEqual(
            self._ordered_ids(desc),
            [str(self.third.id), str(self.first.id), str(self.second.id)],
        )

    def test_ordering_by_status(self):
        # Alphabetical on the stored status value: completed, failed, initiated.
        response = self.client.get(self.url, {"ordering": "status"})
        self.assertEqual(
            self._ordered_ids(response),
            [str(self.second.id), str(self.first.id), str(self.third.id)],
        )

    def test_unknown_ordering_falls_back_to_default(self):
        response = self.client.get(self.url, {"ordering": "not_a_field"})
        self.assertEqual(
            self._ordered_ids(response),
            [str(self.third.id), str(self.second.id), str(self.first.id)],
        )

    def test_ordering_injection_field_is_ignored(self):
        # A non-whitelisted field must not reach order_by() (would raise 500).
        response = self.client.get(self.url, {"ordering": "metadata; drop"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._ordered_ids(response),
            [str(self.third.id), str(self.second.id), str(self.first.id)],
        )

    def test_ordering_multi_dash_falls_back_to_default(self):
        # A crafted multi-dash value must not slip past the whitelist into
        # order_by("--created_at") (a FieldError → HTTP 500). It falls back to
        # the default newest-first ordering.
        response = self.client.get(self.url, {"ordering": "--created_at"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._ordered_ids(response),
            [str(self.third.id), str(self.second.id), str(self.first.id)],
        )

    def test_ordering_tiebreaker_is_created_at_desc(self):
        # Two rows sharing the ordering key fall back to the -created_at, id
        # tiebreaker (newest created first), keeping pagination deterministic.
        now = timezone.now()
        older = PolicyExtractionRequest.objects.create(
            original_filename="zeta.pdf",
            status=PolicyExtractionRequest.Status.COMPLETED,
        )
        newer = PolicyExtractionRequest.objects.create(
            original_filename="zeta.pdf",
            status=PolicyExtractionRequest.Status.COMPLETED,
        )
        PolicyExtractionRequest.objects.filter(pk=older.pk).update(
            created_at=now - timedelta(hours=2)
        )
        PolicyExtractionRequest.objects.filter(pk=newer.pk).update(
            created_at=now - timedelta(hours=1)
        )
        response = self.client.get(self.url, {"ordering": "original_filename"})
        # Both "zeta.pdf" rows sort last; the tie breaks newest-created first.
        self.assertEqual(
            self._ordered_ids(response)[-2:], [str(newer.id), str(older.id)]
        )


class ListDateRangeTests(APITestCase):
    def setUp(self):
        self.url = reverse("policy-extract-requests-list")
        self.now = timezone.now()
        self.old = self._make("old.pdf", self.now - timedelta(days=10))
        self.recent = self._make("recent.pdf", self.now - timedelta(hours=2))

    def _make(self, name, created_at):
        obj = PolicyExtractionRequest.objects.create(
            original_filename=name,
            status=PolicyExtractionRequest.Status.COMPLETED,
        )
        # created_at is auto_now_add; override it directly for a deterministic window.
        PolicyExtractionRequest.objects.filter(pk=obj.pk).update(created_at=created_at)
        return obj

    def _ids(self, response):
        return {row["id"] for row in response.data["results"]}

    def test_created_after_keeps_only_newer(self):
        cutoff = (self.now - timedelta(days=1)).isoformat()
        response = self.client.get(self.url, {"created_after": cutoff})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.recent.id)})

    def test_created_before_keeps_only_older(self):
        cutoff = (self.now - timedelta(days=1)).isoformat()
        response = self.client.get(self.url, {"created_before": cutoff})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.old.id)})

    def test_created_after_and_before_bound_a_window(self):
        response = self.client.get(
            self.url,
            {
                "created_after": (self.now - timedelta(days=1)).isoformat(),
                "created_before": (self.now + timedelta(hours=1)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.recent.id)})

    def test_created_after_naive_datetime_is_coerced(self):
        # A naive value (no tz offset) must be made aware in the server tz, not
        # rejected. Built from local wall-clock so it holds under any TIME_ZONE.
        naive_cutoff = (
            timezone.localtime(self.now - timedelta(days=1))
            .replace(tzinfo=None)
            .isoformat()
        )
        response = self.client.get(self.url, {"created_after": naive_cutoff})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.recent.id)})

    def test_unparsable_datetime_is_ignored(self):
        response = self.client.get(self.url, {"created_after": "not-a-date"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.old.id), str(self.recent.id)})

    def test_date_only_value_is_parsed_as_midnight(self):
        # A date-only value (no time component) is accepted and treated as
        # midnight of that day in the server tz, so a date-only created_after
        # works as a start-of-day cutoff.
        date_only = timezone.localtime(self.now - timedelta(days=1)).date().isoformat()
        response = self.client.get(self.url, {"created_after": date_only})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.recent.id)})

    def test_created_after_boundary_is_inclusive(self):
        # A cutoff exactly equal to a row's created_at includes that row (gte).
        cutoff = (self.now - timedelta(hours=2)).isoformat()
        response = self.client.get(self.url, {"created_after": cutoff})
        self.assertEqual(response.status_code, 200)
        self.assertIn(str(self.recent.id), self._ids(response))

    def test_counts_are_scoped_to_date_range(self):
        response = self.client.get(
            self.url, {"created_after": (self.now - timedelta(days=1)).isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        # Counts move with the date range: only the recent row is in-window.
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["counts"]["total"], 1)
        self.assertEqual(response.data["counts"]["completed"], 1)

    def test_status_selection_within_date_window_keeps_counts(self):
        # A recent failed row alongside the recent completed one. Filtering by
        # date + status=completed narrows the list to the completed row, but the
        # counts still reflect the whole in-window set (both statuses present).
        recent_failed = self._make("recent_failed.pdf", self.now - timedelta(hours=1))
        PolicyExtractionRequest.objects.filter(pk=recent_failed.pk).update(
            status=PolicyExtractionRequest.Status.FAILED
        )
        response = self.client.get(
            self.url,
            {
                "created_after": (self.now - timedelta(days=1)).isoformat(),
                "status": "completed",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.recent.id)})
        counts = response.data["counts"]
        self.assertEqual(counts["total"], 2)
        self.assertEqual(counts["completed"], 1)
        self.assertEqual(counts["failed"], 1)
