import openpyxl
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from master_data.models import MasterDataUpload
from master_data.serializers import (
    CarrierMasterUploadCreateSerializer,
    MasterDataUploadCreateSerializer,
    MasterDataUploadSerializer,
)
from master_data.services.chola_parsers import (
    parse_chola_product_hierarchy,
    parse_chola_rto_zone,
)
from master_data.services.icici_parsers import parse_icici_rto_grid
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
            workbook = openpyxl.load_workbook(
                upload.file, read_only=True, data_only=True
            )
            try:
                result = MasterDataLoader().load_workbook(workbook)
            finally:
                workbook.close()
        except Exception as exc:  # noqa: BLE001 — surface any loader failure to the user
            upload.status = MasterDataUpload.Status.FAILED
            upload.error_message = f"{type(exc).__name__}: {exc}"
            upload.save(update_fields=["status", "error_message", "updated_at"])
            response_serializer = MasterDataUploadSerializer(upload)
            return Response(
                response_serializer.data, status=status.HTTP_400_BAD_REQUEST
            )

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


# carrier -> parser + loader-upsert-method-name for CarrierMasterUploadViewSet.
# Only Chola and ICICI are supported today (chola_parsers.py /
# icici_parsers.py); an unlisted carrier or master_type is rejected with a
# 400 before the file is even parsed.
_CARRIER_MASTER_HANDLERS = {
    ("Chola", "rto_zone"): (parse_chola_rto_zone, "upsert_carrier_rto_zone_rows"),
    ("Chola", "product_hierarchy"): (
        parse_chola_product_hierarchy,
        "upsert_chola_product_hierarchy_rows",
    ),
    ("ICICI", "rto_zone"): (parse_icici_rto_grid, "upsert_carrier_rto_zone_rows"),
}


class CarrierMasterUploadViewSet(viewsets.GenericViewSet):
    """Upload + audit log for carrier-scoped masters (e.g. Chola's RTO-zone
    and product-hierarchy masters).

    POST /api/master-data/carrier-uploads/
        multipart/form-data with `carrier`, `master_type`
        (rto_zone|product_hierarchy), `file` (.xlsx). Runs the SAME shared
        parser (`chola_parsers.py`) and loader upsert methods as the
        committed-CSV seeding path, inside a single transaction, so a
        re-upload is idempotent (existing rows are updated, not duplicated).
    """

    # Unlike MasterDataUploadViewSet (AllowAny, predates this endpoint), a
    # carrier-master upload rewrites the zone/hierarchy tables every Chola
    # extraction reads, so it requires an authenticated caller. Locking the
    # sibling endpoint down too is tracked separately in TODOS.md.
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    queryset = MasterDataUpload.objects.all()
    serializer_class = CarrierMasterUploadCreateSerializer

    @extend_schema(
        summary="Upload a carrier-scoped master (e.g. Chola RTO-zone / product-hierarchy)",
        description=(
            "Upload a .xlsx workbook for a single carrier-scoped master table. "
            "Idempotent — existing rows are updated, new rows inserted."
        ),
        tags=["Master Data"],
        request=CarrierMasterUploadCreateSerializer,
        responses={201: MasterDataUploadSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = CarrierMasterUploadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        carrier = serializer.validated_data["carrier"]
        master_type = serializer.validated_data["master_type"]
        file_obj = serializer.validated_data["file"]

        handler = _CARRIER_MASTER_HANDLERS.get((carrier, master_type))
        if handler is None:
            return Response(
                {
                    "error": (
                        f"Unsupported carrier/master_type combination: "
                        f"{carrier!r}/{master_type!r}"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        parse_fn, upsert_method_name = handler

        upload = MasterDataUpload.objects.create(
            file=file_obj,
            uploaded_by=request.user if request.user.is_authenticated else None,
            status=MasterDataUpload.Status.PENDING,
        )

        try:
            with transaction.atomic():
                upload.file.open("rb")
                workbook = openpyxl.load_workbook(upload.file, data_only=True)
                try:
                    rows = parse_fn(workbook)
                finally:
                    workbook.close()
                loader = MasterDataLoader()
                getattr(loader, upsert_method_name)(rows)
        except Exception as exc:  # noqa: BLE001 — surface any failure to the user
            upload.status = MasterDataUpload.Status.FAILED
            upload.error_message = f"{type(exc).__name__}: {exc}"
            upload.save(update_fields=["status", "error_message", "updated_at"])
            response_serializer = MasterDataUploadSerializer(upload)
            return Response(
                response_serializer.data, status=status.HTTP_400_BAD_REQUEST
            )

        upload.status = MasterDataUpload.Status.SUCCESS
        upload.summary = loader.result.as_dict()
        upload.save(update_fields=["status", "summary", "updated_at"])
        response_serializer = MasterDataUploadSerializer(upload)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="List carrier-scoped master upload history",
        tags=["Master Data"],
        responses={200: MasterDataUploadSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset().order_by("-created_at")
        serializer = MasterDataUploadSerializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Get carrier-scoped master upload",
        tags=["Master Data"],
        responses={200: MasterDataUploadSerializer},
    )
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = MasterDataUploadSerializer(instance)
        return Response(serializer.data)
