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


class RTOMaster(TimeStampedUUIDModel):
    """Registration Transport Office lookup, one row per RTO.

    Seeded from the RTO Master sheet. ``value`` is the full display string the
    client export expects (e.g. ``AP01-Adilabad(Andhra Pradesh)(Group B)``);
    ``code`` is the registration prefix (``AP01``); ``state_slug`` is derived
    from the 2-letter code prefix, NOT the parenthetical (RJ/HR/PB parentheticals
    are city names). The per-product zone ids (``car_zone``/``tw_zone``/
    ``cv_zone``) are non-null only when the RTO is active for that product.
    """

    rto_id = models.IntegerField()
    value = models.CharField(max_length=255)
    code = models.CharField(max_length=32, unique=True)
    state_slug = models.CharField(max_length=64, db_index=True)
    group = models.CharField(max_length=32, blank=True, default="")
    car_zone = models.IntegerField(null=True, blank=True)
    tw_zone = models.IntegerField(null=True, blank=True)
    cv_zone = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["state_slug", "code"]

    def __str__(self):
        return f"{self.value} ({self.state_slug})"
