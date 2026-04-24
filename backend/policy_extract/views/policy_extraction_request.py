from drf_spectacular.utils import extend_schema
from rest_framework import viewsets, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from policy_extract.models import PolicyExtractionRequest
from policy_extract.serializers import (
    PolicyExtractionRequestCreateSerializer,
    PolicyExtractionRequestLinkCreateSerializer,
    PolicyExtractionRequestSerializer,
)
from policy_extract.tasks import enqueue_processing_chain


class PolicyExtractionRequestViewSet(viewsets.GenericViewSet):
    """
    Policy extraction API with background task queue processing.

    POST /api/policy-extract/requests/
        - Accepts multipart file upload with optional metadata
        - Returns immediately with request ID and status="initiated"
        - Enqueues processing chain in Celery

    GET /api/policy-extract/requests/{id}/
        - Poll current status and result
    """
    permission_classes = [AllowAny]
    queryset = PolicyExtractionRequest.objects.all()

    def get_serializer_class(self):
        if self.action == "create":
            return PolicyExtractionRequestCreateSerializer
        return PolicyExtractionRequestSerializer

    @extend_schema(
        summary="Submit policy extraction request",
        description="Accept file upload (multipart) or file_link (JSON). Returns extraction request with status=initiated.",
        tags=["Policy Extract"],
        request=PolicyExtractionRequestCreateSerializer,
        responses={201: PolicyExtractionRequestSerializer},
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
        description="List all extraction requests (newest first) with current processing status.",
        tags=["Policy Extract"],
        responses={200: PolicyExtractionRequestSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset().order_by('-created_at')
        serializer = PolicyExtractionRequestSerializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Get extraction request",
        description="Poll current processing status and extraction result.",
        tags=["Policy Extract"],
        responses={200: PolicyExtractionRequestSerializer},
    )
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = PolicyExtractionRequestSerializer(instance)
        return Response(serializer.data)
