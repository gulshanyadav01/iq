from celery import shared_task, chain

POLICY_EXTRACT_QUEUE = "policy_extract"


@shared_task(bind=True, max_retries=3, queue=POLICY_EXTRACT_QUEUE)
def _set_processing_status(self, extraction_request_id: str):
    """Set status to processing."""
    from policy_extract.models import PolicyExtractionRequest

    try:
        extraction_request = PolicyExtractionRequest.objects.get(id=extraction_request_id)
        extraction_request.status = "processing"
        extraction_request.save()
        return extraction_request_id
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


@shared_task(bind=True, max_retries=3, queue=POLICY_EXTRACT_QUEUE)
def _placeholder_process(self, extraction_request_id: str):
    """Placeholder processing task."""
    from policy_extract.models import PolicyExtractionRequest

    try:
        extraction_request = PolicyExtractionRequest.objects.get(id=extraction_request_id)
        # TODO: Implement actual policy extraction logic
        extraction_request.result = {"status": "processed"}
        extraction_request.save()
        return extraction_request_id
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


@shared_task(bind=True, max_retries=3, queue=POLICY_EXTRACT_QUEUE)
def _set_completed_status(self, extraction_request_id: str):
    """Set status to completed."""
    from policy_extract.models import PolicyExtractionRequest

    try:
        extraction_request = PolicyExtractionRequest.objects.get(id=extraction_request_id)
        extraction_request.status = "completed"
        extraction_request.save()
        return extraction_request_id
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


def enqueue_processing_chain(extraction_request_id: str):
    """Enqueue the processing chain for a policy extraction request."""
    processing_chain = chain(
        _set_processing_status.si(extraction_request_id),
        _placeholder_process.si(extraction_request_id),
        _set_completed_status.si(extraction_request_id),
    )
    processing_chain.apply_async()
