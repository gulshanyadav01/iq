"""Idempotent loader for the Master Mapper Excel workbook.

Used by both the ``load_master_data`` management command and the upload API.
Both call :meth:`MasterDataLoader.load_workbook` so the ingest semantics stay
identical no matter how the workbook arrives.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl
from django.db import transaction
from django.utils import timezone

from master_data.models import (
    CarrierMaster,
    CarrierProductHierarchyMaster,
    CarrierRTOZoneMaster,
    CarrierVehicleMMVMaster,
    FuelTypeMaster,
    InsurerMaster,
    PlanTypeMaster,
    PolicyTypeMaster,
    RTOMaster,
    VehicleClassMaster,
    VehicleMaster,
    VehicleSubClassMaster,
)
from master_data.services.mmv import mmv_slug
from master_data.services.canonical import (
    VEHICLE_SUBCLASS_CANONICAL,
    normalize_insurer_name,
)
from master_data.services.rto import state_slug_for_code, state_slug_for_rto


@dataclass
class SheetResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
        }


@dataclass
class LoadResult:
    sheets: dict[str, SheetResult] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {name: r.as_dict() for name, r in self.sheets.items()}


SHEET_NAMES = {
    "fuel": "Fuel Type",
    "policy": "Policy Type",
    "plan": "Plan Type",
    "carrier": "Carrier ",  # trailing space is intentional — sheet name has it
    "vehicle_class": "Vehicle Class ",  # trailing space — same reason
    "vehicle": "Vehicle Master",
    "subclass": "SubClass Master",
    "insurer": "Insurer Master",
}

# RTO master lives as a committed, diff-friendly CSV in the repo (the source is
# the GetAllRTOMaster export, flattened to rto_id,code,value,car_zone,tw_zone,
# cv_zone). It is NOT a sheet in the Master Mapper workbook.
RTO_CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "rto_master.csv"

# Chola carrier-scoped masters — also committed CSVs, generated from the
# source xlsx via chola_parsers.py (see data/chola_*_source.xlsx). Neither is
# a sheet in the Master Mapper workbook.
CHOLA_RTO_ZONE_CSV_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "chola_rto_zone.csv"
)
CHOLA_PRODUCT_HIERARCHY_CSV_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "chola_product_hierarchy.csv"
)
# Chola's vehicle Make/Model/Variant catalogue, generated from BOTH source
# books (chola_mmv_2w4w_source.xlsx + chola_mmv_cv_source.xlsx) via
# parse_chola_mmv. Two orders of magnitude larger than the other seed CSVs
# (~22k rows), which is why its upsert is batched rather than row-by-row.
CHOLA_MMV_CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "chola_mmv.csv"

# The MMV columns a reseed may rewrite — everything outside the natural key.
# Declared once rather than derived from an arbitrary payload row, so
# bulk_update's field list cannot drift with the shape of the input dict.
_MMV_MUTABLE_FIELDS = [
    "make",
    "model",
    "variant",
    "fuel",
    "sub_class",
    "vehicle_class",
]

# ICICI carrier-scoped RTO-grid master — same shape as Chola's RTO-zone CSV
# (carrier,zone,rto_code), generated from the source xlsx via
# icici_parsers.py (see data/icici_rto_master_source.xlsx). Not a sheet in
# the Master Mapper workbook.
ICICI_RTO_GRID_CSV_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "icici_rto_grid.csv"
)


class MasterDataLoader:
    def __init__(self) -> None:
        self.result = LoadResult()

    @transaction.atomic
    def load_workbook(self, workbook: openpyxl.Workbook) -> LoadResult:
        """Load all master sheets from an open openpyxl Workbook.

        All sheet loads happen inside a single transaction; any failure rolls
        back the entire load.
        """
        self.result = LoadResult()
        self._load_fuel(workbook)
        self._load_policy(workbook)
        self._load_plan(workbook)
        self._load_carrier(workbook)
        self._load_vehicle_class(workbook)
        self._load_vehicle(workbook)
        self._load_subclass(workbook)
        self._load_insurer(workbook)
        self._load_rto()
        self._load_chola_rto_zone()
        self._load_chola_product_hierarchy()
        self._load_chola_mmv()
        self._load_icici_rto_grid()
        return self.result

    def load_path(self, path) -> LoadResult:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            return self.load_workbook(wb)
        finally:
            wb.close()

    # --------------------------- per-sheet loaders ---------------------------

    def _get_sheet(self, wb: openpyxl.Workbook, key: str):
        name = SHEET_NAMES[key]
        # Try exact match, then fall back to a stripped match in case Excel
        # ever loses the trailing space.
        if name in wb.sheetnames:
            return wb[name]
        for sheet_name in wb.sheetnames:
            if sheet_name.strip() == name.strip():
                return wb[sheet_name]
        raise KeyError(f"Sheet '{name}' not found in workbook")

    def _iter_data_rows(self, sheet):
        """Yield rows from row 2 onwards, skipping rows where every cell is None."""
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if row is None or all(c is None for c in row):
                continue
            yield row

    def _track(self, key: str, was_created: bool) -> None:
        bucket = self.result.sheets.setdefault(key, SheetResult())
        if was_created:
            bucket.created += 1
        else:
            bucket.updated += 1

    def _skip(self, key: str) -> None:
        bucket = self.result.sheets.setdefault(key, SheetResult())
        bucket.skipped += 1

    @staticmethod
    def _str(value: Any) -> str:
        return str(value).strip() if value is not None else ""

    @staticmethod
    def _int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _load_fuel(self, wb) -> None:
        sheet = self._get_sheet(wb, "fuel")
        for row in self._iter_data_rows(sheet):
            name, fuel_id = row[0], row[1]
            name = self._str(name)
            fid = self._int(fuel_id)
            if not name or fid is None:
                self._skip("FuelType")
                continue
            _, created = FuelTypeMaster.objects.update_or_create(
                name=name, defaults={"fuel_id": fid}
            )
            self._track("FuelType", created)

    def _load_policy(self, wb) -> None:
        sheet = self._get_sheet(wb, "policy")
        for row in self._iter_data_rows(sheet):
            name = self._str(row[0])
            pid = self._int(row[1])
            if not name or pid is None:
                self._skip("PolicyType")
                continue
            _, created = PolicyTypeMaster.objects.update_or_create(
                name=name, defaults={"policy_id": pid}
            )
            self._track("PolicyType", created)

    def _load_plan(self, wb) -> None:
        sheet = self._get_sheet(wb, "plan")
        for row in self._iter_data_rows(sheet):
            raw_name = self._str(row[0])
            pid = self._int(row[1])
            if not raw_name or pid is None:
                self._skip("PlanType")
                continue
            # Detect ZD variants from the plan name itself.
            upper = raw_name.upper()
            is_zd = "ZD" in upper or "ZERO DEP" in upper
            base_name = (
                raw_name.replace(" + ZD", "")
                .replace("+ZD", "")
                .replace("+ ZD", "")
                .strip()
            )
            _, created = PlanTypeMaster.objects.update_or_create(
                plan_type=base_name,
                is_zero_dep=is_zd,
                defaults={"plan_type_id": pid},
            )
            self._track("PlanType", created)

    def _load_carrier(self, wb) -> None:
        sheet = self._get_sheet(wb, "carrier")
        for row in self._iter_data_rows(sheet):
            name = self._str(row[0])
            cid = self._int(row[1])
            if not name or cid is None:
                self._skip("Carrier")
                continue
            _, created = CarrierMaster.objects.update_or_create(
                name=name, defaults={"carrier_id": cid}
            )
            self._track("Carrier", created)

    def _load_vehicle_class(self, wb) -> None:
        sheet = self._get_sheet(wb, "vehicle_class")
        for row in self._iter_data_rows(sheet):
            name = self._str(row[0])
            # vehicle_class_id is a CharField because some rows have "Blank",
            # but numeric ones must round-trip as integers (1, not "1.0").
            raw = row[1]
            if raw is None:
                class_id = ""
            elif isinstance(raw, (int, float)):
                class_id = str(int(raw))
            else:
                class_id = str(raw).strip()
            if not name:
                self._skip("VehicleClass")
                continue
            _, created = VehicleClassMaster.objects.update_or_create(
                name=name, defaults={"vehicle_class_id": class_id}
            )
            self._track("VehicleClass", created)

    def _load_vehicle(self, wb) -> None:
        sheet = self._get_sheet(wb, "vehicle")
        for row in self._iter_data_rows(sheet):
            vt = self._str(row[0])
            vid = self._int(row[1])
            if not vt or vid is None:
                self._skip("VehicleMaster")
                continue
            _, created = VehicleMaster.objects.update_or_create(
                vehicle_type=vt,
                defaults={"vehicle_id": vid, "product_id": vid},
            )
            self._track("VehicleMaster", created)

    def _load_subclass(self, wb) -> None:
        sheet = self._get_sheet(wb, "subclass")
        for row in self._iter_data_rows(sheet):
            sub_id = self._int(row[0])
            class_id = self._int(row[1])
            sub_name = self._str(row[2])
            product = self._str(row[3])
            product_id = self._int(row[4])
            if sub_id is None or not product or not sub_name:
                self._skip("SubClass")
                continue
            canonical = VEHICLE_SUBCLASS_CANONICAL.get((product, sub_id), sub_name)
            _, created = VehicleSubClassMaster.objects.update_or_create(
                pb_vehicle_subclass_id=sub_id,
                product=product,
                defaults={
                    "vehicle_class_id": class_id,
                    "subclass_name": sub_name,
                    "canonical_name": canonical,
                    "product_id": product_id if product_id is not None else 0,
                },
            )
            self._track("SubClass", created)

    def _load_rto(self, path: Path | None = None) -> None:
        """Load the RTO master from the committed CSV (``rto_master.csv``).

        Tolerant of absence (a missing CSV is a no-op, not an error). Derives
        ``state_slug`` via ``state_slug_for_rto`` (display-value parenthetical
        first, 2-letter code prefix as fallback — see ``services/rto``) and
        ``group`` from the ``(Group X)`` suffix in the display value; rows that
        resolve by neither route (or which lack a code / id / value) are skipped
        and counted so seeding gaps surface in the command output.
        """
        csv_path = path or RTO_CSV_PATH
        if not csv_path.exists():
            return  # RTO CSV not present — nothing to load.
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                rto_id = self._int(row.get("rto_id"))
                code = self._str(row.get("code"))
                value = self._str(row.get("value"))
                if rto_id is None or not code or not value:
                    self._skip("RTO")
                    continue
                state_slug = state_slug_for_rto(code, value)
                if not state_slug:
                    # Neither the display value nor the registration prefix
                    # named a known state — skip loudly via the count.
                    self._skip("RTO")
                    continue
                group = ""
                if "(Group " in value:
                    group = value.split("(Group ", 1)[1].rstrip(")").strip()
                _, created = RTOMaster.objects.update_or_create(
                    code=code,
                    defaults={
                        "rto_id": rto_id,
                        "value": value,
                        "state_slug": state_slug,
                        "group": group,
                        "car_zone": self._int(row.get("car_zone")),
                        "tw_zone": self._int(row.get("tw_zone")),
                        "cv_zone": self._int(row.get("cv_zone")),
                    },
                )
                self._track("RTO", created)

    def _load_chola_rto_zone(self, path: Path | None = None) -> None:
        """Load the Chola RTO-zone master from the committed CSV.

        Tolerant of absence (a missing CSV is a no-op, not an error).
        """
        csv_path = path or CHOLA_RTO_ZONE_CSV_PATH
        if not csv_path.exists():
            return
        rows = []
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                rows.append(
                    (
                        row.get("carrier", ""),
                        row.get("zone", ""),
                        row.get("rto_code", ""),
                    )
                )
        self.upsert_carrier_rto_zone_rows(rows)

    def upsert_carrier_rto_zone_rows(self, rows) -> None:
        """Idempotent upsert of ``(carrier, zone, rto_code)`` rows.

        Carrier-agnostic — shared by every carrier's RTO-zone CSV-seeding
        path (``_load_chola_rto_zone``, ``_load_icici_rto_grid``, ...) and
        the carrier-scoped upload API, so all of them go through one upsert
        code path. ``state_slug`` is derived from the RTO code's 2-letter
        prefix; a prefix absent from ``RTO_PREFIX_TO_STATE_SLUG`` is skipped
        and counted so seeding gaps surface loudly instead of silently
        dropping the row.

        Deliberately prefix-only, unlike ``_load_rto``: a zone row carries a
        bare code with no display value, so there is no parenthetical to prefer.
        These rows drive zone→code expansion in the transformer, not the
        export's state→RTO fill, so the NCR/Chandigarh/Daman reassignments
        ``state_slug_for_rto`` makes do not apply here.
        """
        for carrier, zone, rto_code in rows:
            carrier = self._str(carrier)
            zone = self._str(zone)
            code = self._str(rto_code).upper()
            if not carrier or not zone or not code:
                self._skip("CarrierRTOZone")
                continue
            state_slug = state_slug_for_code(code)
            if not state_slug:
                self._skip("CarrierRTOZone")
                continue
            _, created = CarrierRTOZoneMaster.objects.update_or_create(
                carrier=carrier,
                zone=zone,
                rto_code=code,
                defaults={"state_slug": state_slug},
            )
            self._track("CarrierRTOZone", created)

    def _load_chola_product_hierarchy(self, path: Path | None = None) -> None:
        """Load the Chola product-hierarchy master from the committed CSV.

        Tolerant of absence (a missing CSV is a no-op, not an error). Must
        run after ``_load_subclass`` so ``canonical_subclass`` resolution
        against the already-seeded ``VehicleSubClassMaster`` sees the full
        table within the same transaction.
        """
        csv_path = path or CHOLA_PRODUCT_HIERARCHY_CSV_PATH
        if not csv_path.exists():
            return
        rows = []
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                rows.append(
                    (
                        row.get("carrier", ""),
                        row.get("product", ""),
                        row.get("sub_product", ""),
                        row.get("vehicle_subclass", ""),
                    )
                )
        self.upsert_chola_product_hierarchy_rows(rows)

    def upsert_chola_product_hierarchy_rows(self, rows) -> None:
        """Idempotent upsert of ``(carrier, product, sub_product, vehicle_subclass)``
        rows. Shared by the CSV-seeding path and the carrier-scoped upload API.

        ``canonical_subclass`` reuses the existing ``VehicleSubClassMaster``
        spelling (populated from ``VEHICLE_SUBCLASS_CANONICAL``) via a
        case-insensitive name match — no new canonicalization table. Falls
        back to the raw source spelling when no match exists (e.g. Chola's
        "Kaali Peeli Taxi" / "Moped", which aren't in the shared subclass
        master).
        """
        for carrier, product, sub_product, vehicle_subclass in rows:
            carrier = self._str(carrier)
            product = self._str(product)
            sub_product = self._str(sub_product)
            subclass = self._str(vehicle_subclass)
            if not carrier or not product or not subclass:
                self._skip("CarrierProductHierarchy")
                continue
            canonical = self._canonical_subclass(product, subclass)
            _, created = CarrierProductHierarchyMaster.objects.update_or_create(
                carrier=carrier,
                product=product,
                sub_product=sub_product,
                vehicle_subclass=subclass,
                defaults={"canonical_subclass": canonical},
            )
            self._track("CarrierProductHierarchy", created)

    def _load_chola_mmv(self, path: Path | None = None) -> None:
        """Load Chola's Make/Model/Variant catalogue from the committed CSV.

        Tolerant of absence (a missing CSV is a no-op, not an error). The
        ``*_slug`` match keys are NOT stored in the CSV — they are recomputed
        here through ``mmv_slug``, the same function the transformer and
        validator match with, so the seeded keys can never drift from the
        code that queries them.
        """
        csv_path = path or CHOLA_MMV_CSV_PATH
        if not csv_path.exists():
            return
        with open(csv_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.upsert_chola_mmv_rows(rows)

    def upsert_chola_mmv_rows(self, rows) -> None:
        """Idempotent upsert of MMV rows, batched.

        Shared by the CSV-seeding path and the carrier-scoped upload API.

        Deliberately NOT the per-row ``update_or_create`` every other loader
        uses: this table seeds ~22k rows, and 22k round trips inside
        ``load_workbook``'s single transaction would add most of a minute to
        every seed. Instead it reads the existing rows for the carriers being
        loaded once, then splits into one ``bulk_create`` and one
        ``bulk_update``. The created/updated/skipped accounting the command
        prints is unchanged.

        Rows colliding on the natural key within one payload keep the FIRST
        occurrence's descriptive extras and are counted as skipped. On both
        real paths that count is 0 — ``parse_chola_mmv`` already dedupes and
        the committed CSV is generated from its output — but this method is
        public, so the guard defends the DB constraint against any caller.

        Never DELETES. A row dropped from the CSV or from a re-uploaded
        workbook lingers until removed by hand — same contract as the sibling
        carrier masters. It bites harder here: ``_keep_preferred`` picks the
        SHORTEST spelling per slug, so one stale orphan carrying a shorter
        make can keep winning canonicalization for that brand. To evict,
        delete the carrier's rows then reseed.
        """
        key = "CarrierVehicleMMV"
        prepared: dict[tuple, dict] = {}
        carriers: set[str] = set()

        for row in rows:
            carrier = self._str(row.get("carrier"))
            product = self._str(row.get("product"))
            make = self._str(row.get("make"))
            model = self._str(row.get("model"))
            variant = self._str(row.get("variant"))
            make_slug = mmv_slug(make)
            model_slug = mmv_slug(model)
            if not carrier or not product or not make_slug or not model_slug:
                self._skip(key)
                continue
            variant_slug = mmv_slug(variant)
            natural = (carrier, product, make_slug, model_slug, variant_slug)
            if natural in prepared:
                self._skip(key)
                continue
            carriers.add(carrier)
            prepared[natural] = {
                "make": make,
                "model": model,
                "variant": variant,
                "fuel": self._str(row.get("fuel")),
                "sub_class": self._str(row.get("sub_class")),
                "vehicle_class": self._str(row.get("vehicle_class")),
            }

        if not prepared:
            return

        existing = {
            (
                obj.carrier,
                obj.product,
                obj.make_slug,
                obj.model_slug,
                obj.variant_slug,
            ): obj
            for obj in CarrierVehicleMMVMaster.objects.filter(carrier__in=carriers)
        }

        to_create: list[CarrierVehicleMMVMaster] = []
        to_update: list[CarrierVehicleMMVMaster] = []
        fields = _MMV_MUTABLE_FIELDS + ["updated_at"]

        for natural, defaults in prepared.items():
            carrier, product, make_slug, model_slug, variant_slug = natural
            obj = existing.get(natural)
            if obj is None:
                to_create.append(
                    CarrierVehicleMMVMaster(
                        carrier=carrier,
                        product=product,
                        make_slug=make_slug,
                        model_slug=model_slug,
                        variant_slug=variant_slug,
                        **defaults,
                    )
                )
            else:
                for name, value in defaults.items():
                    setattr(obj, name, value)
                # bulk_update skips pre_save, so TimeStampedUUIDModel's
                # auto_now never fires — set it explicitly or a reseed leaves
                # updated_at frozen and "what did the last seed touch?"
                # becomes unanswerable.
                obj.updated_at = timezone.now()
                to_update.append(obj)

        if to_create:
            CarrierVehicleMMVMaster.objects.bulk_create(to_create, batch_size=2000)
        if to_update:
            CarrierVehicleMMVMaster.objects.bulk_update(
                to_update, fields, batch_size=2000
            )

        bucket = self.result.sheets.setdefault(key, SheetResult())
        bucket.created += len(to_create)
        bucket.updated += len(to_update)

    @staticmethod
    def _canonical_subclass(product: str, raw_name: str) -> str:
        match = VehicleSubClassMaster.objects.filter(
            product=product, subclass_name__iexact=raw_name
        ).first()
        return match.canonical_name if match else raw_name

    def _load_icici_rto_grid(self, path: Path | None = None) -> None:
        """Load the ICICI RTO-grid master from the committed CSV.

        Same shape and upsert path as Chola's RTO-zone master
        (``carrier,zone,rto_code`` -> ``CarrierRTOZoneMaster``) — ICICI's
        source maps each RTO_CODE to a single ``GRID MAPPING`` label (a
        state name, a city/vertical grouping like "HYDERABAD", or a zone
        like "NCR"/"UP-EAST"), which is exactly the zone side of the same
        (carrier, zone, rto_code) relationship Chola's zone->RTO expansion
        uses. Tolerant of absence (a missing CSV is a no-op, not an error).
        """
        csv_path = path or ICICI_RTO_GRID_CSV_PATH
        if not csv_path.exists():
            return
        rows = []
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                rows.append(
                    (
                        row.get("carrier", ""),
                        row.get("zone", ""),
                        row.get("rto_code", ""),
                    )
                )
        self.upsert_carrier_rto_zone_rows(rows)

    def _load_insurer(self, wb) -> None:
        sheet = self._get_sheet(wb, "insurer")
        for row in self._iter_data_rows(sheet):
            insurer_id = self._int(row[0])
            product_id = self._int(row[1])
            name = self._str(row[2])
            if insurer_id is None or product_id is None or not name:
                self._skip("Insurer")
                continue
            _, created = InsurerMaster.objects.update_or_create(
                insurer_id=insurer_id,
                product_id=product_id,
                defaults={
                    "name": name,
                    "name_normalized": normalize_insurer_name(name),
                },
            )
            self._track("Insurer", created)
