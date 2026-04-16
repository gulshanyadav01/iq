from drf_spectacular.utils import extend_schema
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from commission_intake.models import CommissionDocument
from commission_intake.serializers import (
    CommissionDocumentCreateSerializer,
    CommissionDocumentSerializer,
    ProcessingModeRequestSerializer,
)
from commission_intake.tasks import enqueue_processing_chain


class CommissionDocumentViewSet(viewsets.GenericViewSet):
    """
    Commission document intake with background Celery processing.

    POST /api/commission-intake/documents/
        - Accepts multipart file upload (.eml, .pdf, .xlsx)
        - Optional: llm_provider, llm_model, excel_mode
        - Returns immediately with document ID and status="initiated"
        - Enqueues: index → extract → validate chain in Celery

    GET /api/commission-intake/documents/
        - List all documents (newest first) with current status

    GET /api/commission-intake/documents/{id}/
        - Poll status; includes nested cics_result and validation_result when completed
    """

    permission_classes = [IsAuthenticated]
    queryset = CommissionDocument.objects.all()

    def get_serializer_class(self):
        if self.action == "create":
            return CommissionDocumentCreateSerializer
        return CommissionDocumentSerializer

    @extend_schema(
        request=CommissionDocumentCreateSerializer,
        responses={201: CommissionDocumentSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = CommissionDocumentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        doc = serializer.save(uploaded_by=request.user)
        enqueue_processing_chain(str(doc.id), mode=doc.processing_mode)
        return Response(CommissionDocumentSerializer(doc).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={200: CommissionDocumentSerializer(many=True)})
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset().order_by("-created_at")
        serializer = CommissionDocumentSerializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(responses={200: CommissionDocumentSerializer})
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = CommissionDocumentSerializer(instance)
        return Response(serializer.data)

    @extend_schema(
        request=ProcessingModeRequestSerializer,
        responses={200: CommissionDocumentSerializer},
    )
    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != CommissionDocument.Status.FAILED:
            return Response(
                {"detail": "Only failed documents can be retried."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        req_serializer = ProcessingModeRequestSerializer(data=request.data)
        req_serializer.is_valid(raise_exception=True)
        mode = req_serializer.validated_data.get("processing_mode") or instance.processing_mode
        instance.status = CommissionDocument.Status.INITIATED
        instance.error = None
        instance.processing_mode = mode
        instance.save(update_fields=["status", "error", "processing_mode", "updated_at"])
        enqueue_processing_chain(str(instance.id), mode=mode)
        return Response(CommissionDocumentSerializer(instance).data)

    @extend_schema(responses={200: CommissionDocumentSerializer})
    @action(detail=True, methods=["post"], url_path="index")
    def index(self, request, *args, **kwargs):
        instance = self.get_object()
        in_flight = (CommissionDocument.Status.INDEXING, CommissionDocument.Status.EXTRACTING)
        if instance.status in in_flight:
            return Response(
                {"detail": "Document is currently being processed."},
                status=status.HTTP_409_CONFLICT,
            )
        instance.status = CommissionDocument.Status.INITIATED
        instance.error = None
        instance.processing_mode = CommissionDocument.ProcessingMode.INDEX_ONLY
        instance.save(update_fields=["status", "error", "processing_mode", "updated_at"])
        enqueue_processing_chain(str(instance.id), mode="index_only")
        return Response(CommissionDocumentSerializer(instance).data)

    @extend_schema(responses={200: CommissionDocumentSerializer})
    @action(detail=True, methods=["post"], url_path="extract")
    def extract(self, request, *args, **kwargs):
        from commission_intake.models import IndexedContent

        instance = self.get_object()
        in_flight = (CommissionDocument.Status.INDEXING, CommissionDocument.Status.EXTRACTING)
        if instance.status in in_flight:
            return Response(
                {"detail": "Document is currently being processed."},
                status=status.HTTP_409_CONFLICT,
            )
        if not IndexedContent.objects.filter(document=instance).exists():
            return Response(
                {"detail": "Document must be indexed before extraction can run."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.status = CommissionDocument.Status.INITIATED
        instance.error = None
        instance.processing_mode = CommissionDocument.ProcessingMode.EXTRACT_ONLY
        instance.save(update_fields=["status", "error", "processing_mode", "updated_at"])
        enqueue_processing_chain(str(instance.id), mode="extract_only")
        return Response(CommissionDocumentSerializer(instance).data)
