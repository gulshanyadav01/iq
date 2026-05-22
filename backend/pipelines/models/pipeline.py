from django.conf import settings
from django.db import models

from config.abstract_models import TimeStampedUUIDModel


class Pipeline(TimeStampedUUIDModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=64, unique=True, db_index=True)
    description = models.TextField(blank=True, default="")
    is_seeded = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pipelines_created",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.slug})"
