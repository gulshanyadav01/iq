import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone

import requests
from celery import shared_task, chain
from celery.exceptions import Ignore
from django.conf import settings

logger = logging.getLogger(__name__)

POLICY_EXTRACT_QUEUE = "policy_extract"


def _fire_callback(callback_url: str, req, event_type: str) -> None:
    """POST extraction event to callback URL. Failures are logged but don't block task completion."""
    from policy_extract.serializers import PolicyExtractionRequestSerializer

    entity_payload = PolicyExtractionRequestSerializer(req).data
    # Keep only extracted_fields from result
    if entity_payload.get("result"):
        entity_payload["result"] = {
            "extracted_fields": entity_payload["result"].get("extracted_fields")
        }
    payload = {
        "id": req.policy_extraction_id,
        "event_type": event_type,
        "entity_type": "policy_extraction_request",
        "entity_payload": entity_payload,
        "event_created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = requests.post(callback_url, json=payload, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Callback to %s failed: %s", callback_url, exc)


@shared_task(bind=True, max_retries=3, queue=POLICY_EXTRACT_QUEUE)
def _set_processing_status(self, extraction_request_id: str):
    """Set status to processing."""
    from policy_extract.models import PolicyExtractionRequest

    try:
        req = PolicyExtractionRequest.objects.get(id=extraction_request_id)
        req.status = PolicyExtractionRequest.Status.PROCESSING
        req.save(update_fields=["status", "updated_at"])
        return extraction_request_id
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


@shared_task(queue=POLICY_EXTRACT_QUEUE)
def _run_parser(extraction_request_id: str):
    """Download the uploaded file from storage and run MinervaGeminiParser."""
    from django.core.files.storage import default_storage
    from policy_extract.models import PolicyExtractionRequest
    from policy_extract.services.mineru_gemini_parser import MineruGeminiParser

    try:
        req = PolicyExtractionRequest.objects.get(id=extraction_request_id)

        # Download file: from MinIO (source_url) or from external URL (file_link in metadata)
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
                tmp.write(resp.content)

            # Save downloaded file to MinIO and update source_url
            import uuid
            storage_key = f"policy_extractions/originals/{req.id}/{req.original_filename}"
            with open(tmp_path, "rb") as f:
                default_storage.save(storage_key, f)
            req.source_url = storage_key
            req.save(update_fields=["source_url"])

        # Create a temp directory for intermediate files
        intermediate_dir = tempfile.mkdtemp()

        try:
            # Run parser — saves .md and _raw.json into intermediate_dir
            parser = MineruGeminiParser(
                api_key=os.getenv("GOOGLE_API_KEY"),
                intermediate_dir=intermediate_dir
            )
            result = parser.parse_pdf(tmp_path, save_intermediate=True)

            # Upload each intermediate file to MinIO
            intermediate_paths = {}
            for fname in os.listdir(intermediate_dir):
                local_path = os.path.join(intermediate_dir, fname)
                storage_key = f"policy_extractions/intermediates/{extraction_request_id}/{fname}"
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

        return extraction_request_id

    except Exception as exc:
        # Mark as failed and stop the chain
        try:
            req = PolicyExtractionRequest.objects.get(id=extraction_request_id)
            req.status = PolicyExtractionRequest.Status.FAILED
            req.error = str(exc)
            req.save(update_fields=["status", "error", "updated_at"])

            callback_url = getattr(settings, 'POLICY_EXTRACT_CALLBACK_URL', '')
            if callback_url:
                _fire_callback(callback_url, req, "policy_extract.failed")
        except Exception:
            pass
        raise Ignore()


@shared_task(bind=True, max_retries=3, queue=POLICY_EXTRACT_QUEUE)
def _set_completed_status(self, extraction_request_id: str):
    """Set status to completed and fire callback if configured."""
    from policy_extract.models import PolicyExtractionRequest

    try:
        req = PolicyExtractionRequest.objects.get(id=extraction_request_id)
        req.status = PolicyExtractionRequest.Status.COMPLETED
        req.save(update_fields=["status", "updated_at"])

        callback_url = getattr(settings, 'POLICY_EXTRACT_CALLBACK_URL', '')
        if callback_url:
            _fire_callback(callback_url, req, "policy_extract.completed")

        return extraction_request_id
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


def enqueue_processing_chain(extraction_request_id: str):
    """Enqueue the processing chain for a policy extraction request."""
    processing_chain = chain(
        _set_processing_status.si(extraction_request_id),
        _run_parser.si(extraction_request_id),
        _set_completed_status.si(extraction_request_id),
    )
    processing_chain.apply_async(queue=POLICY_EXTRACT_QUEUE)
