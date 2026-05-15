from rest_framework import serializers

from master_data.models import MasterDataUpload


class MasterDataUploadCreateSerializer(serializers.Serializer):
    file = serializers.FileField(write_only=True)


class MasterDataUploadSerializer(serializers.ModelSerializer):
    uploaded_by_email = serializers.SerializerMethodField()

    class Meta:
        model = MasterDataUpload
        fields = [
            "id",
            "status",
            "summary",
            "error_message",
            "uploaded_by",
            "uploaded_by_email",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_uploaded_by_email(self, obj):
        return getattr(obj.uploaded_by, "email", None)
