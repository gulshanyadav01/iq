from django.conf import settings
from django.db import models

from config.abstract_models import TimeStampedUUIDModel


class CommissionDocument(TimeStampedUUIDModel):
    class Status(models.TextChoices):
        INITIATED = "initiated", "Initiated"
        INDEXING = "indexing", "Indexing"
        INDEXED = "indexed", "Indexed"
        EXTRACTING = "extracting", "Extracting"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class ProcessingMode(models.TextChoices):
        FULL = "full", "Full"
        INDEX_ONLY = "index_only", "Index Only"
        EXTRACT_ONLY = "extract_only", "Extract Only"

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commission_documents",
    )
    file = models.FileField(upload_to="commission_intake/%Y/%m/%d/")
    original_filename = models.CharField(max_length=255)
    source_type = models.CharField(max_length=20, blank=True)  # email, pdf, excel
    provider = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.INITIATED,
        db_index=True,
    )
    error = models.TextField(null=True, blank=True)
    llm_provider = models.CharField(max_length=50, default="gemini")
    llm_model = models.CharField(max_length=100, blank=True)
    excel_mode = models.CharField(max_length=30, default="sheet_by_sheet")
    processing_mode = models.CharField(
        max_length=20,
        choices=ProcessingMode.choices,
        default=ProcessingMode.FULL,
    )
    index_progress = models.JSONField(default=dict, blank=True)
    is_validated = models.BooleanField(default=False, db_index=True)
    validated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.original_filename} ({self.status})"
