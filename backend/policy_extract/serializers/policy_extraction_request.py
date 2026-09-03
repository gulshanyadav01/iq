from django.core.files.storage import default_storage
from rest_framework import serializers
from policy_extract.models import PolicyExtractionRequest


class PolicyExtractionRequestCreateSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    metadata = serializers.JSONField(required=False, allow_null=True)

    class Meta:
        model = PolicyExtractionRequest
        fields = ["file", "metadata"]

    def validate_file(self, file_obj):
        # Reject anything the extraction pipeline can't parse before it is stored
        # or enqueued, so a non-PDF (e.g. a .jsonl) never reaches the PDF-only
        # MinerU endpoint. Checks the extension and sniffs the leading bytes so a
        # file merely renamed to .pdf is still caught.
        import os

        from policy_extract.services.content_type import (
            EXTRACTABLE_EXTENSIONS,
            is_extractable,
        )

        head = file_obj.read(16)
        file_obj.seek(0)
        if not is_extractable(file_obj.name, head):
            ext = os.path.splitext(file_obj.name or "")[-1].lower()
            allowed = ", ".join(sorted(EXTRACTABLE_EXTENSIONS))
            detail = (
                f"Unsupported file type '{ext}'."
                if ext
                else "The uploaded file is not a valid PDF."
            )
            raise serializers.ValidationError(
                f"{detail} Only {allowed} files are supported."
            )
        return file_obj

    def create(self, validated_data):
        import uuid

        file_obj = validated_data["file"]
        request_id = uuid.uuid4()

        # Save file to MinIO and store path in source_url
        storage_key = f"policy_extractions/originals/{request_id}/{file_obj.name}"
        default_storage.save(storage_key, file_obj)

        return PolicyExtractionRequest.objects.create(
            original_filename=file_obj.name,
            source_url=storage_key,
            status=PolicyExtractionRequest.Status.INITIATED,
            metadata=validated_data.get("metadata"),
        )


class PolicyExtractionRequestLinkCreateSerializer(serializers.Serializer):
    file_link = serializers.URLField()
    policy_identifier = serializers.CharField()
    file_type = serializers.CharField(required=False, default="application/pdf")
    RequestId = serializers.CharField(required=False, allow_null=True)
    RequestBy = serializers.CharField(required=False, allow_null=True)
    CreatedAt = serializers.CharField(required=False, allow_null=True)
    UpdatedAt = serializers.CharField(required=False, allow_null=True)
    form_num = serializers.CharField(required=False, allow_null=True)
    ipAddress = serializers.CharField(required=False, allow_null=True)

    def create(self, validated_data):
        import uuid

        from policy_extract.services.remote_fetch import fetch_document_to_storage

        policy_identifier = validated_data["policy_identifier"]
        file_link = validated_data["file_link"]
        metadata = {
            k: v for k, v in validated_data.items() if k not in ("policy_identifier",)
        }

        # Fetch the presigned document NOW, while its (typically ~5-minute) URL is
        # still valid, and persist a durable copy under source_url. The task reads
        # that stored copy, never the ephemeral link — so a queue backlog can't
        # push the download past expiry. A failed/expired link raises straight to
        # the client (502/400). See services/remote_fetch.py for the rationale.
        request_id = uuid.uuid4()
        storage_key = f"policy_extractions/originals/{request_id}/{policy_identifier}"
        fetch_document_to_storage(file_link, storage_key, policy_identifier)

        return PolicyExtractionRequest.objects.create(
            original_filename=policy_identifier,
            source_url=storage_key,
            status=PolicyExtractionRequest.Status.INITIATED,
            metadata=metadata,
        )


class PolicyExtractionRequestSerializer(serializers.ModelSerializer):
    # Best-guess MIME type so the frontend can pick a preview renderer even for
    # extensionless filenames (resolved from name + metadata only — no storage IO).
    content_type = serializers.SerializerMethodField()

    class Meta:
        model = PolicyExtractionRequest
        fields = [
            "id",
            "policy_extraction_id",
            "status",
            "original_filename",
            "content_type",
            "source_url",
            "metadata",
            "result",
            "error",
            "callback_response",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "policy_extraction_id",
            "status",
            "original_filename",
            "content_type",
            "source_url",
            "result",
            "error",
            "callback_response",
            "created_at",
            "updated_at",
        ]

    def get_content_type(self, obj):
        from policy_extract.services.content_type import resolve_content_type

        return resolve_content_type(obj)


class PolicyExtractResultSerializer(serializers.Serializer):
    extracted_fields = serializers.DictField(
        help_text="Structured fields extracted from the policy document."
    )


class PolicyExtractCallbackEntitySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    policy_extraction_id = serializers.CharField()
    status = serializers.ChoiceField(
        choices=["initiated", "processing", "completed", "failed"]
    )
    original_filename = serializers.CharField()
    source_url = serializers.CharField(allow_null=True)
    metadata = serializers.DictField(allow_null=True)
    result = PolicyExtractResultSerializer(allow_null=True)
    error = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class PolicyExtractCallbackSerializer(serializers.Serializer):
    id = serializers.CharField(
        help_text="policy_extraction_id of the request (e.g. pext_...)."
    )
    event_type = serializers.ChoiceField(
        choices=["policy_extract.completed", "policy_extract.failed"],
        help_text="Type of event that triggered this callback.",
    )
    entity_type = serializers.CharField(default="policy_extraction_request")
    entity_payload = PolicyExtractCallbackEntitySerializer()
    event_created_at = serializers.DateTimeField()
