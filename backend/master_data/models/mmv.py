from django.db import models
from config.abstract_models import TimeStampedUUIDModel


class CarrierVehicleMMVMaster(TimeStampedUUIDModel):
    """Carrier-specific vehicle Make / Model / Variant catalogue.

    Seeded from the carrier's MMV workbooks (Chola supplied one two-and-four
    wheeler book and one commercial-vehicle book) and used to fold the
    free-text ``vehicle_make`` / ``vehicle_model`` a grid rule carries onto a
    single canonical spelling, and to flag makes/models no real vehicle
    matches. Reference data only — it never creates or drops a rule.

    ``product`` reuses the exact discriminator ``VehicleSubClassMaster.product``
    uses (``Car`` / ``TW`` / ``CV``) so a caller that has already bucketed a
    product does not need a second mapping.

    The ``*_slug`` columns are the match keys (see
    ``master_data.services.mmv.mmv_slug``); ``make`` / ``model`` / ``variant``
    keep the source spelling, which is what gets written back onto a rule.
    ``variant`` is blank for Car — the source's four-wheeler sheet carries make
    and model only.

    The natural key is the slug triple, NOT the source's per-row id: neither
    source sheet is distinct on (make, model, variant), and the rows that
    collide differ only in descriptive extras. The loader keeps the first-seen
    row's extras and counts the rest as skipped.

    The sources' numeric specs (CC, kW, gross vehicle weight, seating
    capacity, wheels, ex-showroom price) are deliberately NOT modelled:
    canonicalization and validation key on make and model alone, and carrying
    ~22k rows of unread numbers cost about a third of the committed seed CSV.
    Add the columns back here and in ``_MMV_COLUMNS`` together if something
    ever needs to price or filter on them.
    """

    # No db_index on carrier or the slug columns, and no extra composite
    # indexes: the ONLY read is carrier_vehicle_mmv's `.filter(carrier=...)`,
    # which the uniq_carrier_mmv constraint's leading column already serves.
    # All product/make/model narrowing happens in Python inside
    # build_mmv_index. Unused btrees are not free here — every reseed rewrites
    # ~22k rows and would pay to maintain each one.
    carrier = models.CharField(max_length=32)
    product = models.CharField(max_length=8)

    make = models.CharField(max_length=128)
    model = models.CharField(max_length=128)
    variant = models.CharField(max_length=128, blank=True, default="")

    make_slug = models.CharField(max_length=128)
    model_slug = models.CharField(max_length=128)
    variant_slug = models.CharField(max_length=128, blank=True, default="")

    fuel = models.CharField(max_length=16, blank=True, default="")
    sub_class = models.CharField(max_length=128, blank=True, default="")
    vehicle_class = models.CharField(max_length=16, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "carrier",
                    "product",
                    "make_slug",
                    "model_slug",
                    "variant_slug",
                ],
                name="uniq_carrier_mmv",
            )
        ]
        ordering = ["carrier", "product", "make", "model", "variant"]

    def __str__(self):
        return (
            f"{self.carrier}:{self.product}:"
            f"{self.make} {self.model} {self.variant}".strip()
        )
