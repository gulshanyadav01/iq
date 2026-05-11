"""
Insurance Carrier Vocabulary

Single source of truth for the 20 valid Indian motor-insurance carriers
recognised by this system. Used by:

- Pass 1 metadata extraction (`MetadataExtraction.provider_name`) — constrains
  the LLM's structured output to one of these short codes via `Literal`.
- The Pass 1 prompt (`prompts/pass1_metadata_prompt.md`) — lists the codes.
- The raw→CICS transformer (`processors/core/raw_to_cics_transformer.py`)
  — normalises any LLM output (or alias) through `CARRIER_ALIASES` before
  emitting the canonical short code as `provider_id`.

Adding a carrier:
1. Append its short code to `VALID_CARRIERS` (kept alphabetical).
2. Add the canonical full name to `CARRIER_FULL_NAMES`.
3. Extend `CARRIER_ALIASES` with the common variants (full name, abbreviations,
   sender-domain). Keep entries lower-case — `normalize_carrier()` lower-cases
   the input before lookup.
4. Update the carrier list inside `prompts/pass1_metadata_prompt.md`.
"""

from typing import Literal, Optional


# ---------------------------------------------------------------------------
# Canonical short codes (alphabetical)
# ---------------------------------------------------------------------------

VALID_CARRIERS: tuple[str, ...] = (
    "Bajaj",
    "Chola",
    "Digit",
    "FG",
    "HDFC",
    "ICICI",
    "Iffco",
    "Kotak",
    "Liberty",
    "Magma",
    "National",
    "New India",
    "Oriental",
    "Reliance",
    "RSGI",
    "SBI",
    "Shriram",
    "United",
    "Universal",
    "Zuno",
)


# `CarrierCode` is the structured-output constraint for Pass 1.
# Empty string is allowed when the LLM cannot confidently identify the carrier.
CarrierCode = Literal[
    "",
    "Bajaj",
    "Chola",
    "Digit",
    "FG",
    "HDFC",
    "ICICI",
    "Iffco",
    "Kotak",
    "Liberty",
    "Magma",
    "National",
    "New India",
    "Oriental",
    "Reliance",
    "RSGI",
    "SBI",
    "Shriram",
    "United",
    "Universal",
    "Zuno",
]


# ---------------------------------------------------------------------------
# Canonical full names (used as `provider_name` in CICS output)
# ---------------------------------------------------------------------------

CARRIER_FULL_NAMES: dict[str, str] = {
    "Bajaj": "Bajaj Allianz",
    "Chola": "Cholamandalam MS",
    "Digit": "Go Digit",
    "FG": "Future Generali",
    "HDFC": "HDFC Ergo",
    "ICICI": "ICICI Lombard",
    "Iffco": "IFFCO Tokio",
    "Kotak": "Kotak Mahindra",
    "Liberty": "Liberty General",
    "Magma": "Magma HDI",
    "National": "National Insurance",
    "New India": "New India Assurance",
    "Oriental": "Oriental Insurance",
    "Reliance": "Reliance General",
    "RSGI": "Royal Sundaram",
    "SBI": "SBI General",
    "Shriram": "Shriram General",
    "United": "United India",
    "Universal": "Universal Sompo",
    "Zuno": "Zuno General",
}


# ---------------------------------------------------------------------------
# Alias map (lower-case input → canonical short code)
# ---------------------------------------------------------------------------
#
# Pragmatic, not exhaustive. Keys MUST be lower-case. The lookup in
# `normalize_carrier()` lower-cases the input first.

CARRIER_ALIASES: dict[str, str] = {
    # Bajaj Allianz General Insurance
    "bajaj": "Bajaj",
    "bajaj allianz": "Bajaj",
    "bajaj allianz general insurance": "Bajaj",
    "bajaj allianz general insurance company": "Bajaj",
    "bagic": "Bajaj",
    "bajajallianz.com": "Bajaj",
    "bajajallianz.co.in": "Bajaj",
    # Cholamandalam MS General Insurance
    "chola": "Chola",
    "chola ms": "Chola",
    "cholamandalam": "Chola",
    "cholamandalam ms": "Chola",
    "cholamandalam ms general insurance": "Chola",
    "cholams.com": "Chola",
    "cholamsgeneral.com": "Chola",
    # Go Digit General Insurance
    "digit": "Digit",
    "go digit": "Digit",
    "godigit": "Digit",
    "go digit general insurance": "Digit",
    "godigit.com": "Digit",
    # Future Generali India Insurance
    "fg": "FG",
    "fgi": "FG",
    "fgii": "FG",
    "future generali": "FG",
    "future generali india": "FG",
    "future generali india insurance": "FG",
    "future generali india insurance company": "FG",
    "futuregenerali.in": "FG",
    "futuregenerali.com": "FG",
    # HDFC Ergo General Insurance
    "hdfc": "HDFC",
    "hdfc ergo": "HDFC",
    "hdfc ergo general insurance": "HDFC",
    "hdfcergo.com": "HDFC",
    # ICICI Lombard General Insurance
    "icici": "ICICI",
    "icici lombard": "ICICI",
    "icici lombard general insurance": "ICICI",
    "icicilombard.com": "ICICI",
    # IFFCO Tokio General Insurance
    "iffco": "Iffco",
    "iffco tokio": "Iffco",
    "iffco-tokio": "Iffco",
    "iffco tokio general insurance": "Iffco",
    "itgi": "Iffco",
    "iffcotokio.co.in": "Iffco",
    # Kotak Mahindra General Insurance
    "kotak": "Kotak",
    "kotak mahindra": "Kotak",
    "kotak general insurance": "Kotak",
    "kotak mahindra general insurance": "Kotak",
    "kotak.com": "Kotak",
    # Liberty General Insurance
    "liberty": "Liberty",
    "liberty general": "Liberty",
    "liberty general insurance": "Liberty",
    "libertyinsurance.in": "Liberty",
    # Magma HDI General Insurance
    "magma": "Magma",
    "magma hdi": "Magma",
    "magma hdi general insurance": "Magma",
    "magmahdi.com": "Magma",
    # National Insurance Company
    "national": "National",
    "national insurance": "National",
    "national insurance company": "National",
    "nationalinsurance.nic.co.in": "National",
    "nic": "National",
    # New India Assurance Company
    "new india": "New India",
    "new india assurance": "New India",
    "the new india assurance": "New India",
    "new india assurance company": "New India",
    "newindia.co.in": "New India",
    "niacl": "New India",
    # Oriental Insurance Company
    "oriental": "Oriental",
    "oriental insurance": "Oriental",
    "the oriental insurance": "Oriental",
    "oriental insurance company": "Oriental",
    "orientalinsurance.org.in": "Oriental",
    # Reliance General Insurance
    "reliance": "Reliance",
    "reliance general": "Reliance",
    "reliance general insurance": "Reliance",
    "reliancegeneral.co.in": "Reliance",
    "rgicl": "Reliance",
    # Royal Sundaram General Insurance (short code RSGI per business convention)
    "rsgi": "RSGI",
    "royal": "RSGI",
    "royal sundaram": "RSGI",
    "royal sundram": "RSGI",  # common misspelling (missing 'a')
    "royal sundaram general": "RSGI",
    "royal sundaram general insurance": "RSGI",
    "royal sundaram alliance": "RSGI",
    "rsai": "RSGI",
    "royalsundaram.com": "RSGI",
    "royalsundaram.in": "RSGI",
    "royalsundaram.co.in": "RSGI",
    # SBI General Insurance
    "sbi": "SBI",
    "sbi general": "SBI",
    "sbi general insurance": "SBI",
    "sbigeneral.in": "SBI",
    # Shriram General Insurance
    "shriram": "Shriram",
    "shriram general": "Shriram",
    "shriram general insurance": "Shriram",
    "shriramgi.com": "Shriram",
    # United India Insurance Company
    "united": "United",
    "united india": "United",
    "united india insurance": "United",
    "united india insurance company": "United",
    "uiic.co.in": "United",
    # Universal Sompo General Insurance
    "universal": "Universal",
    "universal sompo": "Universal",
    "universal sompo general insurance": "Universal",
    "universalsompo.com": "Universal",
    # Zuno General Insurance (formerly Edelweiss General)
    "zuno": "Zuno",
    "zuno general": "Zuno",
    "zuno general insurance": "Zuno",
    "edelweiss general": "Zuno",
    "edelweiss general insurance": "Zuno",
    "hizuno.com": "Zuno",
}


# ---------------------------------------------------------------------------
# Normalisation helper
# ---------------------------------------------------------------------------


def normalize_carrier(value: Optional[str]) -> Optional[str]:
    """
    Map a free-text provider string (LLM output, alias, sender domain) to a
    canonical short code from `VALID_CARRIERS`. Returns `None` when the input
    is empty/blank or no alias matches — callers decide how to handle that
    (typically: emit `UNKNOWN_PROVIDER` and append a warning).

    Matching is case-insensitive and tries, in order:
    1. The whole string against `CARRIER_ALIASES` (lower-cased, trimmed).
    2. Direct membership in `VALID_CARRIERS` (already-canonical input).
    3. Substring containment of any alias key inside the input — handles
       cases like "Future Generali India Insurance Company Limited" where the
       LLM appends extra suffixes that aren't in the alias map verbatim.
    """
    if not value:
        return None
    cleaned = value.strip().lower()
    if not cleaned:
        return None

    if cleaned in CARRIER_ALIASES:
        return CARRIER_ALIASES[cleaned]

    # Already-canonical short code (case-insensitive)
    for code in VALID_CARRIERS:
        if cleaned == code.lower():
            return code

    # Fuzzy: longest alias key that appears as a substring wins. Sorting by
    # length descending avoids "national" matching "international" before a
    # more specific alias does.
    for alias in sorted(CARRIER_ALIASES.keys(), key=len, reverse=True):
        if alias in cleaned:
            return CARRIER_ALIASES[alias]

    return None
