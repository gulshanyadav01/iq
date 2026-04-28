import io

from django.db.models import Count, Q
from django.http import FileResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from commission_intake.models import CommissionDocument
from commission_intake.serializers import (
    CommissionDocumentCreateSerializer,
    CommissionDocumentSerializer,
    ProcessingModeRequestSerializer,
    RerunPassRequestSerializer,
)
from commission_intake.tasks import enqueue_processing_chain, rerun_single_pass, rerun_transform, run_validation


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
        summary="Upload commission document",
        description="Accepts multipart file upload (.eml/.pdf/.xlsx). Enqueues index→extract→validate chain. Returns immediately with status=initiated.",
        tags=["Commission Document"],
        request=CommissionDocumentCreateSerializer,
        responses={201: CommissionDocumentSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = CommissionDocumentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        doc = serializer.save(uploaded_by=request.user)
        enqueue_processing_chain(str(doc.id), mode=doc.processing_mode)
        return Response(CommissionDocumentSerializer(doc).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="List commission documents",
        description="Returns all documents (newest first) with current processing status.",
        tags=["Commission Document"],
        responses={200: CommissionDocumentSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset().order_by("-created_at")
        serializer = CommissionDocumentSerializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Get commission document",
        description="Poll current processing status. Includes nested cics_result and validation_result when completed.",
        tags=["Commission Document"],
        responses={200: CommissionDocumentSerializer},
    )
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = CommissionDocumentSerializer(instance)
        return Response(serializer.data)

    @extend_schema(
        summary="Retry failed document",
        description="Re-enqueues a FAILED document with optional processing mode override.",
        tags=["Commission Document"],
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
        instance.is_validated = False
        instance.validated_at = None
        instance.save(update_fields=[
            "status", "error", "processing_mode", "is_validated", "validated_at", "updated_at",
        ])
        enqueue_processing_chain(str(instance.id), mode=mode)
        return Response(CommissionDocumentSerializer(instance).data)

    @extend_schema(
        summary="Re-index document",
        description="Runs indexing step only (index_only mode).",
        tags=["Commission Document"],
        responses={200: CommissionDocumentSerializer},
    )
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
        instance.is_validated = False
        instance.validated_at = None
        instance.save(update_fields=[
            "status", "error", "processing_mode", "is_validated", "validated_at", "updated_at",
        ])
        enqueue_processing_chain(str(instance.id), mode="index_only")
        return Response(CommissionDocumentSerializer(instance).data)

    @extend_schema(
        summary="Re-run extraction",
        description="Requires document to be indexed. Runs LLM extraction passes.",
        tags=["Commission Document"],
        responses={200: CommissionDocumentSerializer},
    )
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
        instance.is_validated = False
        instance.validated_at = None
        instance.save(update_fields=[
            "status", "error", "processing_mode", "is_validated", "validated_at", "updated_at",
        ])
        enqueue_processing_chain(str(instance.id), mode="extract_only")
        return Response(CommissionDocumentSerializer(instance).data)

    @extend_schema(
        summary="Re-run single extraction pass",
        description="Re-runs one of pass 1/2/3 for a specific sheet/attachment.",
        tags=["Commission Document"],
        request=RerunPassRequestSerializer,
        responses={200: CommissionDocumentSerializer},
    )
    @action(detail=True, methods=["post"], url_path="rerun-pass")
    def rerun_pass(self, request, *args, **kwargs):
        from commission_intake.models import IndexedContent, ExtractionPass

        instance = self.get_object()
        in_flight = (CommissionDocument.Status.INDEXING, CommissionDocument.Status.EXTRACTING)
        if instance.status in in_flight:
            return Response(
                {"detail": "Document is currently being processed."},
                status=status.HTTP_409_CONFLICT,
            )
        if not IndexedContent.objects.filter(document=instance).exists():
            return Response(
                {"detail": "Document must be indexed before a pass can be re-run."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        req = RerunPassRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        pass_number = req.validated_data["pass_number"]
        sheet_name = req.validated_data.get("sheet_name", "")
        attachment_name = req.validated_data.get("attachment_name", "")

        # Mark the pass as pending immediately so the UI reflects queued state
        ExtractionPass.objects.update_or_create(
            document=instance,
            pass_number=pass_number,
            sheet_name=sheet_name,
            attachment_name=attachment_name,
            defaults={"status": ExtractionPass.Status.PENDING, "error": ""},
        )

        if instance.is_validated:
            instance.is_validated = False
            instance.validated_at = None
            instance.save(update_fields=["is_validated", "validated_at", "updated_at"])

        rerun_single_pass.apply_async(
            args=[str(instance.id), pass_number, sheet_name, attachment_name],
            queue="commission_intake",
        )
        return Response(CommissionDocumentSerializer(instance).data)

    @extend_schema(
        summary="Re-run CICS transformation",
        description="Rebuilds CICS result from existing completed passes.",
        tags=["Commission Document"],
        responses={200: CommissionDocumentSerializer},
    )
    @action(detail=True, methods=["post"], url_path="transform")
    def transform(self, request, *args, **kwargs):
        from commission_intake.models import ExtractionPass

        instance = self.get_object()
        in_flight = (CommissionDocument.Status.INDEXING, CommissionDocument.Status.EXTRACTING)
        if instance.status in in_flight:
            return Response(
                {"detail": "Document is currently being processed."},
                status=status.HTTP_409_CONFLICT,
            )
        if not ExtractionPass.objects.filter(
            document=instance, status=ExtractionPass.Status.COMPLETED
        ).exists():
            return Response(
                {"detail": "No completed extraction passes found. Run extraction first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rerun_transform.apply_async(args=[str(instance.id)], queue="commission_intake")
        return Response(CommissionDocumentSerializer(instance).data)

    @extend_schema(
        summary="Re-run validation",
        description="Runs schema + overlap validation on the existing CICS result.",
        tags=["Commission Document"],
        responses={200: CommissionDocumentSerializer},
    )
    @action(detail=True, methods=["post"], url_path="validate")
    def validate(self, request, *args, **kwargs):
        from commission_intake.models import CICSResult

        instance = self.get_object()
        in_flight = (CommissionDocument.Status.INDEXING, CommissionDocument.Status.EXTRACTING)
        if instance.status in in_flight:
            return Response(
                {"detail": "Document is currently being processed."},
                status=status.HTTP_409_CONFLICT,
            )
        if not CICSResult.objects.filter(document=instance).exists():
            return Response(
                {"detail": "No CICS result found. Run extraction and transform first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        run_validation.apply_async(args=[str(instance.id)], queue="commission_intake")
        return Response(CommissionDocumentSerializer(instance).data)

    @extend_schema(
        summary="Mark document as validated",
        description="Mark a completed document as manually validated.",
        tags=["Commission Document"],
        responses={200: CommissionDocumentSerializer},
    )
    @action(detail=True, methods=["post"], url_path="mark-validated")
    def mark_validated(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != CommissionDocument.Status.COMPLETED:
            return Response(
                {"detail": "Only completed documents can be marked as validated."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.is_validated = True
        instance.validated_at = timezone.now()
        instance.save(update_fields=["is_validated", "validated_at", "updated_at"])
        return Response(CommissionDocumentSerializer(instance).data)

    @extend_schema(
        summary="Remove validated flag",
        description="Remove the validated flag from a document.",
        tags=["Commission Document"],
        responses={200: CommissionDocumentSerializer},
    )
    @action(detail=True, methods=["post"], url_path="mark-invalidated")
    def mark_invalidated(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_validated = False
        instance.validated_at = None
        instance.save(update_fields=["is_validated", "validated_at", "updated_at"])
        return Response(CommissionDocumentSerializer(instance).data)

    @extend_schema(
        summary="Delete commission document",
        description="Permanently deletes a commission document and all related data.",
        tags=["Commission Document"],
        responses={204: None},
    )
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(exclude=True)
    @action(detail=False, methods=["get"], url_path="dashboard")
    def dashboard(self, request, *args, **kwargs):
        """Return document-level and provider-level extraction/validation stats."""
        in_flight_statuses = [
            CommissionDocument.Status.INITIATED,
            CommissionDocument.Status.INDEXING,
            CommissionDocument.Status.EXTRACTING,
        ]
        qs = self.get_queryset()
        document_stats = qs.aggregate(
            total=Count("id"),
            extracted=Count("id", filter=Q(status=CommissionDocument.Status.COMPLETED)),
            validated=Count("id", filter=Q(is_validated=True)),
            in_progress=Count("id", filter=Q(status__in=in_flight_statuses)),
            failed=Count("id", filter=Q(status=CommissionDocument.Status.FAILED)),
        )
        provider_stats = (
            qs.filter(status=CommissionDocument.Status.COMPLETED)
            .exclude(provider="")
            .values("provider")
            .annotate(
                extracted=Count("id"),
                validated=Count("id", filter=Q(is_validated=True)),
            )
            .order_by("-extracted")
        )
        return Response({
            "document_stats": document_stats,
            "provider_stats": list(provider_stats),
        })

    @extend_schema(exclude=True)
    @action(detail=True, methods=["get"], url_path="export-excel")
    def export_excel(self, request, *args, **kwargs):
        """Export flattened CICS products/rules as an Excel workbook matching the grid dump format."""
        from commission_intake.models import CICSResult

        instance = self.get_object()
        if not CICSResult.objects.filter(document=instance).exists():
            return Response({"detail": "No CICS result available."}, status=status.HTTP_404_NOT_FOUND)

        cics_doc = instance.cics_result.cics_doc or {}
        provider_name = (cics_doc.get("provider") or {}).get("provider_name", "")
        period = cics_doc.get("commission_period") or {}
        deal_start = period.get("effective_from", "")
        deal_end = period.get("effective_to", "")

        headers = [
            "DealId", "ReferenceDealId", "DealParameter", "DealStartDate", "DealEndDate",
            "Insurer", "Product", "SubProduct",
            "VehicleSubClass", "PolicyType", "State", "RTO", "NumberOfWheels",
            "GVWStartRange", "GVWEndRange", "CCStartRange", "CCEndRange",
            "SeatingCapacityStartRange", "SeatingCapacityEndRange",
            "SchoolBusType", "BookingMode", "ZeroDep", "NCB", "CPA",
            "FuelType", "VehicleAge", "VehicleMake", "VehicleModel",
            "VehicleCarrier", "BusinessType", "ODTerm", "TPTerm",
            "ODPercentage", "TPPercentage", "NetPercentage",
            "TPLessThanEqual3YearPercentage", "TPGreaterThanEqual4YearPercentage", "FlatAmount",
        ]

        def cv(v):
            """Normalise a cell value: join lists, stringify booleans, blank out None."""
            if v is None:
                return ""
            if isinstance(v, list):
                return ",".join(str(i) for i in v)
            if isinstance(v, bool):
                return "Yes" if v else "No"
            return v

        def cv_default(v, default):
            """Like cv() but returns default string when value is None."""
            if v is None:
                return default
            return cv(v)

        def outcome_percentages(outcome, deal_param):
            """
            Map a single CommissionOutcome to the 6 commission columns.
            Column assignment is driven by deal_parameter and commission_type.
            """
            od_pct = tp_pct = net_pct = tp_lte3 = tp_gte4 = flat = ""
            if not outcome:
                return od_pct, tp_pct, net_pct, tp_lte3, tp_gte4, flat

            pct = outcome.get("percentage_value")
            flat_val = outcome.get("flat_value")
            c_type = outcome.get("commission_type", "")

            params = [p.strip().lower() for p in (deal_param or "").split(",") if p.strip()]

            if c_type == "flat" and flat_val is not None:
                flat = flat_val
            elif "od" in params:
                od_pct = pct if pct is not None else ""
            elif "net" in params:
                net_pct = pct if pct is not None else ""
            elif "tp" in params or "regular-tp" in params:
                tp_pct = pct if pct is not None else ""
            elif "tp-with-vehicle-age" in params:
                # split between lte3 and gte4 if tiers present, else put in both
                tiers = outcome.get("tiers") or []
                if tiers:
                    for tier in tiers:
                        thresh = (tier.get("threshold") or {})
                        t_max = thresh.get("max")
                        if t_max is not None and t_max <= 3:
                            tp_lte3 = tier.get("value", "")
                        else:
                            tp_gte4 = tier.get("value", "")
                else:
                    tp_lte3 = pct if pct is not None else ""
                    tp_gte4 = pct if pct is not None else ""
            else:
                # fallback: percentage goes to OD column
                if pct is not None:
                    od_pct = pct

            return od_pct, tp_pct, net_pct, tp_lte3, tp_gte4, flat

        wb = Workbook()
        ws = wb.active
        ws.title = "Commission Rules"

        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(fill_type="solid", fgColor="1F5C99")
        for col_idx, col_name in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=col_name)
            cell.font = header_font
            cell.fill = header_fill

        row_idx = 2
        for product in cics_doc.get("products", []):
            product_name = product.get("product_name", "")
            sub_product = product.get("sub_category", "")
            for rule in product.get("commission_rules", []):
                conditions = rule.get("conditions") or {}
                outcome = rule.get("outcome") or {}
                deal_param = cv(conditions.get("deal_parameter"))
                od_pct, tp_pct, net_pct, tp_lte3, tp_gte4, flat = outcome_percentages(outcome, deal_param)

                row = [
                    "",                                  # DealId
                    "",                                  # ReferenceDealId
                    deal_param,                          # DealParameter
                    deal_start,                          # DealStartDate
                    deal_end,                            # DealEndDate
                    provider_name,                       # Insurer
                    product_name,                        # Product
                    sub_product,                         # SubProduct
                    cv(conditions.get("vehicle_sub_class")),
                    cv(conditions.get("policy_type")),
                    cv(conditions.get("state")),
                    cv(conditions.get("rto")),
                    cv(conditions.get("number_of_wheels")),
                    cv(conditions.get("gvw_start_range")),
                    cv(conditions.get("gvw_end_range")),
                    cv(conditions.get("cc_start_range")),
                    cv(conditions.get("cc_end_range")),
                    cv(conditions.get("seating_capacity_start_range")),
                    cv(conditions.get("seating_capacity_end_range")),
                    cv(conditions.get("school_bus_type")),
                    cv(conditions.get("booking_mode")),
                    cv(conditions.get("zero_dep")),
                    cv_default(conditions.get("ncb"), "Yes,No"),
                    cv_default(conditions.get("cpa"), "With CPA,Without CPA"),
                    cv_default(conditions.get("fuel_type"), "Petrol,Diesel,CNG,LPG,Electric"),
                    cv_default(conditions.get("vehicle_age"), "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15"),
                    cv(conditions.get("vehicle_make")),
                    cv(conditions.get("vehicle_model")),
                    cv(conditions.get("vehicle_carrier")),
                    cv(conditions.get("business_type")),
                    cv(conditions.get("od_term")),
                    cv(conditions.get("tp_term")),
                    od_pct, tp_pct, net_pct, tp_lte3, tp_gte4, flat,
                ]
                for col_idx, val in enumerate(row, start=1):
                    ws.cell(row=row_idx, column=col_idx, value=val)
                row_idx += 1

        for col_idx in range(1, len(headers) + 1):
            col_letter = get_column_letter(col_idx)
            max_len = max(
                len(str(ws.cell(row=r, column=col_idx).value or ""))
                for r in range(1, row_idx)
            )
            ws.column_dimensions[col_letter].width = max_len + 4

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        base_name = instance.original_filename.rsplit(".", 1)[0]
        filename = f"{base_name}_cics.xlsx"
        response = FileResponse(buf, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
