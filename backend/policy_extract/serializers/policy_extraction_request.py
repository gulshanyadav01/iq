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
    callback_url = serializers.URLField(required=False, allow_null=True)
    RequestId = serializers.CharField(required=False, allow_null=True)
    RequestBy = serializers.CharField(required=False, allow_null=True)
    CreatedAt = serializers.CharField(required=False, allow_null=True)
    UpdatedAt = serializers.CharField(required=False, allow_null=True)
    form_num = serializers.CharField(required=False, allow_null=True)
    ipAddress = serializers.CharField(required=False, allow_null=True)

    def create(self, validated_data):
        metadata = {k: v for k, v in validated_data.items()
                    if k not in ("policy_identifier",)}
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
            "created_at",
            "updated_at",
        ]
