from django.db import models

from config.abstract_models import TimeStampedUUIDModel
from commission_intake.models.commission_document import CommissionDocument


class ValidationResult(TimeStampedUUIDModel):
    document = models.OneToOneField(
        CommissionDocument,
        on_delete=models.CASCADE,
        related_name="validation_result",
    )
    # List of ValidationIssue dicts from SchemaValidator
    schema_issues = models.JSONField(default=list)
    # List of RuleOverlap dicts from RuleOverlapChecker
    overlap_issues = models.JSONField(default=list)
    is_valid = models.BooleanField(default=False)
    has_critical = models.BooleanField(default=False)
    has_high = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        status = "valid" if self.is_valid else "invalid"
        return f"ValidationResult({self.document.original_filename}, {status})"
