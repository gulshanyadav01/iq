from rest_framework import serializers

from master_data.models import MasterDataUpload

# Only Chola is seeded/parsed today (chola_parsers.py); the field exists so
# the endpoint is shaped for future carriers without a breaking API change.
CARRIER_MASTER_TYPE_CHOICES = ("rto_zone", "product_hierarchy")


class MasterDataUploadCreateSerializer(serializers.Serializer):
    file = serializers.FileField(write_only=True)


class CarrierMasterUploadCreateSerializer(serializers.Serializer):
    carrier = serializers.CharField(max_length=32)
    master_type = serializers.ChoiceField(choices=CARRIER_MASTER_TYPE_CHOICES)
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
