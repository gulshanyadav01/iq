"""Classify a raw policy-extraction failure message into a stable bucket.

Failures land in ``PolicyExtractionRequest.error`` as ``str(exception)`` (see
``policy_extract/tasks.py``). That free text is not groupable on its own, so this
module maps it onto a small, fixed set of ``ErrorCategory`` values used by the
list endpoint's ``buckets`` breakdown and the ``error_category`` filter.

The function is intentionally dependency-free (no Django, no model imports) so it
can be called from the task, from a data migration's backfill, and from unit
tests. It matches on stable substrings already emitted in ``tasks.py`` and
``services/content_type.py`` — this file is the single place to extend when a new
failure mode appears; an unrecognised message falls through to ``other`` rather
than raising.
"""

# Ordered most-specific first. Each entry is (category, [lowercase substrings]);
# the first category with any substring found in the (lowercased) message wins.
# Values must match ``PolicyExtractionRequest.ErrorCategory`` on the model.
_RULES: list[tuple[str, list[str]]] = [
    # storage_error_message() text for an expired/invalid presigned link.
    ("expired_link", ["link is no longer valid", "has expired"]),
    # Task guard + create serializer for a non-PDF upload/download, and the
    # pre-MinerU precheck_pdf() rejections (encrypted/corrupt, page or size cap).
    (
        "unsupported_file",
        [
            "unsupported file type",
            "not a valid pdf",
            "files are supported",
            "encrypted or unreadable",
            "pages; limit is",
            "mb; limit is",
        ],
    ),
    # Neither a stored file nor a link to fetch.
    ("missing_source", ["no source_url and no file_link"]),
    # Celery soft/hard time limit tripped mid-parse.
    ("timeout", ["softtimelimitexceeded", "timelimitexceeded", "time limit"]),
    # requests/HTTP download failures (link fetch, MinIO read).
    (
        "download_failed",
        ["connectionerror", "httperror", "max retries", "timed out", "connection"],
    ),
    # MinerU / Gemini parse-path failures.
    ("parser_failed", ["mineru", "gemini", "parse", "extraction failed"]),
]


def classify_error(message: str | None) -> str:
    """Map a raw failure message to an ``ErrorCategory`` value.

    Returns ``"other"`` for a blank message or one that matches no rule.
    """
    if not message:
        return "other"
    text = message.lower()
    for category, needles in _RULES:
        if any(needle in text for needle in needles):
            return category
    return "other"
