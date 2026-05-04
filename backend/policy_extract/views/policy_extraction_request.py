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
    """
    permission_classes = [AllowAny]
    queryset = PolicyExtractionRequest.objects.all()
    serializer_class = PolicyExtractionRequestCreateSerializer

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
