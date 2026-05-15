from django.db import models
from config.abstract_models import TimeStampedUUIDModel


class InsurerMaster(TimeStampedUUIDModel):
    insurer_id = models.IntegerField()
    product_id = models.IntegerField()
    name = models.CharField(max_length=255)
    name_normalized = models.CharField(max_length=255, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["insurer_id", "product_id"],
                name="uniq_insurer_product",
            )
        ]
        indexes = [
            models.Index(fields=["product_id", "name_normalized"]),
        ]
        ordering = ["name", "product_id"]

    def __str__(self):
        return f"{self.name} (#{self.insurer_id}, product {self.product_id})"
