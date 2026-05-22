import secrets
import time

from django.conf import settings
from django.db import models

from config.abstract_models import TimeStampedUUIDModel


def _generate_pipeline_run_id():
    ts = int(time.time() * 1000)
    suffix = secrets.token_hex(2)
    return f"prun_{ts}_{suffix}"


class PipelineRun(TimeStampedUUIDModel):
    class Status(models.TextChoices):
        INITIATED = "initiated", "Initiated"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    pipeline_run_id = models.CharField(
        max_length=48,
        unique=True,
        default=_generate_pipeline_run_id,
        editable=False,
        db_index=True,
    )
    pipeline = models.ForeignKey(
        "pipelines.Pipeline",
        on_delete=models.PROTECT,
        related_name="runs",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.INITIATED,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pipeline_runs",
    )
    original_filename = models.CharField(max_length=255, blank=True, default="")
    source_url = models.CharField(max_length=1024, null=True, blank=True)
    inputs = models.JSONField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_by", "-created_at"]),
            models.Index(fields=["pipeline_run_id"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.pipeline_run_id} ({self.status})"
