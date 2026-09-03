"""Resolve the MIME type of a stored policy document.

`original_filename` is the only type signal for a normal multipart upload, but
`file_link` submissions store the bare `policy_identifier` (e.g. ``PBP10356218``)
with no extension — so ``mimetypes.guess_type`` returns ``None`` and the document
gets mis-served as ``application/octet-stream`` (forced download, no inline
preview). This resolver layers two more signals on top of the filename guess: the
``file_type`` MIME hint captured at submission (stored in ``metadata``) and, as a
last resort, a magic-byte sniff of the file's leading bytes.
"""

import mimetypes
import os

_OCTET_STREAM = "application/octet-stream"

# The file extensions the extraction pipeline can actually parse. MinerU (the
# sole parse path in `_run_parser`) validates by filename extension and only
# accepts PDFs, so a non-PDF upload 400s at the API. This allowlist is distinct
# from `PREVIEW_SAFE_TYPES` below, which governs which types are safe to render
# inline in the browser — a file can be previewable (e.g. a PNG) yet not
# extractable. Kept as a module constant so the API-layer and task-layer guards
# share one source of truth; widen it here if MinerU gains other input types.
EXTRACTABLE_EXTENSIONS = frozenset({".pdf"})

# The MIME types safe to render inline (no same-origin script execution risk).
# `_sniff` recognises exactly this set from magic bytes and the `document` view
# serves exactly this set inline — sharing one frozenset keeps the sniffable
# types and the inline-serve allowlist from drifting apart.
PREVIEW_SAFE_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    }
)


def _sniff(head: bytes) -> str | None:
    """Best-effort content type from a file's first bytes; ``None`` if unknown.

    Recognises only the ``PREVIEW_SAFE_TYPES`` via leading-byte signatures — a
    tiny local check so we don't need python-magic/libmagic (a system
    dependency) to identify a handful of formats.
    """
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def resolve_content_type(instance, head: bytes | None = None) -> str:
    """Best available MIME type for a ``PolicyExtractionRequest``'s document.

    Resolution order: filename extension → ``metadata.file_type`` hint → magic-byte
    sniff of ``head`` (when provided). Falls back to ``application/octet-stream``.

    Pass ``head`` (the file's leading bytes) only where the file is already open —
    callers on the list/serializer path omit it to avoid opening storage per row.
    """
    ct = mimetypes.guess_type(instance.original_filename)[0]
    if not ct and instance.metadata:
        hint = instance.metadata.get("file_type")
        if hint:
            ct = hint
    if (not ct or ct == _OCTET_STREAM) and head:
        ct = _sniff(head) or ct
    return ct or _OCTET_STREAM


def is_extractable(filename: str, head: bytes | None = None) -> bool:
    """Whether a file can be sent to the extraction pipeline (PDF-only today).

    Two signals, applied so both ingestion routes work:

    - A **present** extension must be in ``EXTRACTABLE_EXTENSIONS`` (a ``.jsonl``
      upload is rejected outright). An *absent* extension is not disqualifying —
      the ``file_link`` route stores the bare ``policy_identifier`` (e.g.
      ``PBP10356218``) as the filename, so a valid PDF fetched by link has no
      extension to check.
    - When ``head`` (the leading bytes) is available it is authoritative: the
      bytes must start with ``%PDF-``. This catches a non-PDF renamed to ``.pdf``
      and lets an extensionless ``file_link`` download through on its content.

    With no ``head`` to sniff, only a name that already declares a supported
    extension passes. The upload serializer and the task both pass ``head``.
    """
    ext = os.path.splitext(filename or "")[-1].lower()
    if ext and ext not in EXTRACTABLE_EXTENSIONS:
        return False
    if head is not None:
        return head.startswith(b"%PDF-")
    return ext in EXTRACTABLE_EXTENSIONS
