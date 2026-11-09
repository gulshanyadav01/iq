import secrets
import time

from django.db import models
from config.abstract_models import TimeStampedUUIDModel


def _generate_policy_extraction_id():
    ts = int(time.time() * 1000)
    suffix = secrets.token_hex(2)
    return f"pext_{ts}_{suffix}"


class PolicyExtractionRequest(TimeStampedUUIDModel):
    class Status(models.TextChoices):
        INITIATED = "initiated", "Initiated"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class ReviewStatus(models.TextChoices):
        # Set once a row reaches `completed` (null for every non-completed row).
        # The confidence gate (tasks._compute_review_status) decides between
        # AUTO_APPROVED and NEEDS_REVIEW; a human sign-off via the `review` action
        # moves a NEEDS_REVIEW row to APPROVED. "No silent issuance": a low-confidence
        # extraction is never auto-approved.
        AUTO_APPROVED = "auto_approved", "Auto-approved"
        NEEDS_REVIEW = "needs_review", "Needs review"
        APPROVED = "approved", "Approved"

    class ErrorCategory(models.TextChoices):
        # Bucket a failure is classified into (see services/error_classification.py).
        # Null for every non-failed row; set at failure time and on backfill.
        EXPIRED_LINK = "expired_link", "Expired / invalid link"
        UNSUPPORTED_FILE = "unsupported_file", "Unsupported file type"
        MISSING_SOURCE = "missing_source", "Missing source"
        TIMEOUT = "timeout", "Timed out"
        DOWNLOAD_FAILED = "download_failed", "Download failed"
        PARSER_FAILED = "parser_failed", "Extraction failed"
        OTHER = "other", "Other"

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.INITIATED, db_index=True
    )
    policy_extraction_id = models.CharField(
        max_length=32,
        unique=True,
        default=_generate_policy_extraction_id,
        editable=False,
        db_index=True,
    )
    original_filename = models.CharField(max_length=255)
    source_url = models.URLField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    error_category = models.CharField(
        max_length=32,
        choices=ErrorCategory.choices,
        null=True,
        blank=True,
        db_index=True,
    )
    callback_response = models.JSONField(null=True, blank=True)
    # Human-in-the-loop review gate. Null until the row completes; then set by the
    # confidence gate and, on human sign-off, advanced to APPROVED (see ReviewStatus).
    review_status = models.CharField(
        max_length=20,
        choices=ReviewStatus.choices,
        null=True,
        blank=True,
        db_index=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    # MinerU output checkpoint for the split pipeline (POLICY_EXTRACT_SPLIT_LLM):
    # _run_ocr writes it, _run_llm_map reads it, so a redelivery or an LLM-stage
    # failure never re-burns the GPU work. Empty on the legacy _run_parser path.
    ocr_markdown = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Back the columns the list endpoint's (unauthenticated) `ordering`
            # param can sort by, so a filename/updated sort can't force a
            # full-table filesort. created_at and status are already indexed;
            # the trailing -created_at, id match the query's stable tiebreaker.
            models.Index(
                fields=["original_filename", "-created_at", "id"],
                name="pext_filename_created_idx",
            ),
            models.Index(
                fields=["updated_at", "-created_at", "id"],
                name="pext_updated_created_idx",
            ),
        ]

    def __str__(self):
        return f"{self.original_filename} ({self.status})"
