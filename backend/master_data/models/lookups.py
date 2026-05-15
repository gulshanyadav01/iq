from django.db import models
from config.abstract_models import TimeStampedUUIDModel


class PolicyTypeMaster(TimeStampedUUIDModel):
    name = models.CharField(max_length=64, unique=True)
    policy_id = models.IntegerField()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} (#{self.policy_id})"


class FuelTypeMaster(TimeStampedUUIDModel):
    name = models.CharField(max_length=32, unique=True)
    fuel_id = models.IntegerField()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} (#{self.fuel_id})"


class PlanTypeMaster(TimeStampedUUIDModel):
    plan_type = models.CharField(max_length=64)
    is_zero_dep = models.BooleanField(default=False)
    plan_type_id = models.IntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["plan_type", "is_zero_dep"],
                name="uniq_plan_zd",
            )
        ]
        ordering = ["plan_type", "is_zero_dep"]

    def __str__(self):
        zd = " + ZD" if self.is_zero_dep else ""
        return f"{self.plan_type}{zd} (#{self.plan_type_id})"


class CarrierMaster(TimeStampedUUIDModel):
    name = models.CharField(max_length=32, unique=True)
    carrier_id = models.IntegerField()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} (#{self.carrier_id})"
