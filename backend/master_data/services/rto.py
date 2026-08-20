"""RTO-code → state-slug derivation for RTOMaster seeding.

The display value's parenthetical is authoritative when it names a state we
know (``UP14-Ghaziabad(Delhi NCR)(Group B)`` → ``delhi-ncr``); the 2-letter
registration prefix is the fallback (``AP01`` → ``ap`` → ``andhra-pradesh``).

That ordering was reversed until a prod-dump reconciliation showed why it
matters. 21 RTOs register under one state's prefix but belong to another's
zone — the NCR ring (``HR26-Gurgaon``, ``HR29-Faridabad``, ``UP14-Ghaziabad``,
``UP16-Gautam Buddha Nagar``, ``UP18-Greater Noida``), Chandigarh
(``HR70``, ``HR78``, ``PB01``, ``HR03-Panchkula``) and the Daman/Diu/Silvassa
block the carrier zones under Gujarat. The export fills its RTO column from
``rtos_for_states``, which indexes on this slug, so a prefix-derived
``uttar-pradesh`` handed ``UP14-Ghaziabad`` the UP payout (OD 30) where the
carrier pays the Delhi NCR one (OD 27.5). The parenthetical is also what both
our export and the carrier's own system display, so it is the side that has to
win.

The earlier caution — that a parenthetical may carry a city rather than a
state — is handled structurally rather than by preferring the prefix: a
parenthetical is accepted ONLY when it slugifies into a value already present
in ``RTO_PREFIX_TO_STATE_SLUG``, so it can never invent a geography. Measured
against the seeded master, 1720 of 1721 rows resolve that way; the lone holdout
is ``NL10-...(Peren)``, a district name, which falls back to its prefix.

The prefix→slug map duplicates the 2-letter block of cins'
``STATE_NAME_MAPPINGS`` on purpose: importing a cins transformer constant into
``master_data`` would invert the app dependency direction. Keep the two in sync
if a new state/UT prefix appears. The seed loader skips and counts any row that
resolves by neither route so gaps surface loudly instead of silently
mis-labeling.
"""

from __future__ import annotations

import re
from typing import Optional

# 2-letter registration prefix -> canonical state slug (matches the slugs the
# transformer writes into rule conditions["state"]).
RTO_PREFIX_TO_STATE_SLUG = {
    "an": "andaman-and-nicobar",
    "ap": "andhra-pradesh",
    "ar": "arunachal-pradesh",
    "as": "assam",
    "bh": "bihar",  # RTO/grid data uses both BR and BH for Bihar
    "br": "bihar",
    "ch": "chandigarh",
    "cg": "chattisgarh",
    "dl": "delhi-ncr",
    "ga": "goa",
    "gj": "gujarat",
    "hr": "haryana",
    "hp": "himachal-pradesh",
    "jk": "jammu-and-kashmir",
    "jh": "jharkhand",
    "ka": "karnataka",
    "kl": "kerala",
    "la": "ladakh",
    "ld": "lakshadweep",
    "mp": "madhya-pradesh",
    "mh": "maharashtra",
    "mn": "manipur",
    "ml": "meghalaya",
    "mz": "mizoram",
    "nl": "nagaland",
    "od": "orissa",
    "or": "orissa",  # RTO data uses both OD and OR for Odisha
    "py": "pondicherry",
    "pb": "punjab",
    "rj": "rajasthan",
    "sk": "sikkim",
    "tn": "tamil-nadu",
    "ts": "telangana",
    "tg": "telangana",  # RTO data uses both TS and TG for Telangana
    "tr": "tripura",
    "up": "uttar-pradesh",
    "uk": "uttarakhand",
    "ua": "uttarakhand",  # RTO data uses both UK and UA for Uttarakhand
    "wb": "west-bengal",
    # Daman & Diu / Dadra & Nagar Haveli combined UT — both prefixes map to it.
    "dd": "dadra-and-nagar-haveli-and-daman-and-diu",
    "dn": "dadra-and-nagar-haveli-and-daman-and-diu",
}


def state_slug_for_code(code: str) -> Optional[str]:
    """Return the canonical state slug for an RTO code, or None if unmapped.

    Reads the leading 2 alpha chars of the code (``AP01`` → ``ap``). Returns
    None for empty/short codes or prefixes absent from the map — callers should
    skip-and-count these so seeding gaps are visible.
    """
    if not code:
        return None
    prefix = "".join(c for c in str(code).strip()[:2]).lower()
    if len(prefix) < 2 or not prefix.isalpha():
        return None
    return RTO_PREFIX_TO_STATE_SLUG.get(prefix)


# The states an RTO display value is allowed to name. Derived from the prefix
# map rather than listed again so the two can never drift apart.
_KNOWN_STATE_SLUGS = frozenset(RTO_PREFIX_TO_STATE_SLUG.values())

_PAREN_RE = re.compile(r"\(([^()]*)\)")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def state_slug_for_value(value: str) -> Optional[str]:
    """Return the state slug named by an RTO display value, or None.

    ``UP14-Ghaziabad(Delhi NCR)(Group B)`` → ``delhi-ncr``. Reads the LAST
    parenthetical that is not the ``(Group X)`` suffix, since the locality part
    of the value can itself contain brackets (``PB89-Amritsar (2)(Punjab)``).

    Returns None unless the result is already a known state slug, so a value
    carrying a district (``NL10-...(Peren)``) or a stray token cannot invent a
    geography — the caller falls back to the registration prefix.
    """
    if not value:
        return None
    parts = [
        p
        for p in _PAREN_RE.findall(str(value))
        if not p.strip().lower().startswith("group")
    ]
    if not parts:
        return None
    slug = _NON_SLUG_RE.sub("-", parts[-1].strip().lower().replace("&", "and")).strip(
        "-"
    )
    return slug if slug in _KNOWN_STATE_SLUGS else None


def state_slug_for_rto(code: str, value: str = "") -> Optional[str]:
    """The state slug for an RTO row: display value first, code prefix second.

    One definition so every seeding path agrees. See the module docstring for
    why the value wins.
    """
    return state_slug_for_value(value) or state_slug_for_code(code)
