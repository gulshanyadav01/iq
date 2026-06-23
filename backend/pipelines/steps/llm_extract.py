"""LLM extraction executor (Gemini JSON-mode + citation validation).

Reads MD + blocks.json from the previous PDF_TO_MD StepRun, prepends ``[[bN]]``
sentinel markers to each block (per F6: TABLE/IMAGE/EQUATION blocks get explicit
type prefixes), calls Gemini with ``response_mime_type="application/json"``, and
validates that every emitted ``_citations`` block ID exists. Per F5, parsing has
a 3-tier fallback for Gemini quirks.

Returns a dict with extracted_fields, citations (filtered to valid block IDs),
dropped_citations (for debugging), token counts, cost_usd, and parse_status
('ok' | 'recovered' | 'degraded').
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from django.core.files.storage import default_storage

from pipelines.steps.executor import PrevStepOutput, StepExecutor
from pipelines.steps.registry import register_executor

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.1-flash-lite"

# Gemini pricing per 1M tokens (input, output). Mirrors policy_extract values
# so the demo's cost dashboard matches the production extractor's accounting.
PRICING = {
    "gemini-3.1-flash-lite": (0.075, 0.30),
    "gemini-3.5-flash": (0.15, 0.60),
    "gemini-1.5-pro": (1.25, 5.00),
}


class LlmExtractError(Exception):
    """Raised when the LLM step cannot recover at all (e.g. Gemini auth)."""


@register_executor
class LlmExtractExecutor(StepExecutor):
    step_type = "LLM_EXTRACT"

    def run(
        self,
        *,
        step_run: Any,
        prev_outputs: list[PrevStepOutput],
    ) -> dict[str, Any]:
        # --- 1. Resolve config + prev PDF_TO_MD output --------------------
        config = step_run.config_snapshot or {}
        user_instruction = (config.get("user_instruction") or "").strip()
        model_name = config.get("model") or DEFAULT_MODEL

        pdf_step = self._find_pdf_step(prev_outputs)
        if pdf_step is None:
            raise LlmExtractError(
                "LLM_EXTRACT requires a prior PDF_TO_MD step in the chain."
            )
        pdf_output = pdf_step.output

        # --- 2. Read MD + blocks.json from MinIO --------------------------
        md_content = self._read_text(pdf_output["md_key"])
        blocks = self._read_json(pdf_output["blocks_key"])
        has_bbox = bool(pdf_output.get("has_bbox"))

        # --- 3. Compose final prompt --------------------------------------
        # System contract (JSON shape, citations, block markers) is fixed in
        # _SYSTEM_PROMPT. The user-provided instruction is sandwiched between
        # the system contract and the document so the model treats it as a
        # narrowing/refinement rather than a replacement.
        if has_bbox and blocks:
            md_for_llm = self._format_blocks_with_sentinels(blocks)
        else:
            md_for_llm = md_content

        if user_instruction:
            scope_block = _SCOPED_INTRO.format(user_instruction=user_instruction)
        else:
            scope_block = _DEFAULT_INTRO
        prompt = f"{scope_block}\n\n{_OUTPUT_CONTRACT}\n\nDOCUMENT:\n{md_for_llm}\n"

        # --- 4. Call Gemini -----------------------------------------------
        start = time.time()
        response_text, input_tokens, output_tokens = self._call_gemini(
            model_name, prompt
        )
        elapsed = time.time() - start

        # --- 5. Robust JSON parsing (F5) ----------------------------------
        parsed, parse_status = self._parse_json_robust(
            response_text, model_name=model_name, original_prompt=prompt
        )

        fields = parsed.get("fields") if isinstance(parsed, dict) else None
        raw_citations = parsed.get("_citations") if isinstance(parsed, dict) else None

        # --- 6. Validate citations (filter hallucinated IDs) --------------
        valid_block_ids = {b["block_id"] for b in blocks} if blocks else set()
        citations, dropped = self._validate_citations(raw_citations, valid_block_ids)

        # --- 7. Persist raw LLM output for debugging ----------------------
        base = f"pipelines/runs/{step_run.pipeline_run_id}/steps/{step_run.id}"
        llm_raw_key = f"{base}/llm_raw.json"
        llm_extracted_key = f"{base}/llm_extracted.json"

        from django.core.files.base import ContentFile

        default_storage.save(
            llm_raw_key,
            ContentFile(
                json.dumps(
                    {
                        "model": model_name,
                        "raw_response_text": response_text,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    }
                ).encode("utf-8")
            ),
        )

        final_extracted = {
            "fields": fields or {},
            "citations": citations,
            "dropped_citations": dropped,
            "parse_status": parse_status,
        }
        default_storage.save(
            llm_extracted_key,
            ContentFile(json.dumps(final_extracted).encode("utf-8")),
        )

        # --- 8. Cost accounting -------------------------------------------
        in_price, out_price = PRICING.get(model_name, (0.0, 0.0))
        cost_usd = (
            (input_tokens / 1_000_000) * in_price
            + (output_tokens / 1_000_000) * out_price
        )

        logger.info(
            "LLM_EXTRACT: done pipeline_run=%s fields=%d cited=%d dropped=%d "
            "parse_status=%s input_tok=%d output_tok=%d cost=$%.5f elapsed=%.1fs",
            step_run.pipeline_run_id,
            len(fields or {}),
            len(citations),
            len(dropped),
            parse_status,
            input_tokens,
            output_tokens,
            cost_usd,
            elapsed,
        )

        return {
            "llm_raw_key": llm_raw_key,
            "llm_extracted_key": llm_extracted_key,
            "extracted_fields": fields or {},
            "citations": citations,
            "dropped_citations": dropped,
            "parse_status": parse_status,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 6),
            "elapsed_s": round(elapsed, 2),
            "model": model_name,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_pdf_step(
        self, prev_outputs: list[PrevStepOutput]
    ) -> PrevStepOutput | None:
        for step in prev_outputs:
            if step.step_type == "PDF_TO_MD":
                return step
        return None

    def _read_text(self, key: str) -> str:
        with default_storage.open(key, "rb") as fp:
            return fp.read().decode("utf-8", errors="replace")

    def _read_json(self, key: str) -> list[dict[str, Any]]:
        with default_storage.open(key, "rb") as fp:
            data = json.loads(fp.read().decode("utf-8"))
        return data if isinstance(data, list) else []

    def _format_blocks_with_sentinels(
        self, blocks: list[dict[str, Any]]
    ) -> str:
        """Per F6: TABLE/IMAGE/EQUATION blocks get explicit type prefixes so the
        LLM understands the citation may resolve to a region, not just a string.
        """
        lines: list[str] = []
        for block in blocks:
            bid = block["block_id"]
            btype = (block.get("type") or "text").lower()
            text = block.get("text") or ""
            html = block.get("html")
            latex = block.get("latex")
            page = block.get("page_idx", 0)

            if btype == "table":
                body = html or text or "(empty table)"
                lines.append(f"[[{bid}]] [TABLE on page {page + 1}]\n{body}\n")
            elif btype == "image":
                caption = text or "(no caption)"
                lines.append(f"[[{bid}]] [IMAGE on page {page + 1}] {caption}\n")
            elif btype in ("equation", "interline_equation"):
                body = latex or text or "(empty equation)"
                lines.append(f"[[{bid}]] [EQUATION] {body}\n")
            else:
                lines.append(f"[[{bid}]] {text}\n")
        return "\n".join(lines)

    def _call_gemini(
        self, model_name: str, prompt: str
    ) -> tuple[str, int, int]:
        from google import genai
        from google.genai import errors as genai_errors
        from google.genai import types as genai_types

        # Parse the flag to a bool: a raw env string like "false" is truthy in
        # Python, so passing os.getenv(...) directly would enable Vertex for any
        # non-empty value. Public Gemini Developer API is the default.
        use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in ("1", "true", "yes")
        client = genai.Client(vertexai=use_vertex, api_key=os.getenv("GOOGLE_API_KEY"))

        # Retry transient Gemini 5xx (503 UNAVAILABLE, 504 DEADLINE) with
        # exponential backoff. SDK's internal retry has been observed to
        # surface 503 through to the caller during demand spikes.
        max_attempts = 4
        backoff_s = 1.5
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                break
            except genai_errors.ServerError as exc:
                last_exc = exc
                if attempt == max_attempts:
                    raise LlmExtractError(
                        f"Gemini call failed after {max_attempts} attempts: {exc}"
                    ) from exc
                sleep_s = backoff_s * (2 ** (attempt - 1))
                logger.warning(
                    "LLM_EXTRACT: Gemini 5xx (attempt %d/%d), retrying in %.1fs: %s",
                    attempt, max_attempts, sleep_s, exc,
                )
                time.sleep(sleep_s)
            except Exception as exc:
                raise LlmExtractError(f"Gemini call failed: {exc}") from exc

        text = response.text or ""
        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        return text, int(input_tokens), int(output_tokens)

    def _parse_json_robust(
        self, text: str, *, model_name: str, original_prompt: str
    ) -> tuple[Any, str]:
        """F5: tiered fallback for Gemini's occasional JSON quirks."""
        # Tier 1 — direct parse
        try:
            return json.loads(text), "ok"
        except json.JSONDecodeError:
            pass

        # Tier 2 — strip code fences
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
        try:
            return json.loads(stripped), "recovered"
        except json.JSONDecodeError:
            pass

        # Tier 3 — normalize Python-style literals + trailing commas
        normalized = stripped
        normalized = re.sub(r"\bTrue\b", "true", normalized)
        normalized = re.sub(r"\bFalse\b", "false", normalized)
        normalized = re.sub(r"\bNone\b", "null", normalized)
        normalized = re.sub(r"\bNaN\b", "null", normalized)
        normalized = re.sub(r",(\s*[}\]])", r"\1", normalized)
        try:
            return json.loads(normalized), "recovered"
        except json.JSONDecodeError:
            pass

        # Tier 4 — single retry with correction prompt
        try:
            retry_prompt = (
                original_prompt
                + "\n\nYour previous response was not valid JSON. Return strictly valid JSON matching the requested shape."
            )
            retry_text, _, _ = self._call_gemini(model_name, retry_prompt)
            return json.loads(retry_text), "recovered"
        except Exception:
            logger.warning(
                "LLM_EXTRACT: all 4 JSON parse tiers failed; degrading. text_len=%d",
                len(text),
            )
            return {"fields": {}, "_citations": {}}, "degraded"

    def _validate_citations(
        self,
        raw_citations: Any,
        valid_block_ids: set[str],
    ) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
        """Filter the LLM's citations to known block IDs; log drops."""
        citations: dict[str, list[str]] = {}
        dropped: list[dict[str, Any]] = []

        if not isinstance(raw_citations, dict):
            return citations, dropped

        for field, ids in raw_citations.items():
            if not isinstance(ids, list):
                dropped.append({"field": field, "reason": "not_a_list", "value": ids})
                continue
            kept: list[str] = []
            drop_ids: list[str] = []
            for bid in ids:
                if isinstance(bid, str) and bid in valid_block_ids:
                    kept.append(bid)
                else:
                    drop_ids.append(str(bid))
            if kept:
                citations[field] = kept
            if drop_ids:
                dropped.append({"field": field, "dropped_ids": drop_ids})

        return citations, dropped


_DEFAULT_INTRO = """You are a document-extraction assistant. The Markdown below was extracted from a PDF.
Each meaningful block of content is preceded by a marker like [[b12]]; these IDs let you
reference the source location of values you extract.

Extract every clearly-identifiable label-value pair from the document into a flat JSON
object. Field names should be lowercase snake_case derived from the document's labels.
Do not invent fields that aren't visibly present. Skip headers, page numbers, footers,
and decorative text."""


_SCOPED_INTRO = """You are a document-extraction assistant. The Markdown below was extracted from a PDF.
Each meaningful block of content is preceded by a marker like [[b12]]; these IDs let you
reference the source location of values you extract.

THE USER HAS RESTRICTED THE EXTRACTION SCOPE. Extract ONLY the fields the user names
below. Do NOT include any other label-value pairs, even if they are clearly present in
the document. Do NOT include related, adjacent, or contextual fields. If the user asks
for "GSTIN", return ONLY the gstin field -- not state, not address, not registration
type. If the user asks for "invoice number and biller name", return exactly those two
fields and nothing else. The user's list is exhaustive, not illustrative.

USER-REQUESTED FIELDS (verbatim; treat as the complete allow-list):
{user_instruction}

Use lowercase snake_case for the returned field names. If a requested field is not
present in the document, omit it (do not invent a value). Skip headers, page numbers,
footers, and decorative text."""


_OUTPUT_CONTRACT = """Return strictly valid JSON with this exact shape:
{
  "fields": { "<field_name>": "<value as a string>", ... },
  "_citations": { "<field_name>": ["b<id>", ...], ... }
}

Every entry in "fields" MUST have a corresponding entry in "_citations" listing the
block IDs where the value was found. If a value spans multiple blocks, list them all
in document order. Use only block IDs that actually appear in the Markdown below.

If no fields match (empty document or no requested fields present), return
{"fields": {}, "_citations": {}}."""
