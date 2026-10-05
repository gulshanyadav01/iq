"""PDF → Markdown executor (Minerva integration).

Calls the Minerva API with ``return_content_list=true`` so each markdown block
carries bbox + page_idx metadata for downstream citation highlighting.
Writes ``md.md`` and ``blocks.json`` to MinIO. Returns refs + ``has_bbox`` flag.

Hard limits (v1, per F6 / design Pass 2 in the approved plan):
  - 25MB upload cap   — enforced server-side in the create serializer
  - 50-page cap       — enforced HERE before the Minerva call (precheck_pdf)
  - PDF MIME check    — enforced server-side, and again by precheck_pdf

If Minerva returns no usable content_list, the executor still completes the
step and sets ``has_bbox=False`` so LLM_EXTRACT degrades to "no sentinels, no
chips" instead of failing the whole run.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
from typing import Any

import requests
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from core.services.mineru import (
    MINERU_URL,
    PageLimitExceeded,
    PermanentDocumentError,
    base_form,
    get_session,
    precheck_pdf,
)
from pipelines.serializers.pipeline_run import MAX_FILE_SIZE_BYTES
from pipelines.steps.executor import PrevStepOutput, StepExecutor
from pipelines.steps.registry import register_executor

logger = logging.getLogger(__name__)

MINERVA_API_URL = MINERU_URL
MINERVA_TIMEOUT_SECONDS = int(os.getenv("MINERVA_TIMEOUT_SECONDS", "300"))
MAX_PAGES = int(os.getenv("PIPELINES_MAX_PAGES", "50"))


class MinervaError(Exception):
    """Raised when Minerva returns a non-recoverable error."""


class PageCapExceeded(Exception):
    """Raised when a PDF exceeds the configured page cap."""


@register_executor
class PdfToMdExecutor(StepExecutor):
    step_type = "PDF_TO_MD"

    def run(
        self,
        *,
        step_run: Any,
        prev_outputs: list[PrevStepOutput],
    ) -> dict[str, Any]:
        # --- 1. Locate the uploaded PDF -----------------------------------
        pdf_key = self._find_pdf_key(step_run)
        if not pdf_key:
            raise MinervaError(
                "PDF_TO_MD: no input PDF found on PipelineRun (expected "
                "`inputs.pdf_key` set by /api/pipelines/runs/ create handler)."
            )

        # --- 2. Stream the PDF from MinIO into a multipart upload ---------
        start = time.time()
        with default_storage.open(pdf_key, "rb") as pdf_fp:
            pdf_bytes = pdf_fp.read()
        filename = os.path.basename(pdf_key) or "upload.pdf"

        # Reject what Minerva can never parse before it holds a GPU slot.
        try:
            precheck_pdf(pdf_bytes, max_pages=MAX_PAGES, max_bytes=MAX_FILE_SIZE_BYTES)
        except PageLimitExceeded as exc:
            raise PageCapExceeded(str(exc)) from exc
        except PermanentDocumentError as exc:
            raise MinervaError(str(exc)) from exc

        files = {"files": (filename, io.BytesIO(pdf_bytes), "application/pdf")}
        # middle.json carries per-block bbox in PDF point space — the only
        # reliable source for citation highlights. content_list is flat
        # reading-order but Minerva drops bbox on OCR-heavy tabular PDFs.
        data = base_form(return_middle_json="true", return_content_list="true")

        logger.info(
            "PDF_TO_MD: calling Minerva pipeline_run=%s pdf_key=%s bytes=%d",
            step_run.pipeline_run_id,
            pdf_key,
            len(pdf_bytes),
        )

        try:
            response = get_session().post(
                MINERVA_API_URL,
                files=files,
                data=data,
                headers={"accept": "application/json"},
                timeout=MINERVA_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            raise MinervaError(f"Minerva request failed: {exc}") from exc

        # --- 3. Extract md_content + content_list -------------------------
        try:
            payload = response.json()
        except ValueError:
            # Older Minerva builds returned raw markdown; without content_list
            # we cannot do citation highlighting — degrade.
            return self._persist(
                step_run,
                md_content=response.text,
                content_list=None,
                started_at_ts=start,
            )

        md_content, content_list, middle_json = self._parse_minerva_payload(payload)

        # Prefer middle.json (carries bbox in PDF points). Build a flat block
        # list with per-block bbox + text + html so downstream LLM_EXTRACT and
        # the PDF highlight overlay both work. Fall back to content_list when
        # middle.json is absent.
        blocks_from_middle = self._blocks_from_middle_json(middle_json)
        if blocks_from_middle:
            content_list = blocks_from_middle

        return self._persist(
            step_run,
            md_content=md_content or "",
            content_list=content_list,
            started_at_ts=start,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_pdf_key(self, step_run: Any) -> str | None:
        """Read the uploaded PDF storage key from the parent PipelineRun.inputs."""
        run = step_run.pipeline_run
        inputs = run.inputs or {}
        return inputs.get("pdf_key") or run.source_url

    def _parse_minerva_payload(
        self, payload: dict[str, Any]
    ) -> tuple[str, list[dict[str, Any]] | None, dict[str, Any] | None]:
        """Pull md_content + content_list + middle_json out of the (sometimes nested) response."""
        md_content = ""
        content_list: list[dict[str, Any]] | None = None
        middle_json: dict[str, Any] | None = None

        # Standard response shape: {"results": {"<filename>": {"md_content": "...", "content_list": [...], "middle_json": {...}}}}
        results = payload.get("results")
        if isinstance(results, dict) and results:
            first = next(iter(results.values()))
            if isinstance(first, dict):
                md_content = first.get("md_content") or first.get("md") or ""
                cl = first.get("content_list")
                if isinstance(cl, list):
                    content_list = cl
                mj = first.get("middle_json") or first.get("middle")
                if isinstance(mj, str):
                    try:
                        mj = json.loads(mj)
                    except json.JSONDecodeError:
                        mj = None
                if isinstance(mj, dict):
                    middle_json = mj

        # Fallback: top-level keys
        if not md_content:
            for key in ("md_content", "md", "markdown", "content"):
                if isinstance(payload.get(key), str):
                    md_content = payload[key]
                    break
        if content_list is None and isinstance(payload.get("content_list"), list):
            content_list = payload["content_list"]
        if middle_json is None:
            mj = payload.get("middle_json") or payload.get("middle")
            if isinstance(mj, str):
                try:
                    mj = json.loads(mj)
                except json.JSONDecodeError:
                    mj = None
            if isinstance(mj, dict):
                middle_json = mj

        return md_content, content_list, middle_json

    # Tables that occupy >this fraction of the page get row-split so highlight
    # citations land on individual rows instead of the whole page.
    _TABLE_DOMINANCE_THRESHOLD = 0.5

    def _blocks_from_middle_json(
        self, middle_json: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        """Walk Minerva middle.json into a flat content_list shape with bboxes.

        middle.json schema (Minerva docs):
          {"pdf_info": [
             {"page_idx": int,
              "page_size": [w, h] (optional),
              "para_blocks": [
                 {"type": "title|text|table|image|equation|...",
                  "bbox": [x0,y0,x1,y1] (PDF points),
                  "lines": [{"spans": [{"type":"text","content":"..."}, ...]}, ...],
                  "html": "..." (table only),
                  "img_caption": "..." (image only)
                 }],
              "tables": [...],   # sometimes split out of para_blocks
              "images": [...]}
          ]}
        """
        if not middle_json:
            return []
        pages = middle_json.get("pdf_info")
        if not isinstance(pages, list):
            return []

        out: list[dict[str, Any]] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_idx = page.get("page_idx", 0)
            page_size = (
                page.get("page_size")
                if isinstance(page.get("page_size"), list)
                else None
            )
            for source_key in ("para_blocks", "tables", "images", "discarded_blocks"):
                source = page.get(source_key)
                if not isinstance(source, list):
                    continue
                for block in source:
                    flat = self._flatten_middle_block(block, page_idx)
                    if not flat:
                        continue
                    out.extend(self._maybe_split_table(flat, page_size))
        return out

    def _maybe_split_table(
        self,
        block: dict[str, Any],
        page_size: list[Any] | None,
    ) -> list[dict[str, Any]]:
        """If a table block dominates the page, split it row-wise so each <tr>
        gets its own bbox for citation highlights. Otherwise return [block]."""
        if (
            block.get("type") != "table"
            or not block.get("html")
            or not block.get("bbox")
        ):
            return [block]
        bbox = block["bbox"]
        if not (page_size and len(page_size) == 2):
            return [block]
        try:
            page_w = float(page_size[0])
            page_h = float(page_size[1])
        except (TypeError, ValueError):
            return [block]
        page_area = page_w * page_h
        if page_area <= 0:
            return [block]
        bx0, by0, bx1, by1 = bbox
        block_area = max(0.0, (bx1 - bx0) * (by1 - by0))
        if block_area / page_area < self._TABLE_DOMINANCE_THRESHOLD:
            return [block]

        # Match each top-level <tr>...</tr>. Stops at the next <tr> or end of table.
        rows = re.findall(r"<tr\b[\s\S]*?</tr>", block["html"], flags=re.IGNORECASE)
        if len(rows) < 2:
            return [block]

        # All per-row blocks share the parent table bbox. Synthesizing per-row
        # sub-bboxes (equal or text-weighted) misaligned because real PDF row
        # heights vary unpredictably. Whole-table highlight on any row click
        # is approximate but never wrong. Per-row split kept so LLM citations
        # still resolve at row granularity in the MD pane.
        out: list[dict[str, Any]] = []
        for idx, row_html in enumerate(rows):
            out.append(
                {
                    "type": "table",
                    "text": "",
                    "html": f"<table>{row_html}</table>",
                    "latex": None,
                    "page_idx": block["page_idx"],
                    "bbox": [bx0, by0, bx1, by1],
                    "row_idx": idx,
                }
            )
        return out

    def _flatten_middle_block(
        self, block: dict[str, Any] | None, page_idx: int
    ) -> dict[str, Any] | None:
        """Reduce a middle.json block to {type,text,html,latex,bbox,page_idx}.

        Content lives at ``block.blocks[].lines[].spans[]``. Spans carry:
          - type=text    → ``content`` (text)
          - type=table   → ``html``
          - type=equation/interline_equation/inline_equation → ``latex``/``content``
          - type=image   → ``image_path``
        """
        if not isinstance(block, dict):
            return None
        bbox = block.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            bbox = None

        btype = block.get("type") or "text"
        html = block.get("html")
        latex = block.get("latex")
        text_parts: list[str] = []
        image_path: str | None = None

        inner_blocks = block.get("blocks")
        if not isinstance(inner_blocks, list) or not inner_blocks:
            inner_blocks = [block]

        for inner in inner_blocks:
            if not isinstance(inner, dict):
                continue
            lines = inner.get("lines")
            if not isinstance(lines, list):
                continue
            for line in lines:
                if not isinstance(line, dict):
                    continue
                spans = line.get("spans")
                if not isinstance(spans, list):
                    continue
                for span in spans:
                    if not isinstance(span, dict):
                        continue
                    span_type = span.get("type") or "text"
                    if span_type == "table":
                        if not html:
                            html = span.get("html")
                    elif span_type in (
                        "equation",
                        "interline_equation",
                        "inline_equation",
                    ):
                        if not latex:
                            latex = span.get("latex") or span.get("content")
                    elif span_type == "image":
                        if not image_path:
                            image_path = span.get("image_path")
                    else:
                        content = span.get("content") or span.get("text") or ""
                        if content:
                            text_parts.append(content)

        text = " ".join(text_parts).strip()
        if not text and btype == "image":
            text = block.get("img_caption") or block.get("caption") or image_path or ""

        return {
            "type": btype,
            "text": text,
            "html": html,
            "latex": latex,
            "page_idx": page_idx,
            "bbox": bbox,
        }

    def _persist(
        self,
        step_run: Any,
        *,
        md_content: str,
        content_list: list[dict[str, Any]] | None,
        started_at_ts: float,
    ) -> dict[str, Any]:
        """Write md.md + blocks.json to MinIO and build the output dict."""
        base = f"pipelines/runs/{step_run.pipeline_run_id}/steps/{step_run.id}"
        md_key = f"{base}/md.md"
        blocks_key = f"{base}/blocks.json"

        default_storage.save(md_key, ContentFile(md_content.encode("utf-8")))

        # Normalize content_list — assign stable block_id per block so downstream
        # LLM citations have something to reference even if Minerva re-orders.
        normalized_blocks: list[dict[str, Any]] = []
        if content_list:
            for idx, block in enumerate(content_list):
                normalized = {
                    "block_id": f"b{idx}",
                    "type": block.get("type", "text"),
                    "text": block.get("text", "") or "",
                    "html": block.get("html"),
                    "latex": block.get("latex"),
                    "page_idx": block.get("page_idx", 0),
                    "bbox": block.get("bbox"),
                }
                if block.get("row_idx") is not None:
                    normalized["row_idx"] = block["row_idx"]
                normalized_blocks.append(normalized)

        # Fallback: Minerva returned no usable content_list but we have markdown.
        # Synthesize blocks so MdViewer renders something (no bbox, no highlights —
        # has_bbox stays False, citation chips stay hidden).
        if not normalized_blocks and md_content.strip():
            if "<" in md_content and ">" in md_content:
                # HTML-heavy md (e.g. Minerva flattened a tabular PDF to <table>).
                # Split on table boundaries; classify each chunk.
                parts = re.split(
                    r"(<table[\s\S]*?</table>)", md_content, flags=re.IGNORECASE
                )
                idx = 0
                for part in parts:
                    chunk = part.strip()
                    if not chunk:
                        continue
                    is_table = chunk.lower().startswith("<table")
                    normalized_blocks.append(
                        {
                            "block_id": f"b{idx}",
                            "type": "table" if is_table else "html",
                            "text": "" if is_table else chunk,
                            "html": chunk if is_table else chunk,
                            "latex": None,
                            "page_idx": 0,
                            "bbox": None,
                        }
                    )
                    idx += 1
            else:
                paragraphs = [p.strip() for p in md_content.split("\n\n") if p.strip()]
                for idx, para in enumerate(paragraphs):
                    normalized_blocks.append(
                        {
                            "block_id": f"b{idx}",
                            "type": "text",
                            "text": para,
                            "html": None,
                            "latex": None,
                            "page_idx": 0,
                            "bbox": None,
                        }
                    )
            # Single-block degenerate case (no `\n\n`, no `<`): keep entire md
            # as one text block.
            if not normalized_blocks:
                normalized_blocks.append(
                    {
                        "block_id": "b0",
                        "type": "text",
                        "text": md_content.strip(),
                        "html": None,
                        "latex": None,
                        "page_idx": 0,
                        "bbox": None,
                    }
                )

        default_storage.save(
            blocks_key,
            ContentFile(json.dumps(normalized_blocks).encode("utf-8")),
        )

        has_bbox = bool(
            normalized_blocks and any(b.get("bbox") for b in normalized_blocks)
        )
        page_count = (
            max((b["page_idx"] for b in normalized_blocks), default=-1) + 1
            if normalized_blocks
            else 0
        )

        elapsed = time.time() - started_at_ts
        logger.info(
            "PDF_TO_MD: done pipeline_run=%s blocks=%d pages=%d has_bbox=%s elapsed=%.1fs",
            step_run.pipeline_run_id,
            len(normalized_blocks),
            page_count,
            has_bbox,
            elapsed,
        )

        return {
            "md_key": md_key,
            "blocks_key": blocks_key,
            "md_chars": len(md_content),
            "page_count": page_count,
            "blocks_count": len(normalized_blocks),
            "has_bbox": has_bbox,
            "elapsed_s": round(elapsed, 2),
        }
