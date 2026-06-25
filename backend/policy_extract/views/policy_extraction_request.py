from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema, OpenApiCallback
from rest_framework import viewsets, status
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
                    "DRF pagination keys (count/next/previous/results) plus an all-time `counts` "
                    "breakdown by status. Intended for inspection/admin; clients should rely on the "
                    "configured callback for canonical results, not on polling this endpoint.",
        tags=["Policy Extract"],
        responses={200: PolicyExtractionRequestSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset().order_by('-created_at')
        counts = self.get_queryset().aggregate(
            total=Count('id'),
            completed=Count('id', filter=Q(status='completed')),
            failed=Count('id', filter=Q(status='failed')),
            in_progress=Count('id', filter=Q(status__in=['initiated', 'processing'])),
        )
        page = self.paginate_queryset(queryset)
        serializer = PolicyExtractionRequestSerializer(page, many=True)
        response = self.get_paginated_response(serializer.data)
        response.data['counts'] = counts
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
