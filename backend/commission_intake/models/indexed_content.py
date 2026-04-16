from django.db import models

from config.abstract_models import TimeStampedUUIDModel
from commission_intake.models.commission_document import CommissionDocument


class IndexedContent(TimeStampedUUIDModel):
    document = models.OneToOneField(
        CommissionDocument,
        on_delete=models.CASCADE,
        related_name="indexed_content",
    )
    source_type = models.CharField(max_length=20)  # email, pdf, excel
    # Email body / PDF full text / primary sheet markdown
    primary_content = models.TextField(blank=True)
    # For emails: sender, subject, date header block
    email_context = models.TextField(blank=True)
    # {sheet_name: markdown_text} — for Excel documents
    sheet_contents = models.JSONField(default=dict)
    # Sheet classifications (commission_data vs master_data) and table-split metadata
    sheet_metadata = models.JSONField(default=dict)
    # S3 keys for attachments extracted from emails
    attachment_keys = models.JSONField(default=list)
    # SHA256 of the original source file (for cache invalidation)
    content_hash = models.CharField(max_length=64)
    indexer_version = models.CharField(max_length=10)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"IndexedContent({self.document.original_filename})"
