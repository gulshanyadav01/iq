from django.db import models

from config import settings
from config.abstract_models import TimeStampedUUIDModel


class Profile(TimeStampedUUIDModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    def __str__(self):
        return self.user.email
