"""Fetch a linked source document into storage at request time.

The public API accepts a *presigned* URL, typically valid only a few minutes. If
the download were deferred to the Celery task, a queue backlog could push the
task past the URL's expiry and the fetch would 403 — losing the document through
no fault of its own (exactly the failure mode a busy `policy_extract` queue
invites). So we download synchronously while the request is being handled, when
the URL is still fresh, and persist the bytes to storage; the task then reads the
durable stored copy via ``source_url`` and never touches the ephemeral link.

Blocking the request thread here is a deliberate exception to the "async only"
rule: this is a quick transfer bounded by short timeouts, unlike the multi-minute
MinerU/Gemini parse that rightly stays on the queue.
"""

import logging

import requests
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from rest_framework.exceptions import APIException

from policy_extract.services.content_type import (
    is_extractable,
    storage_error_message,
)

logger = logging.getLogger(__name__)

# Short timeouts bound the request-thread download — a presigned link should
# respond promptly; a stalled one must not tie up a gunicorn worker.
_CONNECT_TIMEOUT = 5
_READ_TIMEOUT = 30


class RemoteDocumentUnavailable(APIException):
    status_code = 502
    default_detail = (
        "Could not fetch the document from the provided link "
        "(it may have expired). Retry with a fresh link."
    )
    default_code = "remote_document_unavailable"


class UnsupportedDocument(APIException):
    status_code = 400
    default_detail = (
        "The linked document is not a supported file type. "
        "Only PDF files are supported."
    )
    default_code = "unsupported_document"


def fetch_document_to_storage(file_link: str, storage_key: str, filename: str) -> None:
    """Download ``file_link`` and persist it at ``storage_key`` (PDF-only).

    Raises ``RemoteDocumentUnavailable`` (502) if the link can't be fetched, and
    ``UnsupportedDocument`` (400) if the bytes aren't a PDF — both are DRF
    ``APIException``\\ s, so they surface straight to the client and a bad/expired
    URL is reported at submission instead of hidden behind an async failure
    callback.
    """
    try:
        resp = requests.get(file_link, timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT))
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch linked document %s: %s", file_link, exc)
        raise RemoteDocumentUnavailable() from exc

    content = resp.content
    # An expired presigned link returns the storage service's JSON error envelope
    # (often with HTTP 200, so raise_for_status above didn't catch it) instead of
    # the PDF. Report that as an unavailable link rather than letting the JSON
    # bytes fall through to the PDF gate below and read as "unsupported file type".
    expired_msg = storage_error_message(content, resp.headers.get("Content-Type", ""))
    if expired_msg:
        # Log the raw body with %r (repr escapes control chars, so an untrusted
        # body can't inject log lines); the client only sees the generic message.
        logger.warning(
            "Linked document unavailable %s: %s | body=%.300r",
            file_link,
            expired_msg,
            content,
        )
        raise RemoteDocumentUnavailable(detail=expired_msg)

    # Validate before storing — same PDF-only gate the upload path enforces, run
    # here on the fetched bytes so the link path is guarded identically.
    if not is_extractable(filename, content[:16]):
        raise UnsupportedDocument()

    default_storage.save(storage_key, ContentFile(content))
