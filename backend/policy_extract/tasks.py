import os
import shutil
import tempfile

from celery import shared_task, chain
from celery.exceptions import Ignore

POLICY_EXTRACT_QUEUE = "policy_extract"


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

        # Download file from S3/MinIO to a local temp file
        suffix = os.path.splitext(req.file.name)[-1] or ".pdf"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            with default_storage.open(req.file.name, "rb") as src:
                tmp.write(src.read())

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
        except Exception:
            pass
        raise Ignore()


@shared_task(bind=True, max_retries=3, queue=POLICY_EXTRACT_QUEUE)
def _set_completed_status(self, extraction_request_id: str):
    """Set status to completed."""
    from policy_extract.models import PolicyExtractionRequest

    try:
        req = PolicyExtractionRequest.objects.get(id=extraction_request_id)
        req.status = PolicyExtractionRequest.Status.COMPLETED
        req.save(update_fields=["status", "updated_at"])
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
