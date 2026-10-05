"""Shared MinerU ``/file_parse`` client pieces.

Every MinerU caller (policy_extract, pipelines PDF_TO_MD, CINS extract_pdf) builds
the same multipart form, posts it through the same pooled session, and classifies
failures the same way:

- A transient upstream error (502 while the container restarts, 503 when MinerU's
  limiter is full, 504 past nginx's 300s read timeout, network errors) is
  retryable. Each caller retries it in the way its execution model allows.
- A document MinerU can never parse (encrypted, corrupt, over the page/size cap)
  is rejected by ``precheck_pdf`` *before* it holds a GPU concurrency slot, and
  raises ``PermanentDocumentError`` so callers take their terminal-failure path.

``MINERU_BACKEND`` is env-driven so the server-side cutover to a standalone vLLM
engine (``hybrid-http-client``) is an env flip, not a redeploy. Flip it only in
lockstep with that cutover: ``hybrid-http-client`` fails every request before the
standalone engine exists, and ``hybrid-auto-engine`` against the split workers
starts an extra in-process engine that does not fit in VRAM.
"""

from __future__ import annotations

import os
from typing import Any

import pypdfium2 as pdfium
import requests
from requests.adapters import HTTPAdapter

# `or`, not a getenv default: docker-compose.prod.yml passes these through as
# `${VAR:-}`, so an unset host var arrives as an empty string.
MINERU_URL = (
    os.getenv("MINERVA_API_URL")
    or "https://rhobots.pbpartners.com/api/mineru/file_parse"
)
MINERU_BACKEND = os.getenv("MINERU_BACKEND") or "hybrid-auto-engine"

# nginx's proxy_read_timeout in front of MinerU is 300s; a longer client timeout
# is unreachable and just surfaces as a 504.
MINERU_TIMEOUT_SECONDS = 300

RETRYABLE_STATUS = frozenset({429, 502, 503, 504})

_POOL_SIZE = 16
_session: requests.Session | None = None
_session_pid: int | None = None


def get_session() -> requests.Session:
    """Per-process pooled session (keep-alive through the ALB).

    Created lazily and keyed on the pid so a prefork child never reuses sockets
    opened by its parent.
    """
    global _session, _session_pid
    pid = os.getpid()
    if _session is None or _session_pid != pid:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=_POOL_SIZE, pool_maxsize=_POOL_SIZE)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _session, _session_pid = session, pid
    return _session


def base_form(**overrides: str) -> dict[str, str]:
    """The multipart form fields every caller sends.

    ``server_url`` is deliberately absent: the split MinerU workers supply it via
    ``MINERU_VL_SERVER``, and the swagger placeholder ``"string"`` the callers
    used to send would override that under ``hybrid-http-client``.
    """
    form = {
        "return_middle_json": "false",
        "return_model_output": "false",
        "return_md": "true",
        "return_images": "false",
        "return_content_list": "false",
        "start_page_id": "0",
        "end_page_id": "99999",
        "parse_method": "auto",
        "lang_list": "en",
        "output_dir": "./output",
        "backend": MINERU_BACKEND,
        "table_enable": "true",
        "response_format_zip": "false",
        "formula_enable": "true",
        "force_enable_ocr": "true",
    }
    form.update(overrides)
    return form


def is_retryable(exc: BaseException | None) -> bool:
    """True when ``exc`` (or anything in its ``__cause__`` chain) is transient.

    Walks the chain because callers wrap the requests error (e.g. pipelines'
    ``MinervaError(...) from exc``). A 4xx/500 is permanent: retrying a bad
    request or an unparsable PDF only burns MinerU capacity.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return True
        if isinstance(exc, requests.HTTPError):
            response = exc.response
            if response is not None and response.status_code in RETRYABLE_STATUS:
                return True
        exc = exc.__cause__
    return False


class PermanentDocumentError(Exception):
    """The document can never be parsed by MinerU; do not retry it."""


class PageLimitExceeded(PermanentDocumentError):
    """The PDF is readable but has more pages than the caller's cap."""


def precheck_pdf(
    source: str | bytes,
    *,
    max_pages: int | None = None,
    max_bytes: int | None = None,
) -> int:
    """Validate a PDF locally before it is sent to MinerU. Returns the page count.

    ``source`` is a filesystem path or the raw bytes. Raises
    ``PermanentDocumentError`` (``PageLimitExceeded`` for the page cap) for a file
    that is too large, not a PDF, encrypted/corrupt, or over the page cap.
    """
    if isinstance(source, (bytes, bytearray)):
        size = len(source)
        head = bytes(source[:5])
    else:
        size = os.path.getsize(source)
        with open(source, "rb") as fh:
            head = fh.read(5)

    if max_bytes is not None and size > max_bytes:
        raise PermanentDocumentError(
            f"File is {size / (1024 * 1024):.1f} MB; limit is "
            f"{max_bytes / (1024 * 1024):.0f} MB"
        )
    if head != b"%PDF-":
        raise PermanentDocumentError("File is not a valid PDF (missing %PDF- header)")

    try:
        doc = pdfium.PdfDocument(source)
    except Exception as exc:  # PdfiumError on encrypted / malformed input
        raise PermanentDocumentError(f"PDF is encrypted or unreadable: {exc}") from exc
    try:
        pages = len(doc)
    finally:
        doc.close()

    if max_pages is not None and pages > max_pages:
        raise PageLimitExceeded(f"PDF has {pages} pages; limit is {max_pages}")
    return pages


def response_status(exc: BaseException | None) -> Any:
    """Best-effort HTTP status from ``exc``'s chain, for logging."""
    while exc is not None:
        response = getattr(exc, "response", None)
        if response is not None:
            return response.status_code
        exc = exc.__cause__
    return None
