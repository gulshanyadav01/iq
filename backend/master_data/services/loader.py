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

from master_data.models import (
    CarrierMaster,
    CarrierProductHierarchyMaster,
    CarrierRTOZoneMaster,
    FuelTypeMaster,
    InsurerMaster,
    PlanTypeMaster,
    PolicyTypeMaster,
    RTOMaster,
    VehicleClassMaster,
    VehicleMaster,
    VehicleSubClassMaster,
)
from master_data.services.canonical import (
    VEHICLE_SUBCLASS_CANONICAL,
    normalize_insurer_name,
)
from master_data.services.rto import state_slug_for_code


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
        ``state_slug`` from the 2-letter code prefix and ``group`` from the
        ``(Group X)`` suffix in the display value; rows whose prefix is unmapped
        (or which lack a code / id / value) are skipped and counted so seeding
        gaps surface in the command output.
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
                state_slug = state_slug_for_code(code)
                if not state_slug:
                    # Unmapped registration prefix — skip loudly via the count.
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
