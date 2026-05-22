"""Celery orchestration for pipeline runs.

Chain shape::

    _set_processing_status(run)
        -> _execute_step(step_run_id_1)
        -> _execute_step(step_run_id_2)
        -> ...
        -> _set_completed_status(run)

F2 (idempotency): ``_execute_step`` short-circuits if the StepRun is already
``COMPLETED`` so a Celery retry never re-invokes Minerva/Gemini and never
re-writes MinIO objects. Deterministic MinIO keys + the COMPLETED guard make
the chain safe under network blip → autoretry.

F3 (cache reference, not copy): when a StepRun's status is set to
``SKIPPED_CACHED`` upstream (in ``enqueue_pipeline_run`` for a parent_run_id
short-circuit), this module does not execute it — it copies prev_outputs
through and moves on.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import chain, shared_task
from celery.exceptions import Ignore
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def enqueue_pipeline_run(pipeline_run_id: str) -> None:
    """Build a Celery chain from the run's StepRuns and apply_async."""
    from pipelines.models import PipelineRun

    run = PipelineRun.objects.get(id=pipeline_run_id)
    step_run_ids = list(
        run.step_runs.order_by("order").values_list("id", flat=True)
    )

    tasks = [_set_processing_status.si(str(run.id))]
    for sr_id in step_run_ids:
        tasks.append(_execute_step.si(str(sr_id)))
    tasks.append(_set_completed_status.si(str(run.id)))

    chain(*tasks).apply_async()


# ---------------------------------------------------------------------------
# Bookend tasks
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3)
def _set_processing_status(self, pipeline_run_id: str) -> str:
    from pipelines.models import PipelineRun

    try:
        run = PipelineRun.objects.get(id=pipeline_run_id)
        if run.status == PipelineRun.Status.INITIATED:
            run.status = PipelineRun.Status.PROCESSING
            run.save(update_fields=["status", "updated_at"])
        return pipeline_run_id
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


@shared_task(bind=True, max_retries=3)
def _set_completed_status(self, pipeline_run_id: str) -> str:
    from pipelines.models import PipelineRun, StepRun

    try:
        run = PipelineRun.objects.get(id=pipeline_run_id)
        # Build a denormalized result for the UI: latest LLM_EXTRACT output.
        last_extract = (
            run.step_runs.filter(step_type="LLM_EXTRACT")
            .exclude(output__isnull=True)
            .order_by("-order")
            .first()
        )
        if last_extract and last_extract.output:
            run.result = {
                "extracted_fields": last_extract.output.get("extracted_fields", {}),
                "citations": last_extract.output.get("citations", {}),
                "parse_status": last_extract.output.get("parse_status"),
                "model": last_extract.output.get("model"),
                "input_tokens": last_extract.output.get("input_tokens"),
                "output_tokens": last_extract.output.get("output_tokens"),
                "cost_usd": last_extract.output.get("cost_usd"),
            }
        run.status = PipelineRun.Status.COMPLETED
        run.save(update_fields=["status", "result", "updated_at"])
        return pipeline_run_id
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


# ---------------------------------------------------------------------------
# Per-step executor
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=2)
def _execute_step(self, step_run_id: str) -> str:
    """Run one step with idempotency + status bookkeeping.

    F2: If a previous attempt of THIS task already completed the step, return
    early. External API calls (Minerva, Gemini) are NOT idempotent and
    MUST NOT re-fire on Celery retry.
    """
    from pipelines.models import StepRun, PipelineRun
    from pipelines.steps.registry import get_executor
    from pipelines.steps.executor import PrevStepOutput

    try:
        with transaction.atomic():
            step_run = (
                StepRun.objects.select_for_update()
                .select_related("pipeline_run")
                .get(id=step_run_id)
            )

            # Idempotency: skip if already done.
            if step_run.status == StepRun.Status.COMPLETED:
                logger.info(
                    "STEP %s already completed — short-circuit retry",
                    step_run_id,
                )
                return step_run_id

            # Cache short-circuit (F3): a SKIPPED_CACHED StepRun was set up
            # upstream by the create handler to reference its parent's output;
            # we don't execute, just confirm and move on.
            if step_run.status == StepRun.Status.SKIPPED_CACHED:
                logger.info("STEP %s skipped via cache short-circuit", step_run_id)
                return step_run_id

            step_run.status = StepRun.Status.PROCESSING
            step_run.started_at = timezone.now()
            step_run.save(update_fields=["status", "started_at"])

        # --- Execute outside the transaction (external I/O) ---
        prev_outputs = _build_prev_outputs(step_run.pipeline_run_id, step_run.order)
        executor_cls = get_executor(step_run.step_type)
        executor = executor_cls()
        output = executor.run(step_run=step_run, prev_outputs=prev_outputs)

        # --- Persist success ---
        with transaction.atomic():
            step_run = StepRun.objects.select_for_update().get(id=step_run_id)
            step_run.output = output
            step_run.status = StepRun.Status.COMPLETED
            step_run.finished_at = timezone.now()
            step_run.save(
                update_fields=["output", "status", "finished_at", "updated_at"]
            )

        return step_run_id

    except Exception as exc:
        logger.exception("STEP %s failed: %s", step_run_id, exc)
        try:
            sr = StepRun.objects.get(id=step_run_id)
            sr.status = StepRun.Status.FAILED
            sr.error = str(exc)[:4000]
            sr.finished_at = timezone.now()
            sr.save(update_fields=["status", "error", "finished_at", "updated_at"])

            run = sr.pipeline_run
            run.status = PipelineRun.Status.FAILED
            run.error = f"step {sr.order} ({sr.step_type}): {exc}"[:4000]
            run.save(update_fields=["status", "error", "updated_at"])
        except Exception:
            pass
        # Halt the chain — do not let downstream tasks run after a failure.
        raise Ignore()


def _build_prev_outputs(pipeline_run_id: Any, current_order: int):
    """Query prior StepRuns and build PrevStepOutput records for the executor."""
    from pipelines.models import StepRun
    from pipelines.steps.executor import PrevStepOutput

    prior_qs = StepRun.objects.filter(
        pipeline_run_id=pipeline_run_id, order__lt=current_order
    ).order_by("order")

    return [
        PrevStepOutput(
            order=sr.order,
            step_type=sr.step_type,
            output=sr.output or {},
        )
        for sr in prior_qs
    ]
