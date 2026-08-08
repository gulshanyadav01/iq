from django.core.files.storage import default_storage
from rest_framework import serializers
from policy_extract.models import PolicyExtractionRequest


class PolicyExtractionRequestCreateSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    metadata = serializers.JSONField(required=False, allow_null=True)

    class Meta:
        model = PolicyExtractionRequest
        fields = ["file", "metadata"]

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
        metadata = {
            k: v for k, v in validated_data.items() if k not in ("policy_identifier",)
        }
        return PolicyExtractionRequest.objects.create(
            original_filename=validated_data["policy_identifier"],
            status=PolicyExtractionRequest.Status.INITIATED,
            metadata=metadata,
        )


class PolicyExtractionRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = PolicyExtractionRequest
        fields = [
            "id",
            "policy_extraction_id",
            "status",
            "original_filename",
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
            "source_url",
            "result",
            "error",
            "callback_response",
            "created_at",
            "updated_at",
        ]


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
