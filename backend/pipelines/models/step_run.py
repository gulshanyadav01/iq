from django.db import models

from config.abstract_models import TimeStampedUUIDModel


class StepRun(TimeStampedUUIDModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        SKIPPED_CACHED = "skipped_cached", "Skipped (cached)"

    pipeline_run = models.ForeignKey(
        "pipelines.PipelineRun",
        on_delete=models.CASCADE,
        related_name="step_runs",
    )
    pipeline_step = models.ForeignKey(
        "pipelines.PipelineStep",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="step_runs",
    )
    order = models.PositiveSmallIntegerField()
    step_type = models.CharField(max_length=32, db_index=True)
    config_snapshot = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    inputs = models.JSONField(null=True, blank=True)
    output = models.JSONField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["pipeline_run", "order"]
        indexes = [
            models.Index(fields=["pipeline_run", "order"]),
        ]

    def __str__(self):
        return f"{self.pipeline_run.pipeline_run_id} step#{self.order} ({self.step_type}, {self.status})"
