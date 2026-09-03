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
    callback_response = models.JSONField(null=True, blank=True)

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
