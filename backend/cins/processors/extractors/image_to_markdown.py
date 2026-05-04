"""
Vision-LLM dispatch for converting commission grid images to markdown tables.

Used by the indexer for inline-image commission grids (the kind sent as PNG
screenshots inside an Outlook reply). Wraps the existing multimodal interface
that all three LLM service wrappers already implement, so no provider-specific
code lives here — picking a provider is the caller's job.
"""

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_VISION_PROMPT = (
    "Convert this insurance commission grid image to a markdown table.\n\n"
    "RULES:\n"
    "1. Output ONE valid markdown table per distinct grid in the image.\n"
    "2. Preserve every cell value verbatim, including blanks (use empty cells).\n"
    "3. If the source has multi-row headers (e.g. a top row spanning regions "
    "like 'East/West/North/South' and a sub-row of state codes), FLATTEN them "
    "into a single header row. Combine spanning labels with their child "
    "labels: `<sub_label> (<span_label>)`, e.g. `CG (East)`, `MH (West)`. "
    "Never emit two consecutive header rows separated by a `|---|` divider.\n"
    "4. The table MUST follow the shape: one header row, one separator row "
    "of `|---|---|...`, then data rows. Do not put blank rows or extra "
    "separators between data rows.\n"
    "5. Free-text notes / terms below the grid go AFTER the table as plain "
    "lines (no pipes), one note per line.\n"
    "6. If multiple distinct tables are present, precede each with a "
    "`## Table: <descriptive-name>` heading.\n"
    "7. Output ONLY markdown — no commentary, no code fences, no JSON."
)


def extract_table_markdown(image_path: Path, llm_service: Any) -> str:
    """
    Run the vision LLM on a single image and return the markdown it produces.

    Returns the empty string on any failure so the caller can treat "no
    extraction" the same as "no useful tables in the image" without special-
    casing exceptions.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow not installed; skipping vision extraction for %s", image_path)
        return ""

    # Force plain-text output. The Gemini wrapper defaults to
    # response_mime_type="application/json" which would force the model to
    # wrap the markdown in a JSON envelope. Anthropic and OpenAI ignore an
    # unknown config arg gracefully.
    text_config = _build_text_config(llm_service)

    try:
        with Image.open(image_path) as image:
            image.load()
            kwargs = {"config": text_config} if text_config is not None else {}
            return llm_service.generate_content_multimodal(_VISION_PROMPT, image, **kwargs) or ""
    except Exception as exc:
        logger.warning("Vision extraction failed for %s: %s", image_path, exc)
        return ""


def _build_text_config(llm_service: Any) -> Any:
    """
    Construct a provider-specific config that forces plain-text output, or
    None if the provider doesn't have such a knob (Anthropic / OpenAI).
    """
    cls_name = type(llm_service).__name__.lower()
    if "gemini" in cls_name:
        try:
            from cins.processors.llm_services.gemini import GeminiConfig
            return GeminiConfig(response_mime_type="text/plain")
        except Exception:
            return None
    return None
