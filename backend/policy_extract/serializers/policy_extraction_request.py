from rest_framework import serializers
from policy_extract.models import PolicyExtractionRequest


class PolicyExtractionRequestCreateSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    metadata = serializers.JSONField(required=False, allow_null=True)

    class Meta:
        model = PolicyExtractionRequest
        fields = ["file", "metadata"]

    def create(self, validated_data):
        file_obj = validated_data["file"]
        return PolicyExtractionRequest.objects.create(
            file=file_obj,
            original_filename=file_obj.name,
            status=PolicyExtractionRequest.Status.INITIATED,
            metadata=validated_data.get("metadata"),
        )


class PolicyExtractionRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = PolicyExtractionRequest
        fields = [
            "id",
            "status",
            "original_filename",
            "metadata",
            "result",
            "error",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "original_filename",
            "result",
            "error",
            "created_at",
            "updated_at",
        ]
