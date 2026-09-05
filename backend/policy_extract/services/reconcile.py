"""Repair policy extraction requests stranded in `processing`.

_run_parser writes both its result and its terminal status, so the only rows that reach
here are ones where no code was left running to finish the job: a hard time-limit
SIGKILL, an OOM/`--max-memory-per-child` kill, or a container restart mid-parse. Such a
row is invisible to the caller (no error, and `retry` refuses an in-flight status), so
something has to sweep it.

Shared by the Celery beat task (`policy_extract.tasks.reconcile_stuck_policy_extractions`)
and the `unstick_extractions` management command — one code path, two triggers.
"""

import logging
from datetime import timedelta
from typing import Iterable, Optional

from django.utils.timezone import now

logger = logging.getLogger(__name__)

STUCK_ERROR = (
    "Extraction did not finish: the worker was lost or the task exceeded its hard "
    "time limit before producing a result. Retry the request."
)


def find_stuck_requests(stale_after_seconds: int, ids: Optional[Iterable[str]] = None):
    """Requests sitting in `processing` with no task plausibly still working on them.

    `ids` targets specific rows and bypasses the age filter — the operator has decided
    the row is dead. Without it, only rows untouched for `stale_after_seconds` qualify.
    """
    from policy_extract.models import PolicyExtractionRequest

    qs = PolicyExtractionRequest.objects.filter(
        status=PolicyExtractionRequest.Status.PROCESSING
    )
    if ids:
        return qs.filter(id__in=list(ids))
    cutoff = now() - timedelta(seconds=stale_after_seconds)
    return qs.filter(updated_at__lt=cutoff)


def reconcile_stuck_requests(
    stale_after_seconds: int,
    ids: Optional[Iterable[str]] = None,
    dry_run: bool = False,
) -> dict:
    """Resolve each stuck request to a terminal status. Returns per-outcome counts.

    A row with a `result` had a successful parse whose status write was lost, so it
    becomes `completed` (and fires the callback the caller never got). A row without one
    never produced anything and becomes `failed`.
    """
    from policy_extract.models import PolicyExtractionRequest
    from policy_extract.tasks import _fire_completed_callback

    counts = {"scanned": 0, "completed": 0, "failed": 0, "skipped": 0}

    for req in find_stuck_requests(stale_after_seconds, ids):
        counts["scanned"] += 1
        outcome = "completed" if req.result else "failed"

        if dry_run:
            counts[outcome] += 1
            continue

        # Conditional write: this runs on a different worker than any real in-flight
        # parse, so a row that finished between the select and here must not be
        # clobbered (nor its callback fired twice).
        fields = {"status": outcome, "updated_at": now()}
        if outcome == "failed":
            fields["error"] = STUCK_ERROR
        updated = PolicyExtractionRequest.objects.filter(
            pk=req.pk, status=PolicyExtractionRequest.Status.PROCESSING
        ).update(**fields)

        if not updated:
            counts["skipped"] += 1
            continue

        counts[outcome] += 1
        logger.warning(
            "Unstuck policy extraction %s (%s), last touched %s",
            req.pk,
            outcome,
            req.updated_at,
        )
        if outcome == "completed":
            req.refresh_from_db()
            _fire_completed_callback(req)

    return counts
