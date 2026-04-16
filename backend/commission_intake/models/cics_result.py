from django.db import models

from config.abstract_models import TimeStampedUUIDModel
from commission_intake.models.commission_document import CommissionDocument


class CICSResult(TimeStampedUUIDModel):
    document = models.OneToOneField(
        CommissionDocument,
        on_delete=models.CASCADE,
        related_name="cics_result",
    )
    # Full CICSDocument dict (the final structured output)
    cics_doc = models.JSONField()
    # Merged RawExtraction dict before deterministic transformation (for debugging)
    raw_extraction = models.JSONField(null=True, blank=True)
    # Transformation warnings and errors from RawToCICSTransformer
    warnings = models.JSONField(default=list)
    errors = models.JSONField(default=list)
    duration_seconds = models.FloatField(default=0)
    # Per-sheet breakdown when an Excel workbook is processed sheet-by-sheet
    sheet_results = models.JSONField(default=list)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"CICSResult({self.document.original_filename})"
