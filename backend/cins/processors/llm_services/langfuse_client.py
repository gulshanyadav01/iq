"""
Langfuse tracing client for CINS LLM pipeline.

Trace hierarchy:
  Session  = CommissionDocument.id  (groups all passes for one document)
  Trace    = one per ExtractionPass  (document_id + pass_number + sheet_name)
  Generation = one per LLM API call within a pass

Usage pattern (set before each pass, clear after flush):

    set_trace_context(
        session_id="<document_uuid>",
        trace_id="<document_uuid>-pass1-SheetName",
        trace_name="pass-1-metadata",
        metadata={"document_id": ..., "pass": 1, "sheet": ...},
    )
    # ... call LLM ...
    flush()
    clear_trace_context()
"""

import hashlib
import logging
import os
from contextvars import ContextVar
from typing import Optional

logger = logging.getLogger(__name__)

_session_id: ContextVar[Optional[str]] = ContextVar("langfuse_session_id", default=None)
_trace_id: ContextVar[Optional[str]] = ContextVar("langfuse_trace_id", default=None)
_trace_name: ContextVar[Optional[str]] = ContextVar("langfuse_trace_name", default=None)
_metadata: ContextVar[Optional[dict]] = ContextVar("langfuse_metadata", default=None)

_client = None


def get_langfuse():
    """Return a singleton Langfuse client, or None if Langfuse is not configured."""
    global _client
    if _client is not None:
        return _client

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return None

    try:
        from langfuse import Langfuse

        _client = Langfuse()
        logger.info("Langfuse tracing enabled (host=%s)", os.environ.get("LANGFUSE_HOST"))
    except Exception as exc:
        logger.warning("Langfuse init failed — tracing disabled: %s", exc)
        _client = None

    return _client


def make_pass_trace_name(document_id: str, pass_number: int, sheet_name: str = "", attachment_name: str = "") -> str:
    """Derive a human-readable trace name for a single extraction pass."""
    parts = [document_id, f"pass{pass_number}"]
    if attachment_name:
        parts.append(attachment_name)
    if sheet_name:
        parts.append(sheet_name)
    return "-".join(parts)


def make_pass_trace_id(document_id: str, pass_number: int, sheet_name: str = "", attachment_name: str = "") -> str:
    """
    Derive a stable 32-char hex trace ID (OTel format required by Langfuse v4).
    Deterministic: same inputs always produce the same ID.
    """
    name = make_pass_trace_name(document_id, pass_number, sheet_name, attachment_name)
    return hashlib.md5(name.encode()).hexdigest()


def set_trace_context(
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    trace_name: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """
    Set Langfuse context for the current execution scope.
    Call before each LLM pass; every generation span within that scope
    will be attached to this trace + session.
    """
    _session_id.set(session_id)
    _trace_id.set(trace_id)
    _trace_name.set(trace_name)
    _metadata.set(metadata)


def clear_trace_context() -> None:
    """Reset all context vars. Call after flush() at end of task."""
    _session_id.set(None)
    _trace_id.set(None)
    _trace_name.set(None)
    _metadata.set(None)


def get_trace_context() -> dict:
    return {
        "session_id": _session_id.get(),
        "trace_id": _trace_id.get(),
        "trace_name": _trace_name.get(),
        "metadata": _metadata.get(),
    }


def start_generation(name: str, model: str, input: Optional[str], metadata: Optional[dict] = None):
    """
    Start a Langfuse generation observation attached to the current trace context.
    Returns the observation object (call .end() when done), or None if Langfuse is disabled.
    Uses TraceContext to attach to the trace_id set via set_trace_context().
    """
    client = get_langfuse()
    if not client:
        return None
    ctx = get_trace_context()
    trace_id = ctx.get("trace_id")
    if not trace_id:
        return None
    try:
        from langfuse.types import TraceContext

        return client.start_observation(
            trace_context=TraceContext(trace_id=trace_id),
            name=name,
            as_type="generation",
            model=model,
            input=input,
            metadata=metadata or ctx.get("metadata"),
        )
    except Exception as exc:
        logger.warning("Langfuse start_generation failed: %s", exc)
        return None


def flush() -> None:
    """Flush buffered spans. Must be called before a Celery task returns."""
    client = get_langfuse()
    if client:
        try:
            client.flush()
        except Exception as exc:
            logger.warning("Langfuse flush error: %s", exc)
