from datetime import date

from rest_framework import serializers

from commission_intake.models import (
    CommissionDocument,
    IndexedContent,
    ExtractionPass,
    CICSResult,
    ValidationResult,
)


def format_applicable_period(period_label, effective_from, effective_to=None):
    """Render the commission period for the runs list — structured-first.

    Prefers the uniform structured dates (`effective_from`/`effective_to`)
    normalized to "Jul 2025" so the column sorts and reads consistently. Falls
    back to the free-text LLM `period_label` ("Q1 2024", "July'25") only when no
    parseable structured date is present. Every branch is guarded so malformed
    JSON (partial dates, non-string values) degrades to a raw string or None
    rather than 500ing the list/detail endpoints.
    """
    if effective_from:
        try:
            start = date.fromisoformat(str(effective_from))  # str() + TypeError guard
            label = start.strftime("%b %Y")  # "Jul 2025"
            if effective_to:
                try:
                    end = date.fromisoformat(str(effective_to))
                    if (end.year, end.month) != (start.year, start.month):
                        # Same year → drop the redundant start-year: "Jul – Sep 2025".
                        # Cross-year → keep both: "Dec 2025 – Feb 2026".
                        start_part = (
                            start.strftime("%b")
                            if end.year == start.year
                            else start.strftime("%b %Y")
                        )
                        label = f"{start_part} – {end.strftime('%b %Y')}"
                except (ValueError, TypeError):
                    pass
            return label
        except (ValueError, TypeError):  # partial "2025-07" / non-date → fall through
            pass
    if period_label:
        return str(period_label)
    if effective_from:
        return str(effective_from)  # last resort: raw start string
    return None


class ValidationResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValidationResult
        fields = [
            "is_valid",
            "has_critical",
            "has_high",
            "schema_issues",
            "overlap_issues",
            "created_at",
        ]


class CICSResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = CICSResult
        fields = [
            "id",
            "cics_doc",
            "raw_extraction",
            "warnings",
            "notices",
            "errors",
            "duration_seconds",
            "sheet_results",
            "created_at",
        ]
        read_only_fields = fields


class IndexedContentSummarySerializer(serializers.ModelSerializer):
    hidden_sheets_excluded = serializers.SerializerMethodField()

    class Meta:
        model = IndexedContent
        fields = [
            "source_type",
            "primary_content",
            "email_context",
            "sheet_contents",
            "sheet_metadata",
            "indexer_version",
            "hidden_sheets_excluded",
            "created_at",
        ]

    def get_hidden_sheets_excluded(self, obj) -> bool:
        """Whether the pipeline is currently excluding hidden worksheets.

        The UI cannot derive this: `visibility` is recorded on every sheet
        regardless, and the `CINS_SKIP_HIDDEN_SHEETS` rollout switch decides
        whether routing acts on it. Without this field, flipping the flag off
        makes the backend extract hidden sheets while the UI still reports them
        as skipped — the rollback path silently turning the coverage notice
        into a lie.
        """
        from cins.processors.core.sheet_visibility import skip_hidden_sheets_enabled

        return skip_hidden_sheets_enabled()


class ExtractionPassSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractionPass
        fields = [
            "pass_number",
            "sheet_name",
            "attachment_name",
            "status",
            "result",
            "prompt_text",
            "system_instruction",
            "llm_model_used",
            "extraction_method",
            "duration_seconds",
            "error",
            "created_at",
        ]


class CommissionDocumentCreateSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    metadata = serializers.JSONField(required=False, allow_null=True, write_only=True)
    processing_mode = serializers.ChoiceField(
        choices=CommissionDocument.ProcessingMode.choices,
        default=CommissionDocument.ProcessingMode.FULL,
        required=False,
    )

    class Meta:
        model = CommissionDocument
        fields = [
            "file",
            "metadata",
            "llm_provider",
            "llm_model",
            "excel_mode",
            "processing_mode",
        ]

    def validate(self, data):
        if (
            data.get("processing_mode")
            == CommissionDocument.ProcessingMode.EXTRACT_ONLY
        ):
            raise serializers.ValidationError(
                {
                    "processing_mode": "extract_only cannot be used when uploading a new document."
                }
            )
        return data

    def create(self, validated_data):
        file_obj = validated_data.pop("file")
        validated_data.pop("metadata", None)
        return CommissionDocument.objects.create(
            file=file_obj,
            original_filename=file_obj.name,
            status=CommissionDocument.Status.INITIATED,
            **validated_data,
        )


class ProcessingModeRequestSerializer(serializers.Serializer):
    processing_mode = serializers.ChoiceField(
        choices=CommissionDocument.ProcessingMode.choices,
        required=False,
    )


class ExtractRequestSerializer(serializers.Serializer):
    # When true, skip the CICS output-cache lookup and re-run the LLM extraction;
    # a gate-passing result overwrites the cached row for that key.
    force_refresh = serializers.BooleanField(required=False, default=False)


class RerunPassRequestSerializer(serializers.Serializer):
    pass_number = serializers.ChoiceField(choices=[1, 2, 3])
    sheet_name = serializers.CharField(required=False, default="", allow_blank=True)
    attachment_name = serializers.CharField(
        required=False, default="", allow_blank=True
    )


class CommissionDocumentListSerializer(serializers.ModelSerializer):
    """Lean serializer for list views — excludes heavy nested extraction/indexing payloads."""

    applicable_period = serializers.SerializerMethodField()

    class Meta:
        model = CommissionDocument
        fields = [
            "id",
            "status",
            "processing_mode",
            "original_filename",
            "source_type",
            "provider",
            "llm_provider",
            "llm_model",
            "excel_mode",
            "error",
            "index_progress",
            "is_validated",
            "validated_at",
            "applicable_period",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_applicable_period(self, obj) -> "str | None":
        # Reads DB-side JSON annotations set on the list queryset (no blob load).
        return format_applicable_period(
            getattr(obj, "_period_label", None),
            getattr(obj, "_period_effective_from", None),
            getattr(obj, "_period_effective_to", None),
        )


class CommissionDocumentSerializer(serializers.ModelSerializer):
    cics_result = CICSResultSerializer(read_only=True)
    validation_result = ValidationResultSerializer(read_only=True)
    indexed_content = IndexedContentSummarySerializer(read_only=True)
    extraction_passes = ExtractionPassSerializer(many=True, read_only=True)
    applicable_period = serializers.SerializerMethodField()

    class Meta:
        model = CommissionDocument
        fields = [
            "id",
            "status",
            "processing_mode",
            "original_filename",
            "source_type",
            "provider",
            "llm_provider",
            "llm_model",
            "excel_mode",
            "error",
            "index_progress",
            "is_validated",
            "validated_at",
            "indexed_content",
            "extraction_passes",
            "cics_result",
            "validation_result",
            "applicable_period",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_applicable_period(self, obj) -> "str | None":
        # Reads from the already-loaded reverse-O2O relation (cached, no extra
        # query) so a poll-completed detail row shows the period immediately.
        cics = getattr(obj, "cics_result", None)
        doc = getattr(cics, "cics_doc", None) if cics else None
        period = doc.get("commission_period") if isinstance(doc, dict) else None
        period = period if isinstance(period, dict) else {}
        return format_applicable_period(
            period.get("period_label"),
            period.get("effective_from"),
            period.get("effective_to"),
        )
