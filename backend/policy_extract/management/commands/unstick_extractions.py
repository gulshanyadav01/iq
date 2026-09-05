"""Resolve policy extraction requests stranded in `processing`.

Same repair the beat sweep performs, on demand — for clearing a backlog that predates
the sweep, or for a specific row an operator has already written off. Dry run by
default; nothing is written without --apply.

    docker compose exec backend python manage.py unstick_extractions
    docker compose exec backend python manage.py unstick_extractions --apply
    docker compose exec backend python manage.py unstick_extractions --id <uuid> --apply
"""

from django.core.management.base import BaseCommand
from django.utils.timezone import now

from policy_extract.services.reconcile import (
    find_stuck_requests,
    reconcile_stuck_requests,
)
from policy_extract.tasks import _STALE_AFTER_SECONDS


class Command(BaseCommand):
    help = (
        "Resolve policy extraction requests stuck in `processing` to a terminal status."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--stale-after",
            type=int,
            default=_STALE_AFTER_SECONDS,
            help=(
                "Only touch rows untouched for this many seconds "
                f"(default {_STALE_AFTER_SECONDS}, from POLICY_EXTRACT_STALE_AFTER)."
            ),
        )
        parser.add_argument(
            "--id",
            action="append",
            dest="ids",
            help="Target a specific request id, ignoring --stale-after. Repeatable.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually write the repairs. Without this the command only reports.",
        )

    def handle(self, *args, **options):
        stale_after = options["stale_after"]
        ids = options["ids"]
        apply_changes = options["apply"]

        candidates = list(find_stuck_requests(stale_after, ids))
        if not candidates:
            self.stdout.write(self.style.SUCCESS("No stuck extraction requests found."))
            return

        current = now()
        self.stdout.write(f"{len(candidates)} stuck request(s):")
        for req in candidates:
            age = int((current - req.updated_at).total_seconds())
            action = "completed" if req.result else "failed"
            self.stdout.write(
                f"  {req.id}  {req.policy_extraction_id}  age={age}s  "
                f"result={'yes' if req.result else 'no'}  -> {action}"
            )

        counts = reconcile_stuck_requests(stale_after, ids, dry_run=not apply_changes)

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING("Dry run — nothing written. Re-run with --apply.")
            )
            return
        self.stdout.write(self.style.SUCCESS(f"Done: {counts}"))
