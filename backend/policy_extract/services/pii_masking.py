"""PII masking for the policy-extract LLM pipeline.

The OCR markdown contains personal data verbatim (customer name, mobile, email,
address, PAN/Aadhaar, vehicle registration/engine/chassis). Both Gemini stages are
fed from that markdown, so without masking the public LLM sees raw PII.

The strategy is a reversible token round-trip:

    masked, token_map = mask_markdown(markdown)   # before any LLM call
    ... Gemini Stage 1 + Stage 2 run on `masked` ...
    unmask_result(structured_data, token_map)     # after post-processing

Each PII span is replaced with a placeholder of the form ``PII<TYPE><NNNN>`` —
uppercase and space-free *by design*, so it survives the three transforms the
parser applies downstream: Stage 2 uppercases all text values, ``_clean_whitespace``
strips spaces from reg/engine/chassis/mobile/email/pincode, and ``_post_process``
re-reads the (masked) markdown. A letter-prefixed token is also never mistaken for
a premium amount by the numeric-anchor logic. Stage 1 copies values verbatim, so a
token round-trips into the schema value, where ``unmask_result`` restores it.

Two detection layers sit behind one interface, chosen by
``POLICY_EXTRACT_PII_ENGINE``:
  * ``regex`` (default, offline, no model): high-precision structured IDs.
  * ``presidio``: the above plus Microsoft Presidio NER for names/addresses
    (requires ``presidio-analyzer`` + a spaCy model; degrades to regex if absent).

Known limitation: the raw PDF reaches the OCR step *before* any markdown exists, so
masking protects the extraction (Stage 1/2) hops, not the OCR hop. With
``POLICY_EXTRACT_OCR_ENGINE=mineru`` that OCR hop is the (self-hosted) Minerva
server; with ``gemini`` the PDF goes to the public LLM at OCR time, so masking no
longer shields that first hop. See the plan's caveats.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex detectors. Order matters: more specific / higher-risk-of-overlap
# patterns run first so their spans are tokenised before a looser pattern can
# claim part of them. Each entry is (type_label, compiled_regex, group_index)
# where group_index is the capture group holding the PII (0 = whole match).
# ---------------------------------------------------------------------------

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# PAN: 5 letters, 4 digits, 1 letter.
_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
# Aadhaar: 12 digits, optionally grouped 4-4-4.
_AADHAAR = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
# Indian mobile: optional +91/0 prefix then a 10-digit number starting 6-9.
_MOBILE = re.compile(r"(?<!\d)(?:\+?91[-\s]?|0)?[6-9]\d{9}(?!\d)")
# Vehicle registration: e.g. MH 12 AB 1234 / DL8CAB1234 / TS09EA9999.
_REG = re.compile(r"\b[A-Z]{2}[-\s]?\d{1,2}[-\s]?[A-Z]{1,3}[-\s]?\d{1,4}\b")
# Label-anchored identifiers/pincode — mask only the value (group 2), never the
# label, and only when the label is present (keeps false positives off amounts).
_ENGINE = re.compile(
    r"(engine\s*(?:no\.?|number|#)?\s*[:\-]?\s*)([A-Z0-9]{5,})", re.IGNORECASE
)
_CHASSIS = re.compile(
    r"(chassis\s*(?:no\.?|number|#)?\s*[:\-]?\s*)([A-Z0-9]{5,})", re.IGNORECASE
)
_PINCODE = re.compile(
    r"(pin\s*code|pincode|pin)(\D{0,6})([1-9]\d{5})(?!\d)", re.IGNORECASE
)

# (type, regex, value_group). Tuples with a 2-group label-anchored pattern keep
# the label text and only tokenise the trailing value group.
_PATTERNS: List[Tuple[str, re.Pattern, int]] = [
    ("EMAIL", _EMAIL, 0),
    ("PAN", _PAN, 0),
    ("AADHAAR", _AADHAAR, 0),
    ("ENGINE", _ENGINE, 2),
    ("CHASSIS", _CHASSIS, 2),
    ("PINCODE", _PINCODE, 3),
    ("REG", _REG, 0),
    ("MOBILE", _MOBILE, 0),
]


class _Masker:
    """Accumulates a per-document token map so the same value maps to one token."""

    def __init__(self) -> None:
        self.token_map: Dict[str, str] = {}  # token -> original
        self._by_value: Dict[str, str] = {}  # original -> token (dedupe)
        self._seq = 0

    def token_for(self, original: str, type_: str) -> str:
        key = f"{type_}:{original}"
        existing = self._by_value.get(key)
        if existing:
            return existing
        self._seq += 1
        token = f"PII{type_}{self._seq:04d}"
        self.token_map[token] = original
        self._by_value[key] = token
        return token

    def apply_pattern(
        self, text: str, type_: str, pattern: re.Pattern, group: int
    ) -> str:
        def _repl(match: re.Match) -> str:
            value = match.group(group)
            if not value or not value.strip():
                return match.group(0)
            token = self.token_for(value, type_)
            # Preserve everything outside the value group (e.g. the label prefix).
            whole = match.group(0)
            return (
                whole[: match.start(group) - match.start(0)]
                + token
                + whole[match.end(group) - match.start(0) :]
            )

        return pattern.sub(_repl, text)


def _run_presidio(text: str, masker: _Masker) -> str:
    """Mask PERSON / LOCATION spans via Presidio, if available.

    Lazily imported and fully optional: any import/runtime problem logs once and
    returns the text unchanged so masking degrades to the regex layer rather than
    failing the extraction.
    """
    try:
        from presidio_analyzer import AnalyzerEngine
    except Exception:  # noqa: BLE001 - optional dependency
        logger.warning(
            "POLICY_EXTRACT_PII_ENGINE=presidio but presidio-analyzer is not "
            "installed; falling back to regex-only masking."
        )
        return text

    try:
        analyzer = _get_presidio_analyzer(AnalyzerEngine)
        results = analyzer.analyze(
            text=text, entities=["PERSON", "LOCATION"], language="en"
        )
        # Replace from the end so earlier offsets stay valid as we splice tokens in.
        for res in sorted(results, key=lambda r: r.start, reverse=True):
            type_ = "NAME" if res.entity_type == "PERSON" else "ADDRESS"
            original = text[res.start : res.end]
            if not original.strip():
                continue
            token = masker.token_for(original, type_)
            text = text[: res.start] + token + text[res.end :]
    except Exception:  # noqa: BLE001 - never let NER break extraction
        logger.exception("Presidio analysis failed; using regex-only masking.")
    return text


_PRESIDIO_ANALYZER = None


def _get_presidio_analyzer(engine_cls):
    """Build the Presidio analyzer once (loading a spaCy model is expensive)."""
    global _PRESIDIO_ANALYZER
    if _PRESIDIO_ANALYZER is None:
        _PRESIDIO_ANALYZER = engine_cls()
    return _PRESIDIO_ANALYZER


def mask_markdown(markdown: str, engine: str = "regex") -> Tuple[str, Dict[str, str]]:
    """Return ``(masked_markdown, token_map)``.

    ``token_map`` maps each placeholder token back to its original value. Pass it
    to :func:`unmask_result` after extraction. Masking is idempotent per value: a
    repeated PII string reuses the same token.
    """
    if not markdown:
        return markdown, {}

    masker = _Masker()
    text = markdown
    # Names/addresses first (Presidio needs the untouched prose); structured IDs
    # after, so a tokenised name can't swallow a nearby number.
    if engine == "presidio":
        text = _run_presidio(text, masker)
    for type_, pattern, group in _PATTERNS:
        text = masker.apply_pattern(text, type_, pattern, group)
    return text, masker.token_map


def build_summary(token_map: Dict[str, str], enabled: bool) -> Dict:
    """Privacy summary for the result payload / UI badge."""
    types: Dict[str, int] = {}
    for token in token_map:
        m = re.match(r"PII([A-Z]+)\d+", token)
        if m:
            types[m.group(1).lower()] = types.get(m.group(1).lower(), 0) + 1
    return {"enabled": enabled, "masked_count": len(token_map), "types": types}


_TOKEN_IN_TEXT = re.compile(r"PII[A-Z]+\d{4,}")


def _unmask_text(value: str, token_map: Dict[str, str]) -> str:
    if not token_map:
        return value
    return _TOKEN_IN_TEXT.sub(lambda m: token_map.get(m.group(0), m.group(0)), value)


def unmask_result(structured_data: Dict, token_map: Dict[str, str]) -> None:
    """Restore real PII values in-place across the schema's field dicts.

    Walks ``structured_data[field]["value"]`` strings and reverses any tokens,
    including tokens embedded inside larger values (e.g. a name inside an address).
    Also restores ``original_value`` if present.
    """
    if not token_map or not isinstance(structured_data, dict):
        return
    for data in structured_data.values():
        if not isinstance(data, dict):
            continue
        for key in ("value", "original_value"):
            val = data.get(key)
            if isinstance(val, str) and "PII" in val:
                data[key] = _unmask_text(val, token_map)
