import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone

import requests
from celery import shared_task
from celery.exceptions import Ignore
from django.conf import settings
from django.utils.timezone import now

logger = logging.getLogger(__name__)

POLICY_EXTRACT_QUEUE = "policy_extract"

# Time limits for the heavy parse task only (not global Celery config — a
# `CELERY_TASK_*` Django setting would apply the limit to every task, including
# commission_intake). Soft limit raises inside the task for a clean FAILED;
# the hard limit kills a child hung past its own MinerU/Gemini HTTP timeout.
_PARSER_SOFT_TIME_LIMIT = int(os.getenv("POLICY_EXTRACT_SOFT_TIME_LIMIT", "900"))
_PARSER_TIME_LIMIT = int(os.getenv("POLICY_EXTRACT_TIME_LIMIT", "1080"))

# A row is only "stuck" once no running task could still be writing to it, so this
# must stay above the hard time limit (a SIGKILL at _PARSER_TIME_LIMIT leaves no code
# running to mark the row failed — the reconciler is the only thing that can).
_STALE_AFTER_SECONDS = int(os.getenv("POLICY_EXTRACT_STALE_AFTER", "1500"))

# Cap the callback body we persist. The callback receiver is a trust boundary, so
# an oversized (or hostile/MITM'd) response must not bloat the row or freeze the
# operator's browser when the body is rendered in the UI.
_MAX_CALLBACK_BODY_CHARS = 64 * 1024


def _fire_callback(callback_url: str, req, event_type: str) -> None:
    """POST extraction event to callback URL. Failures are logged but don't block task completion."""
    from policy_extract.serializers import PolicyExtractionRequestSerializer

    entity_payload = PolicyExtractionRequestSerializer(req).data
    # Keep only extracted_fields from result
    if entity_payload.get("result"):
        entity_payload["result"] = {
            "extracted_fields": entity_payload["result"].get("extracted_fields")
        }
    # Don't echo a prior callback response back to the receiver
    entity_payload.pop("callback_response", None)
    payload = {
        "id": req.policy_extraction_id,
        "event_type": event_type,
        "entity_type": "policy_extraction_request",
        "entity_payload": entity_payload,
        "event_created_at": datetime.now(timezone.utc).isoformat(),
    }

    callback_response = {
        "status_code": None,
        "body": None,
        "ok": False,
        "error": None,
        "received_at": None,
    }
    try:
        resp = requests.post(callback_url, json=payload, timeout=30)
        callback_response["status_code"] = resp.status_code
        text = resp.text or ""
        if len(text) > _MAX_CALLBACK_BODY_CHARS:
            callback_response["body"] = text[:_MAX_CALLBACK_BODY_CHARS] + "…[truncated]"
            callback_response["body_truncated"] = True
        else:
            try:
                # parse_constant neutralizes NaN/Infinity, which Postgres jsonb rejects.
                callback_response["body"] = resp.json(
                    parse_constant=lambda _const: None
                )
            except ValueError:
                callback_response["body"] = text
        resp.raise_for_status()
        callback_response["ok"] = True
    except Exception as exc:
        callback_response["error"] = str(exc)
        logger.warning("Callback to %s failed: %s", callback_url, exc)

    # Stamp when the attempt concluded (after the response returned / failed).
    callback_response["received_at"] = datetime.now(timezone.utc).isoformat()
    try:
        req.callback_response = callback_response
        req.save(update_fields=["callback_response", "updated_at"])
    except Exception as exc:
        logger.warning("Failed to persist callback response for %s: %s", req.pk, exc)


def _fire_completed_callback(req) -> None:
    """Fire the completion callback, if configured, without ever failing the caller.

    The row is already `completed` by the time this runs; letting a callback error
    escape would send the caller's except-branch straight to FAILED and undo a good
    extraction. _fire_callback swallows request errors itself — this guards the rest
    (serializer, DB write of callback_response).
    """
    callback_url = getattr(settings, "POLICY_EXTRACT_CALLBACK_URL", "")
    if not callback_url:
        return
    try:
        _fire_callback(callback_url, req, "policy_extract.completed")
    except Exception as exc:
        logger.warning("Completion callback for %s raised: %s", req.pk, exc)


# DEPRECATED. _run_parser now owns every status transition (see enqueue_processing_chain).
# These two survive only so chain messages already sitting in the broker at deploy time
# still resolve to a registered task; both are no-ops once the row has moved on. Delete
# them in a follow-up, once no pre-deploy chain can still be in flight.
@shared_task(bind=True, max_retries=3, queue=POLICY_EXTRACT_QUEUE)
def _set_processing_status(self, extraction_request_id: str):
    """Deprecated: set status to processing, unless the parser already moved past it."""
    from policy_extract.models import PolicyExtractionRequest

    try:
        updated = PolicyExtractionRequest.objects.filter(
            id=extraction_request_id,
            status=PolicyExtractionRequest.Status.INITIATED,
        ).update(status=PolicyExtractionRequest.Status.PROCESSING, updated_at=now())
        if not updated:
            logger.info(
                "_set_processing_status: %s already past initiated, skipping",
                extraction_request_id,
            )
        return extraction_request_id
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


@shared_task(
    queue=POLICY_EXTRACT_QUEUE,
    # A child killed mid-extraction (OOM / --max-memory-per-child recycle) requeues
    # the task instead of silently dropping it; _run_parser is idempotent
    # (re-downloads, overwrites result), so re-execution is safe.
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=_PARSER_SOFT_TIME_LIMIT,
    time_limit=_PARSER_TIME_LIMIT,
)
def _run_parser(extraction_request_id: str):
    """Download the uploaded file from storage and run MinervaGeminiParser."""
    from django.core.files.storage import default_storage
    from policy_extract.models import PolicyExtractionRequest
    from policy_extract.services.content_type import (
        is_extractable,
        storage_error_message,
    )
    from policy_extract.services.mineru_gemini_parser import MineruGeminiParser

    try:
        req = PolicyExtractionRequest.objects.get(id=extraction_request_id)

        # acks_late means a child lost after the completion write gets the same message
        # redelivered. Re-running a 60s parse and re-firing the callback would be worse
        # than useless, so an already-finished request is a no-op.
        if req.status == PolicyExtractionRequest.Status.COMPLETED and req.result:
            logger.info(
                "_run_parser: %s already completed, skipping re-parse",
                extraction_request_id,
            )
            return extraction_request_id

        # Owned here rather than in a preceding chain task: the row reads `initiated`
        # while it waits in the queue and `processing` only while actually being parsed.
        req.status = PolicyExtractionRequest.Status.PROCESSING
        req.save(update_fields=["status", "updated_at"])

        # Read the file from MinIO. Both ingestion paths now store the document at
        # submission time (uploads directly; file_link fetched-and-stored while its
        # presigned URL is still valid), so source_url is the normal path. The
        # file_link branch below is a legacy/backfill fallback for any row created
        # before that change — it re-fetches the link, which may already be expired.
        if req.source_url:
            suffix = os.path.splitext(req.original_filename)[-1] or ".pdf"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name
                with default_storage.open(req.source_url, "rb") as src:
                    tmp.write(src.read())
        else:
            file_link = (req.metadata or {}).get("file_link")
            if not file_link:
                raise ValueError("No source_url and no file_link in metadata")
            suffix = os.path.splitext(req.original_filename)[-1] or ".pdf"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name
                resp = requests.get(file_link, timeout=60)
                resp.raise_for_status()
                # An expired link returns the storage service's JSON error
                # envelope (often HTTP 200) instead of the PDF — fail with that
                # message rather than writing the JSON bytes and later reporting
                # the misleading "unsupported file type".
                expired_msg = storage_error_message(
                    resp.content, resp.headers.get("Content-Type", "")
                )
                if expired_msg:
                    # %r keeps an untrusted body from injecting log lines; the
                    # generic message is what lands in req.error for the client.
                    logger.warning(
                        "Linked document unavailable %s: %s | body=%.300r",
                        file_link,
                        expired_msg,
                        resp.content,
                    )
                    raise ValueError(expired_msg)
                tmp.write(resp.content)

            # Save downloaded file to MinIO and update source_url
            storage_key = (
                f"policy_extractions/originals/{req.id}/{req.original_filename}"
            )
            with open(tmp_path, "rb") as f:
                default_storage.save(storage_key, f)
            req.source_url = storage_key
            req.save(update_fields=["source_url"])

        # Create a temp directory for intermediate files
        intermediate_dir = tempfile.mkdtemp()

        try:
            # Guard the parse path for both ingestion routes — the API upload is
            # already validated, but file_link downloads bypass that serializer,
            # so confirm the fetched bytes are actually a PDF before handing them
            # to the PDF-only MinerU endpoint. A clear failure beats an opaque
            # 400, and the finally below still cleans up the temp files.
            with open(tmp_path, "rb") as check:
                head = check.read(16)
            if not is_extractable(req.original_filename, head):
                raise ValueError(
                    f"Unsupported file type for '{req.original_filename}'. "
                    "Only PDF files can be extracted."
                )

            # Run parser — saves .md and _raw.json into intermediate_dir
            parser = MineruGeminiParser(
                api_key=os.getenv("GOOGLE_API_KEY"), intermediate_dir=intermediate_dir
            )
            result = parser.parse_pdf(tmp_path, save_intermediate=True)

            # Upload each intermediate file to MinIO
            intermediate_paths = {}
            for fname in os.listdir(intermediate_dir):
                local_path = os.path.join(intermediate_dir, fname)
                storage_key = (
                    f"policy_extractions/intermediates/{extraction_request_id}/{fname}"
                )
                with open(local_path, "rb") as f:
                    default_storage.save(storage_key, f)
                intermediate_paths[fname] = storage_key

            # Save result + intermediate paths
            result["intermediate_files"] = intermediate_paths
            req.result = result
            req.save(update_fields=["result", "updated_at"])

        finally:
            # Clean up temp PDF and intermediate dir
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            shutil.rmtree(intermediate_dir, ignore_errors=True)

        # Terminal status is written by the same task that produced the result, before
        # the callback — mirroring the failure path below, so a slow or hostile callback
        # receiver can never leave a finished extraction stuck in `processing`.
        req.status = PolicyExtractionRequest.Status.COMPLETED
        req.save(update_fields=["status", "updated_at"])
        _fire_completed_callback(req)

        return extraction_request_id

    except Exception as exc:
        # Mark as failed and stop the chain
        try:
            req = PolicyExtractionRequest.objects.get(id=extraction_request_id)
            req.status = PolicyExtractionRequest.Status.FAILED
            req.error = str(exc)
            req.save(update_fields=["status", "error", "updated_at"])

            callback_url = getattr(settings, "POLICY_EXTRACT_CALLBACK_URL", "")
            if callback_url:
                _fire_callback(callback_url, req, "policy_extract.failed")
        except Exception:
            pass
        raise Ignore()


@shared_task(bind=True, max_retries=3, queue=POLICY_EXTRACT_QUEUE)
def _set_completed_status(self, extraction_request_id: str):
    """Deprecated: _run_parser already marked the row completed and fired the callback."""
    from policy_extract.models import PolicyExtractionRequest

    try:
        req = PolicyExtractionRequest.objects.get(id=extraction_request_id)
        if req.status == PolicyExtractionRequest.Status.COMPLETED:
            return extraction_request_id
        # Only reachable for a pre-deploy chain whose _run_parser predates the inline
        # completion write; finish the job rather than leaving the row processing.
        req.status = PolicyExtractionRequest.Status.COMPLETED
        req.save(update_fields=["status", "updated_at"])
        _fire_completed_callback(req)
        return extraction_request_id
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


@shared_task(queue=POLICY_EXTRACT_QUEUE)
def reconcile_stuck_policy_extractions():
    """Periodic sweep: resolve requests left in `processing` by a lost worker.

    _run_parser writes its own terminal status, so the only rows this finds are ones
    where no code was left running to do it — a hard time-limit SIGKILL, an OOM kill,
    or a container restart mid-parse.
    """
    from policy_extract.services.reconcile import reconcile_stuck_requests

    counts = reconcile_stuck_requests(_STALE_AFTER_SECONDS)
    if counts["completed"] or counts["failed"]:
        logger.warning("Reconciled stuck policy extractions: %s", counts)
    return counts


def enqueue_processing_chain(extraction_request_id: str):
    """Enqueue processing for a policy extraction request.

    One self-contained task, deliberately not a chain. _run_parser writes `processing`
    on entry and its own terminal status on exit, so a successful parse can never be
    separated from its status write by a lost broker message — the failure mode that
    used to strand rows in `processing` forever when the third chain link went missing.
    """
    _run_parser.apply_async(args=[extraction_request_id], queue=POLICY_EXTRACT_QUEUE)
