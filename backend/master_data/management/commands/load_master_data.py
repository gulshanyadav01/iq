"""Seed/refresh master-data tables from the Master Mapper Excel workbook.

Idempotent: re-run any time the Excel changes. Each row is upserted on its
natural unique key, so existing rows are updated and new rows inserted.

Excel is the source of truth. Admin-edits to master tables get clobbered on
the next reseed unless the same change is also made in the Excel.
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from master_data.services.loader import MasterDataLoader

DEFAULT_FILENAME = "Master_Mapper_Rhobots.xlsx"


class Command(BaseCommand):
    help = (
        "Load/refresh master-data tables from the Master Mapper Excel workbook. "
        "Idempotent — safe to re-run after any Excel update. "
        "Excel is the source of truth; admin edits are overwritten on reseed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default=None,
            help=(
                f"Path to the Excel workbook. "
                f"Defaults to backend/master_data/data/{DEFAULT_FILENAME}."
            ),
        )

    def handle(self, *args, **options):
        path = self._resolve_path(options.get("file"))
        if not path.exists():
            raise CommandError(
                f"Workbook not found at {path}. "
                f"Pass --file PATH to override, or restore the file."
            )

        self.stdout.write(self.style.NOTICE(f"Loading master data from {path}"))
        loader = MasterDataLoader()
        result = loader.load_path(path)

        for sheet_name, sheet_result in result.sheets.items():
            self.stdout.write(
                f"  {sheet_name:16s}  "
                f"created={sheet_result.created:<4d} "
                f"updated={sheet_result.updated:<4d} "
                f"skipped={sheet_result.skipped:<4d}"
            )
        self.stdout.write(self.style.SUCCESS("Master data load complete."))

    @staticmethod
    def _resolve_path(file_arg: str | None) -> Path:
        if file_arg:
            return Path(file_arg).expanduser().resolve()
        # Default: backend/master_data/data/Master_Mapper_Rhobots.xlsx
        # __file__ -> backend/master_data/management/commands/load_master_data.py
        return Path(__file__).resolve().parents[2] / "data" / DEFAULT_FILENAME
