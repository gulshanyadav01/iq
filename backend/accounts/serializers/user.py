from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from accounts.models import User


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(max_length=128, write_only=True, required=True, validators=[validate_password])
    uuid = serializers.UUIDField(read_only=True)

    def create(self, validated_data: dict):
        password = validated_data.pop('password')
        validated_data['is_signed_up'] = True
        instance = super().create(validated_data)
        instance.set_password(password)
        instance.save()

        # send email to confirm email ownership
        instance.send_welcome_email()
        return instance

    def update(self, instance, validated_data):
        if validated_data.get('password', None):
            instance.set_password(validated_data.get('password'))
            validated_data.pop('password')
        is_signed_up = instance.is_signed_up
        if not is_signed_up:
            validated_data['is_signed_up'] = True
        instance = super().update(instance, validated_data)
        if not is_signed_up:
            # send email to confirm email ownership
            if not instance.is_email_verified:
                instance.send_welcome_email()
        return instance

    class Meta:
        model = User
        fields = ['id', 'uuid', 'password', 'last_login', 'first_name', 'last_name', 'email', 'is_email_verified']
