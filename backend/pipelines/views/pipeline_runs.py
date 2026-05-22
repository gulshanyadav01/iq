from __future__ import annotations

import logging
import re

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import StreamingHttpResponse, Http404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from pipelines.models import Pipeline, PipelineRun, StepRun
from pipelines.serializers import (
    PipelineRunCreateSerializer,
    PipelineRunSerializer,
)
from pipelines.services.snapshot import merge_config
from pipelines.tasks import enqueue_pipeline_run

logger = logging.getLogger(__name__)


# F7: whitelist artifact_kind → StepRun.output key. Path traversal is impossible
# because the key is resolved from DB, not from the URL.
ARTIFACT_KIND_TO_OUTPUT_KEY = {
    "pdf": None,  # special: read from PipelineRun.inputs.pdf_key
    "md": "md_key",
    "blocks": "blocks_key",
    "llm_raw": "llm_raw_key",
    "llm_extracted": "llm_extracted_key",
}


class PipelineRunsViewSet(viewsets.ModelViewSet):
    """Pipeline runs (list, retrieve, create) for the authenticated user.

    POST /api/pipelines/runs/ accepts multipart upload (fresh run) OR a JSON
    body with `parent_run_id` for live prompt diff (cache short-circuit per F3).
    """

    permission_classes = [IsAuthenticated]
    lookup_field = "pipeline_run_id"
    lookup_value_regex = r"prun_[a-z0-9_]+"
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return PipelineRunCreateSerializer
        return PipelineRunSerializer

    def get_queryset(self):
        return (
            PipelineRun.objects.filter(created_by=self.request.user)
            .select_related("pipeline", "created_by")
            .prefetch_related("step_runs")
            .order_by("-created_at")
        )

    # ------------------------------------------------------------------
    # CREATE
    # ------------------------------------------------------------------

    def create(self, request, *args, **kwargs):
        serializer = PipelineRunCreateSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        pipeline = Pipeline.objects.prefetch_related("steps").get(
            slug=data["pipeline_slug"]
        )
        parent_run_id = data.get("parent_run_id") or ""
        config_overrides = data.get("config_overrides") or {}
        upload = data.get("file")

        # --- Determine PDF storage key ------------------------------------
        if upload:
            pdf_key, original_filename = self._save_upload(upload, request.user)
        else:
            # parent_run_id flow — reuse parent's PDF
            parent = PipelineRun.objects.get(
                pipeline_run_id=parent_run_id, created_by=request.user
            )
            parent_inputs = parent.inputs or {}
            pdf_key = parent_inputs.get("pdf_key")
            original_filename = parent.original_filename
            if not pdf_key:
                return Response(
                    {"detail": "parent_run_id has no associated PDF."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # --- Create PipelineRun + ordered StepRuns ------------------------
        run = PipelineRun.objects.create(
            pipeline=pipeline,
            created_by=request.user,
            original_filename=original_filename,
            source_url=pdf_key,
            inputs={
                "pdf_key": pdf_key,
                "parent_run_id": parent_run_id or None,
                "config_overrides": config_overrides,
            },
        )

        parent_run = None
        if parent_run_id:
            parent_run = PipelineRun.objects.prefetch_related("step_runs").get(
                pipeline_run_id=parent_run_id, created_by=request.user
            )

        for step in pipeline.steps.order_by("order"):
            # Per-step override key in config_overrides is the step's order int.
            override = config_overrides.get(str(step.order)) or config_overrides.get(
                step.order
            )
            snapshot = merge_config(step.config, override)

            sr_kwargs = dict(
                pipeline_run=run,
                pipeline_step=step,
                order=step.order,
                step_type=step.step_type,
                config_snapshot=snapshot,
                status=StepRun.Status.PENDING,
            )

            # F3: PDF_TO_MD with a valid parent → SKIPPED_CACHED reference
            if (
                parent_run is not None
                and step.step_type == "PDF_TO_MD"
                and self._cache_is_valid(parent_run, pdf_key)
            ):
                parent_pdf_step = (
                    parent_run.step_runs.filter(step_type="PDF_TO_MD")
                    .order_by("order")
                    .first()
                )
                if parent_pdf_step and parent_pdf_step.output:
                    sr_kwargs["status"] = StepRun.Status.SKIPPED_CACHED
                    # Reference, not copy — but include the resolvable refs for
                    # downstream executors that read by key.
                    sr_kwargs["output"] = {
                        "source_step_run_id": str(parent_pdf_step.id),
                        "md_key": parent_pdf_step.output["md_key"],
                        "blocks_key": parent_pdf_step.output["blocks_key"],
                        "md_chars": parent_pdf_step.output.get("md_chars"),
                        "page_count": parent_pdf_step.output.get("page_count"),
                        "blocks_count": parent_pdf_step.output.get("blocks_count"),
                        "has_bbox": parent_pdf_step.output.get("has_bbox"),
                    }
                    sr_kwargs["started_at"] = run.created_at
                    sr_kwargs["finished_at"] = run.created_at

            StepRun.objects.create(**sr_kwargs)

        # --- Kick off Celery chain ----------------------------------------
        enqueue_pipeline_run(str(run.id))

        serializer_out = PipelineRunSerializer(run)
        return Response(serializer_out.data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # ARTIFACT PROXY (F7)
    # ------------------------------------------------------------------

    @extend_schema(
        summary="Download a pipeline run artifact (whitelisted kinds only).",
        parameters=[
            OpenApiParameter(
                name="kind",
                description="Artifact kind",
                required=True,
                type=str,
                enum=list(ARTIFACT_KIND_TO_OUTPUT_KEY.keys()),
            )
        ],
    )
    @action(detail=True, methods=["get"], url_path=r"artifacts/(?P<kind>[a-z_]+)")
    def artifacts(self, request, pipeline_run_id=None, kind=None):
        if kind not in ARTIFACT_KIND_TO_OUTPUT_KEY:
            return Response(
                {"detail": f"Unsupported artifact kind {kind!r}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        run = self.get_object()  # already user-scoped by get_queryset

        if kind == "pdf":
            storage_key = (run.inputs or {}).get("pdf_key")
        else:
            output_key_name = ARTIFACT_KIND_TO_OUTPUT_KEY[kind]
            storage_key = self._resolve_step_output_key(run, kind, output_key_name)

        if not storage_key:
            raise Http404(f"Artifact {kind!r} not yet available for this run.")

        try:
            fh = default_storage.open(storage_key, "rb")
        except Exception:
            raise Http404("Artifact missing in storage.")

        content_type = {
            "pdf": "application/pdf",
            "md": "text/markdown",
            "blocks": "application/json",
            "llm_raw": "application/json",
            "llm_extracted": "application/json",
        }.get(kind, "application/octet-stream")

        return StreamingHttpResponse(fh, content_type=content_type)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _save_upload(self, upload, user) -> tuple[str, str]:
        import uuid

        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", upload.name)[:200] or "upload.pdf"
        storage_key = f"pipelines/uploads/{uuid.uuid4()}/{safe_name}"
        default_storage.save(storage_key, ContentFile(upload.read()))
        return storage_key, upload.name

    def _cache_is_valid(self, parent_run: PipelineRun, pdf_key: str) -> bool:
        """F3 4-check validation. Caller already verified ownership + completed."""
        # Same PDF reference
        parent_pdf_key = (parent_run.inputs or {}).get("pdf_key")
        if not parent_pdf_key or parent_pdf_key != pdf_key:
            return False
        parent_pdf_step = (
            parent_run.step_runs.filter(step_type="PDF_TO_MD")
            .order_by("order")
            .first()
        )
        if not parent_pdf_step or parent_pdf_step.status != StepRun.Status.COMPLETED:
            return False
        if not parent_pdf_step.output:
            return False
        md_key = parent_pdf_step.output.get("md_key")
        blocks_key = parent_pdf_step.output.get("blocks_key")
        if not md_key or not blocks_key:
            return False
        # Confirm MinIO objects still present
        try:
            if not default_storage.exists(md_key):
                return False
            if not default_storage.exists(blocks_key):
                return False
        except Exception:
            return False
        return True

    def _resolve_step_output_key(
        self, run: PipelineRun, kind: str, output_key_name: str
    ) -> str | None:
        # Match by step type implied by the artifact kind.
        step_type = {
            "md": "PDF_TO_MD",
            "blocks": "PDF_TO_MD",
            "llm_raw": "LLM_EXTRACT",
            "llm_extracted": "LLM_EXTRACT",
        }.get(kind)
        if not step_type:
            return None
        step_run = (
            run.step_runs.filter(step_type=step_type)
            .exclude(output__isnull=True)
            .order_by("-order")
            .first()
        )
        if not step_run or not step_run.output:
            return None
        return step_run.output.get(output_key_name)
