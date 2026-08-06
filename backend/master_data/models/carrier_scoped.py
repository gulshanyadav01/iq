from django.db import models
from config.abstract_models import TimeStampedUUIDModel


class CarrierRTOZoneMaster(TimeStampedUUIDModel):
    """Carrier-specific underwriting-zone -> RTO/state expansion.

    Chola grids express rates by UW zone (Mumbai, Pune, Central MH, Chennai,
    UP East, NCR) rather than by RTO code or state name directly. Seeded from
    the carrier's RTO-zone master; the transformer expands a zone label found
    on a rule's location text into the RTO codes + states it represents.
    """

    carrier = models.CharField(max_length=32, db_index=True)
    zone = models.CharField(max_length=64)
    rto_code = models.CharField(max_length=32)
    state_slug = models.CharField(max_length=64, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["carrier", "zone", "rto_code"],
                name="uniq_carrier_zone_rto",
            )
        ]
        ordering = ["carrier", "zone", "rto_code"]

    def __str__(self):
        return f"{self.carrier}:{self.zone} -> {self.rto_code} ({self.state_slug})"


class CarrierProductHierarchyMaster(TimeStampedUUIDModel):
    """Carrier-specific Product / SubProduct / VehicleSubClass taxonomy.

    Seeded as reference data and used to validate the (product, sub_product,
    vehicle_sub_class) combinations a carrier's grid rules claim.
    ``vehicle_subclass`` is the raw source spelling (may carry the source's
    own typos, e.g. "Excavaters"); ``canonical_subclass`` is resolved at load
    time against the already-seeded ``VehicleSubClassMaster`` (no separate
    canonicalization table per carrier).
    """

    carrier = models.CharField(max_length=32, db_index=True)
    product = models.CharField(max_length=16)
    sub_product = models.CharField(max_length=32, blank=True, default="")
    vehicle_subclass = models.CharField(max_length=128)
    canonical_subclass = models.CharField(max_length=128)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["carrier", "product", "sub_product", "vehicle_subclass"],
                name="uniq_carrier_product_hierarchy",
            )
        ]
        indexes = [
            models.Index(fields=["carrier", "product"]),
        ]
        ordering = ["carrier", "product", "sub_product", "vehicle_subclass"]

    def __str__(self):
        seg = f"{self.product}/{self.sub_product}" if self.sub_product else self.product
        return f"{self.carrier}:{seg}:{self.vehicle_subclass}"
