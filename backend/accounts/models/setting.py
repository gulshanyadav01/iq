from django.db import models

from config import settings
from config.abstract_models import TimeStampedUUIDModel


class Setting(TimeStampedUUIDModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    setting = models.JSONField()

    def __str__(self):
        return self.user.email
