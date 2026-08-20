"""Regenerate the committed Chola MMV seed CSV from the source workbooks.

The CSV in ``master_data/data/chola_mmv.csv`` is a DERIVED artifact — the
xlsx books beside it are the source of truth. This command is how it gets
regenerated, so the transform is a tested code path rather than a one-off
shell snippet nobody can reproduce.

Run after replacing either source workbook, then commit the CSV::

    python manage.py export_chola_mmv_csv

Line endings are pinned to ``\\n``: Python's csv writer defaults to ``\\r\\n``,
which the repo's mixed-line-ending pre-commit hook rewrites, so an unpinned
writer produces a file that never matches its own regeneration.
"""

from __future__ import annotations

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from master_data.services.chola_parsers import _MMV_COLUMNS, parse_chola_mmv
from master_data.services.loader import CHOLA_MMV_CSV_PATH

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SOURCE_FILENAMES = ("chola_mmv_2w4w_source.xlsx", "chola_mmv_cv_source.xlsx")


class Command(BaseCommand):
    help = (
        "Regenerate master_data/data/chola_mmv.csv from the committed Chola "
        "MMV source workbooks. Deterministic — same inputs, same bytes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--out",
            type=str,
            default=None,
            help=f"Output path. Defaults to {CHOLA_MMV_CSV_PATH}.",
        )

    def handle(self, *args, **options):
        rows = []
        for name in SOURCE_FILENAMES:
            path = DATA_DIR / name
            if not path.exists():
                raise CommandError(f"Source workbook not found at {path}")
            parsed = parse_chola_mmv(path)
            self.stdout.write(f"  {name:32s} {len(parsed):6d} rows")
            rows.extend(parsed)

        # Sorted so a regeneration produces a stable diff rather than
        # reshuffling 22k lines whenever openpyxl changes iteration order.
        rows.sort(key=lambda r: (r["product"], r["make"], r["model"], r["variant"]))

        out = (
            Path(options["out"]).expanduser()
            if options.get("out")
            else CHOLA_MMV_CSV_PATH
        )
        with open(out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=list(_MMV_COLUMNS),
                extrasaction="ignore",  # the parser also emits the *_slug keys
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)

        self.stdout.write(self.style.SUCCESS(f"Wrote {len(rows)} rows to {out}"))
