import uuid

from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import extend_schema, OpenApiCallback, OpenApiParameter
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from policy_extract.models import PolicyExtractionRequest
from policy_extract.serializers import (
    PolicyExtractionRequestCreateSerializer,
    PolicyExtractionRequestLinkCreateSerializer,
    PolicyExtractionRequestSerializer,
    PolicyExtractCallbackSerializer,
)
from policy_extract.tasks import enqueue_processing_chain

# Non-terminal statuses grouped as "in_progress" in both the counts breakdown and
# the status filter. Single source so the cards and the filter can never disagree.
IN_PROGRESS_STATUSES = [
    PolicyExtractionRequest.Status.INITIATED,
    PolicyExtractionRequest.Status.PROCESSING,
]


class PolicyExtractionRequestViewSet(viewsets.GenericViewSet):
    """
    Policy extraction API with background task queue processing.

    POST /api/policy-extract/requests/
        - Accepts multipart file upload or file_link JSON
        - Returns immediately with request ID and status="initiated"
        - Enqueues processing chain in Celery
        - On completion/failure, a callback is POSTed to settings.POLICY_EXTRACT_CALLBACK_URL

    GET /api/policy-extract/requests/
        - List all extraction requests (newest first). Inspection/admin use only;
          clients should rely on the callback for the canonical outcome.

    GET /api/policy-extract/requests/{id}/
        - Fetch a single request. Useful for one-off recovery if a callback was missed.
    """

    permission_classes = [AllowAny]
    queryset = PolicyExtractionRequest.objects.all()

    def get_serializer_class(self):
        if self.action == "create":
            return PolicyExtractionRequestCreateSerializer
        return PolicyExtractionRequestSerializer

    @extend_schema(
        summary="Submit policy extraction request",
        description="Accept file upload (multipart) or file_link (JSON). Returns extraction request with status=initiated. "
        "On completion or failure, a POST callback is sent to the server-configured callback URL.",
        tags=["Policy Extract"],
        request=PolicyExtractionRequestCreateSerializer,
        responses={201: PolicyExtractionRequestSerializer},
        callbacks=[
            OpenApiCallback(
                name="policyExtractCallback",
                path="{$settings.POLICY_EXTRACT_CALLBACK_URL}",
                decorator=extend_schema(
                    summary="Policy extraction callback",
                    description="POSTed by the server when extraction completes or fails.",
                    request=PolicyExtractCallbackSerializer,
                    responses={200: None},
                ),
            )
        ],
    )
    def create(self, request, *args, **kwargs):
        if "file_link" in request.data:
            serializer = PolicyExtractionRequestLinkCreateSerializer(data=request.data)
        else:
            serializer = PolicyExtractionRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        extraction_request = serializer.save()
        enqueue_processing_chain(str(extraction_request.id))
        response_serializer = PolicyExtractionRequestSerializer(extraction_request)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="List extraction requests",
        description="Paginated list of extraction requests (newest first). Response includes "
        "DRF pagination keys (count/next/previous/results) plus a `counts` breakdown "
        "by status and a `buckets` breakdown of failures by error_category. Both "
        "breakdowns are scoped to the search and date-range filters but NOT the "
        "status/error_category selection (so the cards and buckets stay switchable). "
        "Intended for inspection/admin; clients should rely on the configured callback "
        "for canonical results, not on polling this endpoint.",
        tags=["Policy Extract"],
        parameters=[
            OpenApiParameter(
                name="search",
                type=str,
                description="Case-insensitive substring match over the partner form number "
                "(metadata.form_num), original filename and policy_extraction_id, "
                "plus an exact match on the request id (UUID). The `counts` breakdown "
                "is scoped to this filter.",
            ),
            OpenApiParameter(
                name="status",
                type=str,
                enum=[
                    "completed",
                    "failed",
                    "in_progress",
                    "initiated",
                    "processing",
                ],
                description="Filter the list by status. `in_progress` groups "
                "`initiated` + `processing` (matching the UI cards). Unknown values "
                "are ignored. The `counts` breakdown is NOT scoped by this filter, "
                "so all four status cards stay populated and switchable.",
            ),
            OpenApiParameter(
                name="error_category",
                type=str,
                enum=[
                    "expired_link",
                    "unsupported_file",
                    "missing_source",
                    "timeout",
                    "download_failed",
                    "parser_failed",
                    "other",
                ],
                description="Drill-down filter by failure reason (only failed "
                "requests carry a category, so this narrows to failures). Unknown "
                "values are ignored. The `buckets` breakdown in the response is NOT "
                "scoped by this filter, so every bucket stays switchable.",
            ),
            OpenApiParameter(
                name="ordering",
                type=str,
                description="Sort field: one of created_at, updated_at, "
                "original_filename, status. Prefix with `-` for descending. "
                "Defaults to `-created_at` (newest first). Unknown fields fall "
                "back to the default.",
            ),
            OpenApiParameter(
                name="created_after",
                type=str,
                description="Keep only requests submitted at or after this "
                "ISO-8601 datetime (e.g. 2026-09-03T00:00:00Z). Unparsable "
                "values are ignored. The `counts` breakdown is scoped to this "
                "range.",
            ),
            OpenApiParameter(
                name="created_before",
                type=str,
                description="Keep only requests submitted at or before this "
                "ISO-8601 datetime. A date-only value is treated as that day's "
                "midnight (00:00), so pass a full datetime to include the whole "
                "day. Unparsable values are ignored. The `counts` breakdown is "
                "scoped to this range.",
            ),
        ],
        responses={200: PolicyExtractionRequestSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        # `base` carries the filters that also scope the `counts` breakdown:
        # free-text search and the submitted-date range. The status selection is
        # applied to the list only (below), NOT to `base` — the four status cards
        # double as the status switcher, so each must keep showing its count
        # within the current search/date window regardless of which one is active.
        base = self.get_queryset()
        # Bound the term length so a pathologically long value can't blow up the
        # LIKE patterns run across the table on every (unauthenticated) call.
        search = request.query_params.get("search", "").strip()[:200]
        if search:
            filters = (
                Q(metadata__form_num__icontains=search)
                | Q(original_filename__icontains=search)
                | Q(policy_extraction_id__icontains=search)
            )
            # Match our internal UUID only when the term is a valid UUID
            # (avoids a database cast error on non-UUID search terms).
            try:
                filters |= Q(id=uuid.UUID(search))
            except (ValueError, TypeError):
                pass
            base = base.filter(filters)

        # Submitted (created_at) datetime range. Naive values are assumed to be
        # in the server's current timezone; unparsable values are ignored.
        def _parse_dt(raw):
            # Bound the length before parsing, mirroring the `search` cap — this
            # is an unauthenticated endpoint and a multi-MB value shouldn't reach
            # the parser.
            parsed = parse_datetime(raw.strip()[:64]) if raw else None
            if parsed and timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed)
            return parsed

        created_after = _parse_dt(request.query_params.get("created_after", ""))
        if created_after:
            base = base.filter(created_at__gte=created_after)
        created_before = _parse_dt(request.query_params.get("created_before", ""))
        if created_before:
            base = base.filter(created_at__lte=created_before)
        # Counts reflect search + date range (so the cards move with those
        # filters) but not the status selection (so all four stay switchable).
        counts = base.aggregate(
            total=Count("id"),
            completed=Count("id", filter=Q(status="completed")),
            failed=Count("id", filter=Q(status="failed")),
            in_progress=Count("id", filter=Q(status__in=IN_PROGRESS_STATUSES)),
        )
        # Failure-reason breakdown for the "failure buckets" UI. Scoped like
        # `counts` — search + date range but NOT the status/category selection —
        # so the buckets stay stable while one is active for drill-down. Only
        # failed rows carry an error_category, so this naturally excludes the
        # rest. Returned as {category: count}; a null category (older rows before
        # backfill) is bucketed under "other".
        bucket_rows = (
            base.filter(status="failed")
            # Clear the model's default ordering; otherwise created_at leaks into
            # the GROUP BY and splits each category into per-row groups.
            .order_by()
            .values("error_category")
            .annotate(count=Count("id"))
        )
        buckets = {
            (row["error_category"] or "other"): row["count"] for row in bucket_rows
        }
        # Status filter, grouped to match the UI metric cards (in_progress spans
        # both non-terminal statuses). Unknown values are ignored so a bad param
        # on this unauthenticated endpoint can't error.
        status_filters = {
            "completed": Q(status="completed"),
            "failed": Q(status="failed"),
            "in_progress": Q(status__in=IN_PROGRESS_STATUSES),
            "initiated": Q(status="initiated"),
            "processing": Q(status="processing"),
        }
        status_param = request.query_params.get("status", "").strip().lower()
        queryset = base
        if status_param in status_filters:
            queryset = queryset.filter(status_filters[status_param])
        # Drill-down by failure bucket. Only failed rows carry an error_category,
        # so this implicitly narrows to failures. Unknown values are ignored (like
        # the status filter) so a bad param on this unauthenticated endpoint can't
        # error. Not applied to `buckets`, so the breakdown stays switchable.
        category_param = request.query_params.get("error_category", "").strip().lower()
        if category_param in PolicyExtractionRequest.ErrorCategory.values:
            queryset = queryset.filter(error_category=category_param)
        # Whitelisted ordering; append -created_at, id as a stable tiebreaker so
        # pagination is deterministic when sorting by a field with many ties
        # (status/original_filename).
        ordering_fields = {"created_at", "updated_at", "original_filename", "status"}
        ordering = request.query_params.get("ordering", "").strip()
        # Honor only a single optional leading "-"; strip more than one and the
        # field would still pass a naive whitelist but blow up order_by() with a
        # FieldError (HTTP 500) on this unauthenticated endpoint.
        descending = ordering.startswith("-")
        field = ordering[1:] if descending else ordering
        if field in ordering_fields:
            normalized = ("-" if descending else "") + field
            # Stable tiebreaker: id alone when sorting by created_at (a dead
            # "created_at, -created_at" otherwise), else -created_at, id.
            tiebreak = ["id"] if field == "created_at" else ["-created_at", "id"]
            queryset = queryset.order_by(normalized, *tiebreak)
        else:
            queryset = queryset.order_by("-created_at", "id")
        page = self.paginate_queryset(queryset)
        serializer = PolicyExtractionRequestSerializer(page, many=True)
        response = self.get_paginated_response(serializer.data)
        response.data["counts"] = counts
        response.data["buckets"] = buckets
        return response

    @extend_schema(
        summary="Get extraction request",
        description="Fetch a single extraction request by id. Intended for one-off recovery if a callback was missed; "
        "not for client-driven polling.",
        tags=["Policy Extract"],
        responses={200: PolicyExtractionRequestSerializer},
    )
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = PolicyExtractionRequestSerializer(instance)
        return Response(serializer.data)

    @extend_schema(
        summary="Retry an extraction request",
        description="Re-enqueues a completed or failed request. Resets status to "
        "initiated and clears the previous error/result before dispatching the "
        "processing chain. Returns 409 if the request is still in flight.",
        tags=["Policy Extract"],
        responses={200: PolicyExtractionRequestSerializer},
    )
    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, *args, **kwargs):
        # Retry is allowed from a terminal status (completed/failed): reset the row
        # and re-enqueue. In-flight requests (initiated/processing) are rejected so
        # a second chain can't race the running one and double-fire the callback.
        instance = self.get_object()
        if instance.status in (
            PolicyExtractionRequest.Status.INITIATED,
            PolicyExtractionRequest.Status.PROCESSING,
        ):
            return Response(
                {
                    "detail": "Request is still processing; "
                    "wait for it to finish before retrying."
                },
                status=status.HTTP_409_CONFLICT,
            )
        instance.status = PolicyExtractionRequest.Status.INITIATED
        instance.error = None
        instance.error_category = None
        instance.result = None
        instance.callback_response = None
        instance.save(
            update_fields=[
                "status",
                "error",
                "error_category",
                "result",
                "callback_response",
                "updated_at",
            ]
        )
        enqueue_processing_chain(str(instance.id))
        return Response(PolicyExtractionRequestSerializer(instance).data)

    @extend_schema(
        summary="Stream the original uploaded document",
        description="Streams the original uploaded file so the browser can preview it. Preview-safe "
        "types (PDF/PNG/JPEG/GIF/WebP) are served inline; anything else is forced to "
        "download. Served through the app (not a presigned URL) to keep it same-origin for "
        "inline PDF rendering. Returns 404 for requests whose file was never stored (e.g. "
        "still-processing or failed submissions).",
        tags=["Policy Extract"],
    )
    @action(detail=True, methods=["get"], url_path="document")
    def document(self, request, *args, **kwargs):
        from django.core.files.storage import default_storage
        from django.http import FileResponse

        from policy_extract.services.content_type import (
            PREVIEW_SAFE_TYPES,
            resolve_content_type,
        )

        instance = self.get_object()
        if not instance.source_url or not default_storage.exists(instance.source_url):
            return Response(
                {"detail": "No document available."}, status=status.HTTP_404_NOT_FOUND
            )

        file = default_storage.open(instance.source_url, "rb")
        # Sniff the leading bytes so extensionless files (e.g. file_link uploads
        # named after the bare policy id) still get their real type; rewind so the
        # response streams from the start.
        head = file.read(16)
        file.seek(0)
        content_type = resolve_content_type(instance, head=head)
        # Only preview types the browser renders safely inline; anything else
        # (e.g. HTML/SVG that could carry script and run same-origin) is forced
        # to download. nosniff stops the browser second-guessing the type.
        inline_safe = PREVIEW_SAFE_TYPES
        response = FileResponse(
            file,
            content_type=content_type,
            as_attachment=content_type not in inline_safe,
            filename=instance.original_filename,
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response
