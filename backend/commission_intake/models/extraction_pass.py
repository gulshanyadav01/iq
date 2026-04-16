from django.db import models

from config.abstract_models import TimeStampedUUIDModel
from commission_intake.models.commission_document import CommissionDocument


class ExtractionPass(TimeStampedUUIDModel):
    class PassNumber(models.IntegerChoices):
        PASS1_METADATA = 1, "Pass 1: Metadata"
        PASS2_MASTER_DATA = 2, "Pass 2: Master Data"
        PASS3_PRODUCTS_RULES = 3, "Pass 3: Products & Rules"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    document = models.ForeignKey(
        CommissionDocument,
        on_delete=models.CASCADE,
        related_name="extraction_passes",
    )
    pass_number = models.IntegerField(choices=PassNumber.choices)
    # Sheet name within the Excel file (blank for non-sheet documents)
    sheet_name = models.CharField(max_length=255, blank=True)
    # Attachment filename when source is email with Excel attachments (blank otherwise)
    attachment_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    # Raw JSON output from the LLM for this pass (null until completed)
    result = models.JSONField(null=True, blank=True)
    # The prompt sent to the LLM (stored for debugging and re-runs)
    prompt_text = models.TextField(blank=True)
    llm_model_used = models.CharField(max_length=100, blank=True)
    duration_seconds = models.FloatField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["document", "attachment_name", "sheet_name", "pass_number"]
        unique_together = [("document", "pass_number", "sheet_name", "attachment_name")]

    def __str__(self):
        parts = [f"Pass {self.pass_number}"]
        if self.attachment_name:
            parts.append(self.attachment_name)
        if self.sheet_name:
            parts.append(self.sheet_name)
        parts.append(f"for {self.document.original_filename}")
        return " / ".join(parts)
