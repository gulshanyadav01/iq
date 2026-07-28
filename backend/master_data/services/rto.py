"""RTO-code → state-slug derivation for RTOMaster seeding.

State slug is derived from the **2-letter registration prefix** of an RTO code
(``AP01`` → ``ap`` → ``andhra-pradesh``), NOT the parenthetical in the display
string (RJ/HR/PB parentheticals carry city names, not states).

The prefix→slug map duplicates the 2-letter block of cins'
``STATE_NAME_MAPPINGS`` on purpose: importing a cins transformer constant into
``master_data`` would invert the app dependency direction. Keep the two in sync
if a new state/UT prefix appears. The seed loader skips and counts any prefix
absent from this map so gaps surface loudly instead of silently mis-labeling.
"""

from __future__ import annotations

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
