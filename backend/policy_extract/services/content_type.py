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

_OCTET_STREAM = "application/octet-stream"

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
