from django.db import models
from config.abstract_models import TimeStampedUUIDModel


class VehicleMaster(TimeStampedUUIDModel):
    vehicle_type = models.CharField(max_length=64, unique=True)
    vehicle_id = models.IntegerField()
    product_id = models.IntegerField(db_index=True)

    class Meta:
        ordering = ["vehicle_type"]

    def __str__(self):
        return f"{self.vehicle_type} (product {self.product_id})"


class VehicleSubClassMaster(TimeStampedUUIDModel):
    pb_vehicle_subclass_id = models.IntegerField()
    vehicle_class_id = models.IntegerField(null=True, blank=True)
    subclass_name = models.CharField(max_length=128)
    canonical_name = models.CharField(max_length=128)
    product = models.CharField(max_length=8)
    product_id = models.IntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["pb_vehicle_subclass_id", "product"],
                name="uniq_subclass_product",
            )
        ]
        indexes = [
            models.Index(fields=["product"]),
        ]
        ordering = ["product", "pb_vehicle_subclass_id"]

    def __str__(self):
        return f"{self.canonical_name} ({self.product} #{self.pb_vehicle_subclass_id})"


class VehicleClassMaster(TimeStampedUUIDModel):
    name = models.CharField(max_length=64, unique=True)
    vehicle_class_id = models.CharField(max_length=16)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.vehicle_class_id})"
