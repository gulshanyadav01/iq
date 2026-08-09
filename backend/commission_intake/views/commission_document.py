import io
import json
import re

from django.db.models import Count, Q
from django.db.models.fields.json import KeyTextTransform
from django.http import FileResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cins.processors.core.cpa_split import applied_on_to_column, derive_applied_on
from cins.processors.core.sheet_visibility import (
    hidden_sheet_names,
    skip_hidden_sheets_enabled,
)
from commission_intake.models import CommissionDocument


def split_other_conditions(text):
    """Split a free-text other_conditions blob into individual clauses.

    Source clauses arrive as one long line concatenated from notes tables
    (e.g. "Please note that ... - For X ... - For Y ..."). Split on bullet
    markers (" - ", " • ", " | ") so each clause stands on its own.
    """
    if not text:
        return []
    s = re.sub(r"\s+", " ", str(text)).strip()
    if not s:
        return []
    parts = re.split(r"\s+[-•|]\s+", s)
    return [p.strip(" -•|\t").strip() for p in parts if p and p.strip()]


def format_other_conditions(text):
    """Render the other_conditions blob as a pretty-printed JSON array.

    Excel cells render the JSON readably under wrap_text — one clause per
    line, quoted, with clear array boundaries. Downstream consumers (deal
    management, audit tooling) can also json.loads() the cell value to get
    a structured list of clauses without re-parsing free text.
    """
    clauses = split_other_conditions(text)
    if not clauses:
        return ""
    return json.dumps(clauses, ensure_ascii=False, indent=2)


def format_qualifying_clauses(clauses):
    """Render qualifying_clauses (list of dicts) as pretty-printed JSON.

    Pass 3 LLM emits a structured list of {clause_type, text, when?, effect?}
    that the downstream booking pipeline ingests programmatically. The export
    cell preserves that exact JSON so analysts and downstream systems read
    the same shape.
    """
    if not clauses:
        return ""
    if not isinstance(clauses, list):
        return ""
    return json.dumps(clauses, ensure_ascii=False, indent=2)


def _clause_key(clause):
    """Stable hashable key for clause / text dedup across rules."""
    if isinstance(clause, (dict, list)):
        return json.dumps(clause, sort_keys=True, ensure_ascii=False)
    return str(clause or "")


def partition_global_modifiers(cics_doc, min_shared=2):
    """Walk every rule once. Return (global_other_conditions, global_clauses).

    A modifier is "global" when it is shared across multiple rules
    (default ≥ 2 occurrences). Documents commonly mix products where
    Chola-style footnotes hit every Private Car rule but no Two Wheeler
    rule, so the strict "appears on every rule" rule misses the obvious
    deduplication. Threshold ≥ 2 promotes anything repeated; row-specific
    clauses (single occurrence) stay inline.

    Args:
      cics_doc: parsed CICS document dict.
      min_shared: minimum row count for a modifier to be considered global.

    Returns:
      - set[str] of other_conditions text values that are shared
      - set[str] of clause keys (json.dumps with sort_keys) that are shared
    """
    from collections import Counter

    other_counts = Counter()
    clause_counts = Counter()

    for product in cics_doc.get("products", []) or []:
        for rule in product.get("commission_rules", []) or []:
            outcome = rule.get("outcome") or {}
            other = outcome.get("other_conditions")
            if other:
                other_counts[str(other)] += 1
            for clause in outcome.get("qualifying_clauses") or []:
                clause_counts[_clause_key(clause)] += 1

    global_other = {text for text, n in other_counts.items() if n >= min_shared}
    global_clauses = {key for key, n in clause_counts.items() if n >= min_shared}
    return global_other, global_clauses


def _write_text_cell(ws, row, column, value):
    """Write a string cell that Excel will never evaluate as a formula.

    Sheet names come from an uploaded workbook and classification labels come
    from an LLM, so both are untrusted. openpyxl types any string starting with
    "=" as TYPE_FORMULA, which would make a tab named `=1+1` a live formula in
    a workbook an analyst opens on their desktop. Pinning data_type to "s"
    keeps the displayed text identical while making it inert.
    """
    cell = ws.cell(row=row, column=column, value="" if value is None else str(value))
    cell.data_type = "s"
    return cell


def _write_coverage_sheet(wb, document, header_font, header_fill):
    """Append a "Sheet Coverage" worksheet describing which source sheets fed
    the export.

    Reconciliation is done in Excel, away from the app, so the coverage notice
    on the detail page is invisible exactly when someone is asking "why is this
    row missing". Three outcomes are distinguished, because one word would
    conflate them: *Extracted* (fed the extraction passes), *Reference only*
    (informed the passes but emits no rules of its own), and *Skipped — hidden*.

    The extracted set comes from `derive_pipeline_routing`, the same helper the
    pipeline routes on — not from the classification labels. Those two are not
    interchangeable: when any commission sheet exists, routing processes ONLY
    commission sheets, so a visible unclassified sheet is not extracted; when
    none exists, routing falls back to every visible sheet, so master sheets
    ARE extracted. Deriving the fate from labels would over-report coverage in
    the first case and under-report it in the second, which is precisely the
    false confidence this sheet exists to prevent.

    No-ops for documents with no indexed sheets.
    """
    from commission_intake.models import IndexedContent
    from cins.processors.core.pipeline import derive_pipeline_routing
    from cins.processors.core.sheet_visibility import hidden_sheet_names, sheet_state

    indexed = IndexedContent.objects.filter(document=document).first()
    if not indexed or not indexed.sheet_contents:
        return

    sheet_metadata = indexed.sheet_metadata or {}
    classifications = sheet_metadata.get("classifications") or {}
    source_meta = sheet_metadata.get("source_metadata") or {}
    sheet_names = list(indexed.sheet_contents.keys())

    routed, _attachment = derive_pipeline_routing(
        source_type=indexed.source_type,
        sheet_classifications=classifications,
        sheet_names=sheet_names,
        excel_attachments=source_meta.get("excel_attachments", []),
    )
    routed_set = set(routed)
    hidden = hidden_sheet_names(classifications)
    hidden_set = set(hidden)

    cws = wb.create_sheet(title="Sheet Coverage")
    coverage_headers = ["Sheet", "Classification", "TabState", "UsedFor"]
    for col_idx, col_name in enumerate(coverage_headers, start=1):
        cell = cws.cell(row=1, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill

    row = 2
    for name in sheet_names:
        info = classifications.get(name) or {}
        if name in hidden_set:
            used_for = "Skipped — hidden sheet"
        elif name in routed_set:
            used_for = "Extracted"
        elif (info.get("sheet_type")) == "master_data":
            used_for = "Reference only (master data)"
        else:
            used_for = "Not extracted (unclassified)"
        _write_text_cell(cws, row, 1, name)
        _write_text_cell(cws, row, 2, info.get("sheet_type") or "unknown")
        _write_text_cell(cws, row, 3, sheet_state(info))
        _write_text_cell(cws, row, 4, used_for)
        row += 1

    summary_row = row + 1
    extracted_count = len([n for n in sheet_names if n in routed_set])
    reference_count = len(sheet_names) - extracted_count - len(hidden_set)
    cws.cell(row=summary_row, column=1, value="Summary").font = header_font
    # "not extracted" rather than "used as reference only": the count also
    # includes visible unclassified sheets, which routing skips whenever any
    # commission sheet exists. The per-row labels above say the same thing.
    _write_text_cell(
        cws,
        summary_row,
        2,
        f"{extracted_count} of {len(sheet_names)} sheet(s) extracted"
        + (f"; {len(hidden)} hidden sheet(s) skipped" if hidden else "")
        + (f"; {reference_count} not extracted" if reference_count else ""),
    )

    cws.column_dimensions["A"].width = 40
    cws.column_dimensions["B"].width = 20
    cws.column_dimensions["C"].width = 14
    cws.column_dimensions["D"].width = 34


from commission_intake.serializers import (  # noqa: E402
    CommissionDocumentCreateSerializer,
    CommissionDocumentListSerializer,
    CommissionDocumentSerializer,
    ExtractRequestSerializer,
    ProcessingModeRequestSerializer,
    RerunPassRequestSerializer,
)
from commission_intake.tasks import (  # noqa: E402
    enqueue_processing_chain,
    rerun_single_pass,
    rerun_transform,
    run_validation,
)


# Real model columns loaded for the lean list query. Kept explicit (not
# `*ListSerializer.Meta.fields`) because that fields list now includes the
# non-column `applicable_period` SerializerMethodField — passing it to `.only()`
# would raise FieldError. Drift between the two is intentionally visible here.
_LIST_MODEL_FIELDS = (
    "id",
    "status",
    "processing_mode",
    "original_filename",
    "source_type",
    "provider",
    "llm_provider",
    "llm_model",
    "excel_mode",
    "error",
    "index_progress",
    "is_validated",
    "validated_at",
    "created_at",
    "updated_at",
)


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
        if self.action == "list":
            return CommissionDocumentListSerializer
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
        return Response(
            CommissionDocumentSerializer(doc).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        summary="List commission documents",
        description="Returns all documents (newest first) with current processing status. Excludes heavy nested extraction/indexing payloads — fetch the detail endpoint for those.",
        tags=["Commission Document"],
        responses={200: CommissionDocumentListSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        cd = "cics_result__cics_doc"

        def _p(key):
            # cics_doc -> "commission_period" -> {key}, extracted at the DB layer
            # over the reverse-O2O LEFT JOIN. NULL when the row/key is absent.
            return KeyTextTransform(key, KeyTextTransform("commission_period", cd))

        queryset = (
            self.get_queryset()
            .only(*_LIST_MODEL_FIELDS)
            .annotate(
                _period_label=_p("period_label"),
                _period_effective_from=_p("effective_from"),
                _period_effective_to=_p("effective_to"),
            )
            .order_by("-created_at")
        )
        serializer = CommissionDocumentListSerializer(queryset, many=True)
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
        mode = (
            req_serializer.validated_data.get("processing_mode")
            or instance.processing_mode
        )
        instance.status = CommissionDocument.Status.INITIATED
        instance.error = None
        instance.processing_mode = mode
        instance.is_validated = False
        instance.validated_at = None
        instance.save(
            update_fields=[
                "status",
                "error",
                "processing_mode",
                "is_validated",
                "validated_at",
                "updated_at",
            ]
        )
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
        in_flight = (
            CommissionDocument.Status.INDEXING,
            CommissionDocument.Status.EXTRACTING,
        )
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
        instance.save(
            update_fields=[
                "status",
                "error",
                "processing_mode",
                "is_validated",
                "validated_at",
                "updated_at",
            ]
        )
        enqueue_processing_chain(str(instance.id), mode="index_only")
        return Response(CommissionDocumentSerializer(instance).data)

    @extend_schema(
        summary="Re-run extraction",
        description=(
            "Requires document to be indexed. Runs LLM extraction passes. "
            "Pass force_refresh=true to bypass the CICS output cache and re-run "
            "the LLM; a gate-passing result replaces the cached output."
        ),
        tags=["Commission Document"],
        request=ExtractRequestSerializer,
        responses={200: CommissionDocumentSerializer},
    )
    @action(detail=True, methods=["post"], url_path="extract")
    def extract(self, request, *args, **kwargs):
        from commission_intake.models import IndexedContent

        instance = self.get_object()
        in_flight = (
            CommissionDocument.Status.INDEXING,
            CommissionDocument.Status.EXTRACTING,
        )
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
        request_serializer = ExtractRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        force_refresh = request_serializer.validated_data.get("force_refresh", False)
        instance.status = CommissionDocument.Status.INITIATED
        instance.error = None
        instance.processing_mode = CommissionDocument.ProcessingMode.EXTRACT_ONLY
        instance.is_validated = False
        instance.validated_at = None
        instance.save(
            update_fields=[
                "status",
                "error",
                "processing_mode",
                "is_validated",
                "validated_at",
                "updated_at",
            ]
        )
        enqueue_processing_chain(
            str(instance.id), mode="extract_only", force_refresh=force_refresh
        )
        return Response(CommissionDocumentSerializer(instance).data)

    @extend_schema(
        summary="Re-run single extraction pass",
        description=(
            "Re-runs one of pass 1/2/3 for a specific sheet/attachment. "
            "Rejects sheet names outside the document's extraction set — hidden "
            "worksheets and sheets the pipeline does not route are not re-runnable."
        ),
        tags=["Commission Document"],
        request=RerunPassRequestSerializer,
        responses={
            200: CommissionDocumentSerializer,
            400: OpenApiResponse(
                description=(
                    "Document is not indexed, or the sheet is not part of its "
                    "extraction set."
                )
            ),
            409: OpenApiResponse(description="Document is currently being processed."),
        },
    )
    @action(detail=True, methods=["post"], url_path="rerun-pass")
    def rerun_pass(self, request, *args, **kwargs):
        from commission_intake.models import IndexedContent, ExtractionPass

        instance = self.get_object()
        in_flight = (
            CommissionDocument.Status.INDEXING,
            CommissionDocument.Status.EXTRACTING,
        )
        if instance.status in in_flight:
            return Response(
                {"detail": "Document is currently being processed."},
                status=status.HTTP_409_CONFLICT,
            )
        # One fetch, reused by the routed-set guard below.
        indexed = IndexedContent.objects.filter(document=instance).first()
        if indexed is None:
            return Response(
                {"detail": "Document must be indexed before a pass can be re-run."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        req = RerunPassRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        pass_number = req.validated_data["pass_number"]
        sheet_name = req.validated_data.get("sheet_name", "")
        attachment_name = req.validated_data.get("attachment_name", "")

        # The sheet name arrives from the client, and the UI now lists sheets
        # that were deliberately excluded from extraction. Without this check a
        # POST naming a hidden sheet would run a full three-pass extraction of
        # content the pipeline is designed never to touch.
        #
        # Gated on the rollout flag: CINS_SKIP_HIDDEN_SHEETS=false is documented
        # as restoring pre-change behaviour without a redeploy, so it has to
        # lift this rejection too, not just the routing filter.
        if sheet_name and indexed.sheet_contents and skip_hidden_sheets_enabled():
            from cins.processors.core.pipeline import derive_pipeline_routing

            sheet_metadata = indexed.sheet_metadata or {}
            source_meta = sheet_metadata.get("source_metadata", {})
            classifications = sheet_metadata.get("classifications", {})
            routed, _ = derive_pipeline_routing(
                source_type=indexed.source_type,
                sheet_classifications=classifications,
                sheet_names=list(indexed.sheet_contents.keys()),
                excel_attachments=source_meta.get("excel_attachments", []),
            )
            if sheet_name not in routed:
                # Name the actual reason. A visible master_data sheet is also
                # outside the routed set, and telling its owner "hidden sheets
                # are excluded" sends them hunting for a hidden tab that is not
                # there.
                if sheet_name in set(hidden_sheet_names(classifications)):
                    reason = "its Excel tab is hidden"
                else:
                    reason = (
                        f"it is classified "
                        f"'{(classifications.get(sheet_name) or {}).get('sheet_type') or 'unclassified'}'"
                        f" and the pipeline only extracts commission sheets"
                    )
                return Response(
                    {
                        "detail": (
                            f"Sheet '{sheet_name}' is not part of this document's "
                            f"extraction set — {reason}."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

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
        in_flight = (
            CommissionDocument.Status.INDEXING,
            CommissionDocument.Status.EXTRACTING,
        )
        if instance.status in in_flight:
            return Response(
                {"detail": "Document is currently being processed."},
                status=status.HTTP_409_CONFLICT,
            )
        if not ExtractionPass.objects.filter(
            document=instance, status=ExtractionPass.Status.COMPLETED
        ).exists():
            return Response(
                {
                    "detail": "No completed extraction passes found. Run extraction first."
                },
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
        in_flight = (
            CommissionDocument.Status.INDEXING,
            CommissionDocument.Status.EXTRACTING,
        )
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
        return Response(
            {
                "document_stats": document_stats,
                "provider_stats": list(provider_stats),
            }
        )

    @extend_schema(exclude=True)
    @action(detail=True, methods=["get"], url_path="export-excel")
    def export_excel(self, request, *args, **kwargs):
        """Export flattened CICS products/rules as an Excel workbook matching the grid dump format."""
        from commission_intake.models import CICSResult

        instance = self.get_object()
        if not CICSResult.objects.filter(document=instance).exists():
            return Response(
                {"detail": "No CICS result available."},
                status=status.HTTP_404_NOT_FOUND,
            )

        cics_doc = instance.cics_result.cics_doc or {}
        provider_name = (cics_doc.get("provider") or {}).get("provider_name", "")
        period = cics_doc.get("commission_period") or {}
        deal_start = period.get("effective_from", "")
        deal_end = period.get("effective_to", "")

        headers = [
            "DealId",
            "ReferenceDealId",
            "DealParameter",
            "DealStartDate",
            "DealEndDate",
            "Insurer",
            "Product",
            "SubProduct",
            "VehicleSubClass",
            "PolicyType",
            "State",
            "RTO",
            "NumberOfWheels",
            "GVWStartRange",
            "GVWEndRange",
            "CCStartRange",
            "CCEndRange",
            "SeatingCapacityStartRange",
            "SeatingCapacityEndRange",
            "PoliciesCountStartRange",
            "PoliciesCountEndRange",
            "SchoolBusType",
            "BookingMode",
            "ZeroDep",
            "NCB",
            "CPA",
            "FuelType",
            "VehicleAge",
            "VehicleMake",
            "VehicleModel",
            "VehicleCarrier",
            "BusinessType",
            "ODTerm",
            "TPTerm",
            "ODPercentage",
            "TPPercentage",
            "NetPercentage",
            "TPLessThanEqual3YearPercentage",
            "TPGreaterThanEqual4YearPercentage",
            "FlatAmount",
            "OtherConditions",
            "QualifyingClauses",
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

        def render_make_model_cell(include, exclude):
            """Render a VehicleMake / VehicleModel cell with inline exclusion.

            An exclusion list renders as ``exclude(A, B, C)`` inside the
            existing column — no new columns, so index-based importers are
            unaffected. An include list renders exactly as ``cv`` does
            (unchanged). Both should never be set from the bucket fan-out; if
            they are, prefer the include list defensively.
            """
            if include:
                return cv(include)
            if exclude:
                vals = exclude if isinstance(exclude, list) else [exclude]
                return "exclude(" + ", ".join(str(i) for i in vals) + ")"
            return ""

        def outcome_percentages(outcome, deal_param, conditions=None):
            """
            Map a single CommissionOutcome to the 6 commission columns.

            A rule carries exactly one percentage. Its column is derived from the
            rule's applied_on basis (resolved via derive_applied_on, which honors
            coverage-conditional applied_on_override clauses) — applied_on is
            authoritative over deal_parameter. When applied_on does not name a
            known basis, fall back to deal_parameter routing.
            """
            od_pct = tp_pct = net_pct = tp_lte3 = tp_gte4 = flat = ""
            if not outcome:
                return od_pct, tp_pct, net_pct, tp_lte3, tp_gte4, flat

            pct = outcome.get("percentage_value")
            flat_val = outcome.get("flat_value")
            c_type = outcome.get("commission_type", "")

            params = [
                p.strip().lower() for p in (deal_param or "").split(",") if p.strip()
            ]

            # applied_on-derived column wins (Option B). Flat outcomes still go to
            # the flat column regardless of basis.
            column = applied_on_to_column(derive_applied_on(conditions or {}, outcome))

            if c_type == "flat" and flat_val is not None:
                flat = flat_val
            elif column == "od":
                od_pct = pct if pct is not None else ""
            elif column == "tp":
                tp_pct = pct if pct is not None else ""
            elif column == "net":
                net_pct = pct if pct is not None else ""
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
                        thresh = tier.get("threshold") or {}
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

        # Detect modifiers that apply to every rule — hoist onto a dedicated
        # "Global Rules" worksheet so we don't repeat the same JSON blob on
        # hundreds of rows. Per-row cells show only the row-specific subset.
        global_other_set, global_clause_keys = partition_global_modifiers(cics_doc)

        # CPA-deduction rules are already bifurcated into With/Without-CPA rows
        # at transform time (cins.processors.core.cpa_split.materialize_cpa_split),
        # so the export is a pure renderer: one row per rule.
        _CPA_LABELS = {"with-cpa": "With CPA", "without-cpa": "Without CPA"}

        # CPA-deduction rules are already bifurcated into With/Without-CPA rows
        # at transform time (cins.processors.core.cpa_split.materialize_cpa_split),
        # so the export is a pure renderer: one row per rule.
        _CPA_LABELS = {"with-cpa": "With CPA", "without-cpa": "Without CPA"}

        # RTO fill (Fix v): when a rule names a state but no explicit RTO, fill
        # the export RTO cell with the product-active RTOs for that state. Built
        # once; degrades to the raw (empty) value if the master is unavailable.
        try:
            from master_data.services.resolver import MasterDataResolver

            _rto_resolver = MasterDataResolver()
        except Exception:
            _rto_resolver = None

        def _product_bucket(name):
            """Normalise an export product_name to the {CAR,CV,TW} RTO bucket."""
            n = (name or "").lower()
            if any(k in n for k in ("commercial", "gcv", "pcv", "goods", "bus")):
                return "CV"
            if any(k in n for k in ("two", "2w", "wheeler")) and "four" not in n:
                return "TW"
            if any(k in n for k in ("car", "private", "taxi")):
                return "CAR"
            return None

        # Export complement fill (1A): a UW-zone grid can carry a specific
        # zone rule ("UP- East" -> explicit rto=[UP28..]) alongside a plain
        # state rule ("UP" with no rto). Filling the plain rule's RTO cell
        # from the full state RTO list would then render every UP RTO code,
        # including the ones the sibling zone rule already claims explicitly
        # -- e.g. the NCR zone's UP14/UP16 -- producing overlapping cells.
        # Pre-pass collects, per product bucket, every RTO code any rule in
        # this document claims via an explicit conditions.rto, so rto_cell
        # can subtract the sibling-claimed set from a generic state-derived
        # fill ("UP" renders as UP-minus-East). General mechanism, not
        # UP-special-cased -- applies to any carrier/zone with the same
        # explicit-zone-rule + plain-state-rule shape.
        _claimed_rto_by_bucket: dict = {}
        for _product in cics_doc.get("products", []):
            _bucket = _product_bucket(_product.get("product_name", ""))
            if _bucket is None:
                continue
            _claimed = _claimed_rto_by_bucket.setdefault(_bucket, set())
            for _rule in _product.get("commission_rules", []):
                _explicit_rto = (_rule.get("conditions") or {}).get("rto")
                if not _explicit_rto:
                    continue
                _vals = (
                    _explicit_rto
                    if isinstance(_explicit_rto, list)
                    else [_explicit_rto]
                )
                _claimed.update(str(v) for v in _vals if v)

        def rto_cell(conditions, product_name):
            """RTO column value: explicit rto if present, else state-derived
            fill minus any RTO codes a sibling rule already claims explicitly
            (1A)."""
            rto = conditions.get("rto")
            if rto:  # present and non-empty -> honor explicit value
                return cv(rto)
            states = conditions.get("state")
            if not states or _rto_resolver is None:
                return cv(rto)
            bucket = _product_bucket(product_name)
            if bucket is None:
                return cv(rto)
            if not isinstance(states, list):
                states = [states]
            claimed = _claimed_rto_by_bucket.get(bucket)
            return ",".join(
                _rto_resolver.rtos_for_states(states, bucket, exclude_codes=claimed)
            )

        row_idx = 2
        for product in cics_doc.get("products", []):
            product_name = product.get("product_name", "")
            sub_product = product.get("sub_category", "")
            for rule in product.get("commission_rules", []):
                conditions = rule.get("conditions") or {}
                outcome = rule.get("outcome") or {}
                deal_param = cv(conditions.get("deal_parameter"))
                od_pct, tp_pct, net_pct, tp_lte3, tp_gte4, flat = outcome_percentages(
                    outcome, deal_param, conditions
                )
                cpa_val = conditions.get("cpa")
                cpa_label = _CPA_LABELS.get(
                    cpa_val, cv_default(cpa_val, "With CPA,Without CPA")
                )

                row = [
                    "",  # DealId
                    "",  # ReferenceDealId
                    deal_param,  # DealParameter
                    deal_start,  # DealStartDate
                    deal_end,  # DealEndDate
                    provider_name,  # Insurer
                    product_name,  # Product
                    sub_product,  # SubProduct
                    cv(conditions.get("vehicle_sub_class")),
                    cv(conditions.get("policy_type")),
                    cv(conditions.get("state")),
                    rto_cell(conditions, product_name),
                    cv(conditions.get("number_of_wheels")),
                    cv(conditions.get("gvw_start_range")),
                    cv(conditions.get("gvw_end_range")),
                    cv(conditions.get("cc_start_range")),
                    cv(conditions.get("cc_end_range")),
                    cv(conditions.get("seating_capacity_start_range")),
                    cv(conditions.get("seating_capacity_end_range")),
                    cv(conditions.get("policies_count_start_range")),
                    cv(conditions.get("policies_count_end_range")),
                    cv(conditions.get("school_bus_type")),
                    cv(conditions.get("booking_mode")),
                    cv(conditions.get("zero_dep")),
                    cv_default(conditions.get("ncb"), "Yes,No"),
                    cpa_label,  # CPA
                    cv_default(
                        conditions.get("fuel_type"), "Petrol,Diesel,CNG,LPG,Electric"
                    ),
                    cv_default(
                        conditions.get("vehicle_age"),
                        "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15",
                    ),
                    render_make_model_cell(
                        conditions.get("vehicle_make"),
                        conditions.get("vehicle_make_exclude"),
                    ),
                    render_make_model_cell(
                        conditions.get("vehicle_model"),
                        conditions.get("vehicle_model_exclude"),
                    ),
                    cv(conditions.get("vehicle_carrier")),
                    cv(conditions.get("business_type")),
                    cv(conditions.get("od_term")),
                    cv(conditions.get("tp_term")),
                    od_pct,
                    tp_pct,
                    net_pct,
                    tp_lte3,
                    tp_gte4,
                    flat,
                    format_other_conditions(
                        outcome.get("other_conditions")
                        if outcome.get("other_conditions") not in global_other_set
                        else ""
                    ),
                    format_qualifying_clauses(
                        [
                            c
                            for c in (outcome.get("qualifying_clauses") or [])
                            if _clause_key(c) not in global_clause_keys
                        ]
                    ),
                ]
                for col_idx, val in enumerate(row, start=1):
                    ws.cell(row=row_idx, column=col_idx, value=val)
                row_idx += 1

        json_col_indices = {
            headers.index("OtherConditions") + 1,
            headers.index("QualifyingClauses") + 1,
        }
        wrap_alignment = Alignment(wrap_text=True, vertical="top")
        for r in range(2, row_idx):
            for col_idx in json_col_indices:
                ws.cell(row=r, column=col_idx).alignment = wrap_alignment

        for col_idx in range(1, len(headers) + 1):
            col_letter = get_column_letter(col_idx)
            if col_idx in json_col_indices:
                # JSON-formatted cell — fixed width keeps the row height
                # readable instead of stretching out for the longest clause.
                ws.column_dimensions[col_letter].width = 60
                continue
            max_len = max(
                len(str(ws.cell(row=r, column=col_idx).value or ""))
                for r in range(1, row_idx)
            )
            ws.column_dimensions[col_letter].width = max_len + 4

        # ── Global Rules sheet ──────────────────────────────────────────
        # Hoist modifiers that apply to every rule onto their own sheet.
        # Cleaner than repeating the same JSON blob on hundreds of rows.
        if global_other_set or global_clause_keys:
            gws = wb.create_sheet(title="Global Rules")
            global_headers = ["Type", "ClauseType", "Text", "When", "Effect"]
            for col_idx, col_name in enumerate(global_headers, start=1):
                cell = gws.cell(row=1, column=col_idx, value=col_name)
                cell.font = header_font
                cell.fill = header_fill

            g_row = 2
            for text in sorted(global_other_set):
                gws.cell(row=g_row, column=1, value="other_conditions")
                gws.cell(row=g_row, column=3, value=format_other_conditions(text))
                g_row += 1

            # Re-materialise clauses from their stable keys so the sheet
            # shows the full structured payload.
            seen_keys = set()
            for product in cics_doc.get("products", []) or []:
                for rule in product.get("commission_rules", []) or []:
                    for clause in (rule.get("outcome") or {}).get(
                        "qualifying_clauses"
                    ) or []:
                        key = _clause_key(clause)
                        if key not in global_clause_keys or key in seen_keys:
                            continue
                        seen_keys.add(key)
                        if not isinstance(clause, dict):
                            continue
                        gws.cell(row=g_row, column=1, value="qualifying_clause")
                        gws.cell(
                            row=g_row, column=2, value=clause.get("clause_type", "")
                        )
                        gws.cell(row=g_row, column=3, value=clause.get("text", ""))
                        when = clause.get("when")
                        if when:
                            gws.cell(
                                row=g_row,
                                column=4,
                                value=json.dumps(when, ensure_ascii=False, indent=2),
                            )
                        effect = clause.get("effect")
                        if effect:
                            gws.cell(
                                row=g_row,
                                column=5,
                                value=json.dumps(effect, ensure_ascii=False, indent=2),
                            )
                        g_row += 1

            for r in range(2, g_row):
                for col_idx in (3, 4, 5):
                    gws.cell(row=r, column=col_idx).alignment = wrap_alignment

            gws.column_dimensions["A"].width = 22
            gws.column_dimensions["B"].width = 24
            gws.column_dimensions["C"].width = 80
            gws.column_dimensions["D"].width = 50
            gws.column_dimensions["E"].width = 50

        # ── Sheet Coverage sheet ────────────────────────────────────────
        # Reconciliation happens in Excel, outside the app, so the coverage
        # notice on the detail page is invisible at the moment it matters most.
        # One row per source sheet: what it was classified as, and whether it
        # contributed rules, was reference-only, or was skipped.
        _write_coverage_sheet(wb, instance, header_font, header_fill)

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        base_name = instance.original_filename.rsplit(".", 1)[0]
        filename = f"{base_name}_cics.xlsx"
        response = FileResponse(
            buf,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        # The export is regenerated from live CICS data on every request. Without
        # this, the browser caches the GET by URL and serves a stale workbook
        # (e.g. a pre-split download), making re-exports look unchanged after a
        # re-extraction or a fix.
        response["Cache-Control"] = "no-store"
        return response

    @extend_schema(
        summary="Get download URL for original uploaded document",
        description="Returns a short-lived presigned URL the browser can use to download the original file.",
        tags=["Commission Document"],
    )
    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, *args, **kwargs):
        import boto3
        from botocore.config import Config
        from django.conf import settings

        instance = self.get_object()
        if not instance.file:
            return Response(
                {"detail": "No file available."}, status=status.HTTP_404_NOT_FOUND
            )

        public_endpoint = getattr(settings, "AWS_S3_PUBLIC_ENDPOINT_URL", "") or None
        addressing_style = (
            getattr(settings, "AWS_S3_ADDRESSING_STYLE", "virtual") or "virtual"
        )
        s3_client = boto3.client(
            "s3",
            endpoint_url=public_endpoint
            or getattr(settings, "AWS_S3_ENDPOINT_URL", None),
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
            region_name=settings.AWS_REGION,
            config=Config(
                signature_version="s3v4", s3={"addressing_style": addressing_style}
            ),
        )
        url = s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
                "Key": instance.file.name,
                "ResponseContentDisposition": f'attachment; filename="{instance.original_filename}"',
            },
            ExpiresIn=300,
        )
        return Response({"url": url})
