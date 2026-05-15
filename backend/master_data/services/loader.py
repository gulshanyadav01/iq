"""Idempotent loader for the Master Mapper Excel workbook.

Used by both the ``load_master_data`` management command and the upload API.
Both call :meth:`MasterDataLoader.load_workbook` so the ingest semantics stay
identical no matter how the workbook arrives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import openpyxl
from django.db import transaction

from master_data.models import (
    CarrierMaster,
    FuelTypeMaster,
    InsurerMaster,
    PlanTypeMaster,
    PolicyTypeMaster,
    VehicleClassMaster,
    VehicleMaster,
    VehicleSubClassMaster,
)
from master_data.services.canonical import (
    VEHICLE_SUBCLASS_CANONICAL,
    normalize_insurer_name,
)


@dataclass
class SheetResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0

    def as_dict(self) -> dict:
        return {"created": self.created, "updated": self.updated, "skipped": self.skipped}


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
