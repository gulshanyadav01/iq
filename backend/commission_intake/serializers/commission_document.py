from rest_framework import serializers

from commission_intake.models import (
    CommissionDocument,
    IndexedContent,
    ExtractionPass,
    CICSResult,
    ValidationResult,
)


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
            "errors",
            "duration_seconds",
            "sheet_results",
            "created_at",
        ]
        read_only_fields = fields


class IndexedContentSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = IndexedContent
        fields = [
            "source_type",
            "primary_content",
            "email_context",
            "sheet_contents",
            "sheet_metadata",
            "indexer_version",
            "created_at",
        ]


class ExtractionPassSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractionPass
        fields = [
            "pass_number",
            "sheet_name",
            "attachment_name",
            "status",
            "result",
            "llm_model_used",
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
        fields = ["file", "metadata", "llm_provider", "llm_model", "excel_mode", "processing_mode"]

    def validate(self, data):
        if data.get("processing_mode") == CommissionDocument.ProcessingMode.EXTRACT_ONLY:
            raise serializers.ValidationError(
                {"processing_mode": "extract_only cannot be used when uploading a new document."}
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


class RerunPassRequestSerializer(serializers.Serializer):
    pass_number = serializers.ChoiceField(choices=[1, 2, 3])
    sheet_name = serializers.CharField(required=False, default="", allow_blank=True)
    attachment_name = serializers.CharField(required=False, default="", allow_blank=True)


class CommissionDocumentSerializer(serializers.ModelSerializer):
    cics_result = CICSResultSerializer(read_only=True)
    validation_result = ValidationResultSerializer(read_only=True)
    indexed_content = IndexedContentSummarySerializer(read_only=True)
    extraction_passes = ExtractionPassSerializer(many=True, read_only=True)

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
            "indexed_content",
            "extraction_passes",
            "cics_result",
            "validation_result",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
