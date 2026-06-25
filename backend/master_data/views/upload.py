import openpyxl
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from master_data.models import MasterDataUpload
from master_data.serializers import (
    MasterDataUploadCreateSerializer,
    MasterDataUploadSerializer,
)
from master_data.services.loader import MasterDataLoader


class MasterDataUploadViewSet(viewsets.GenericViewSet):
    """Upload + audit log for Master Mapper Excel ingests.

    POST /api/master-data/uploads/
        multipart/form-data with `file` (.xlsx). Synchronous; runs the loader
        inside a single transaction and returns per-sheet created/updated counts.

    GET /api/master-data/uploads/
        List recent uploads (newest first).

    GET /api/master-data/uploads/{id}/
        Fetch a single upload record.
    """

    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]
    queryset = MasterDataUpload.objects.all()

    def get_serializer_class(self):
        if self.action == "create":
            return MasterDataUploadCreateSerializer
        return MasterDataUploadSerializer

    @extend_schema(
        summary="Upload Master Mapper Excel",
        description=(
            "Upload a .xlsx workbook to refresh the master tables (insurer, vehicle, "
            "fuel, plan, carrier, etc.). Idempotent — existing rows are updated, new "
            "rows inserted."
        ),
        tags=["Master Data"],
        request=MasterDataUploadCreateSerializer,
        responses={201: MasterDataUploadSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = MasterDataUploadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        file_obj = serializer.validated_data["file"]

        upload = MasterDataUpload.objects.create(
            file=file_obj,
            uploaded_by=request.user if request.user.is_authenticated else None,
            status=MasterDataUpload.Status.PENDING,
        )

        try:
            upload.file.open("rb")
            workbook = openpyxl.load_workbook(upload.file, read_only=True, data_only=True)
            try:
                result = MasterDataLoader().load_workbook(workbook)
            finally:
                workbook.close()
        except Exception as exc:  # noqa: BLE001 — surface any loader failure to the user
            upload.status = MasterDataUpload.Status.FAILED
            upload.error_message = f"{type(exc).__name__}: {exc}"
            upload.save(update_fields=["status", "error_message", "updated_at"])
            response_serializer = MasterDataUploadSerializer(upload)
            return Response(response_serializer.data, status=status.HTTP_400_BAD_REQUEST)

        upload.status = MasterDataUpload.Status.SUCCESS
        upload.summary = result.as_dict()
        upload.save(update_fields=["status", "summary", "updated_at"])
        response_serializer = MasterDataUploadSerializer(upload)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="List master-data upload history",
        tags=["Master Data"],
        responses={200: MasterDataUploadSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset().order_by("-created_at")
        serializer = MasterDataUploadSerializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Get master-data upload",
        tags=["Master Data"],
        responses={200: MasterDataUploadSerializer},
    )
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = MasterDataUploadSerializer(instance)
        return Response(serializer.data)
