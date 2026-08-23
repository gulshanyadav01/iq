"""
RawToCICSTransformer - Converts RawExtraction to full CICSDocument

This transformer handles all deterministic transformations:
- ID generation (document_id, rule_id, trace_id)
- Priority computation from rule specificity
- Commission type detection from value patterns
- Condition normalization (text to enums)
- Premium range parsing
- State/RTO expansion from master data
- Source trace generation
"""

import json
import logging
import os
import re
import hashlib
from collections import OrderedDict
from datetime import datetime
from itertools import chain
from typing import List, Dict, NamedTuple, Optional, Set, Tuple, Any

from cins.processors.core.carrier_subclasses import (
    canonical_subclass_slugs,
    carrier_subclass_rows,
)
from cins.schema.carriers import CARRIER_FULL_NAMES, normalize_carrier
from cins.schema.raw_extraction import (
    RawExtraction,
    RawCommissionEntry,
    RawMasterDataEntry,
)
from cins.schema.cics_model import (
    CICSDocument,
    DocumentMetadata,
    Provider,
    CommissionPeriod,
    Product,
    CommissionRule,
    RuleConditions,
    CommissionOutcome,
    NumericRange,
    CommissionTier,
    Bonus,
    Deduction,
    QualifyingClause,
    SourceTrace,
    SourceLocation,
    ChangeReference,
    Validation,
)

logger = logging.getLogger(__name__)


def parse_document_date(content: str) -> Optional[str]:
    """Best-effort ISO ``YYYY-MM-DD`` from a document's own date signal.

    Used as the commission-period fallback when the extraction found no written
    period. Today only the email ``Date:`` header (RFC 2822) is recognised — it
    is machine-parseable and authoritative for the "effective from" of a grid
    mailed with no explicit period. Returns ``None`` for non-email content or an
    unparsable header, so callers fall through to their last-resort default.
    """
    if not content:
        return None
    from email.utils import parsedate_to_datetime

    m = re.search(r"^\s*Date:\s*(.+)$", content, re.MULTILINE)
    if not m:
        return None
    try:
        dt = parsedate_to_datetime(m.group(1).strip())
    except (TypeError, ValueError):
        return None
    return dt.strftime("%Y-%m-%d") if dt else None


# Mapping tables for normalizing free-text to enum values

BOOKING_MODE_MAPPINGS = {
    "online": "online",
    "digital": "online",
    "web": "online",
    "app": "online",
    "offline": "offline",
    "branch": "offline",
    "agent": "offline",
    "partner": "offline",
    "misp": "offline",
    "broker": "offline",
    "direct": "offline",
}

BUSINESS_TYPE_MAPPINGS = {
    "new": "new",
    "new customer": "new",
    "new customers": "new",
    "first time": "new",
    "fresh": "new",
    "renewal": "renewal",
    "renewals": "renewal",
    "renew": "renewal",
    "existing": "renewal",
    "rollover": "rollover",
    "portability": "port",
    "port": "port",
}

POLICY_TYPE_MAPPINGS = {
    "comprehensive": "comprehensive",
    "comp": "comprehensive",
    "full": "comprehensive",
    "package": "comprehensive",
    "pack": "comprehensive",  # Cholamandalam: PACK = comprehensive
    "third-party": "third-party",
    "third party": "third-party",
    "tp": "third-party",
    "tp only": "third-party",
    "satp": "third-party",
    "act": "third-party",  # Cholamandalam: ACT = third-party (act-only)
    "saod": "saod",
    "sa od": "saod",
    "stand alone od": "saod",
    "standalone od": "saod",
    "sod": "saod",  # Cholamandalam: SOD = standalone OD
    "od-only": "saod",
    "od only": "saod",
    "own damage": "saod",
}

VEHICLE_SUB_CLASS_MAPPINGS = {
    "private car": "private-car",
    "private-car": "private-car",
    "pvt car": "private-car",
    "car": "private-car",
    "taxi": "taxi",
    "kaali peeli taxi": "kaali-peeli-taxi",
    "kaali-peeli-taxi": "kaali-peeli-taxi",
    "scooter": "scooter",
    "moped": "moped",
    "bike": "bike",
    "motorcycle": "bike",
    "e-bike": "e-bike",
    "e bike": "e-bike",
    "e rickshaw": "e-rickshaw",
    "e-rickshaw": "e-rickshaw",
    "auto rickshaw": "auto-rickshaw",
    "auto-rickshaw": "auto-rickshaw",
    # Chola CV segment slugs, fed through _normalize_subclass_slug (which strips
    # the "N_" index and any "[QUALIFIER]", then unpacks underscores). A goods or
    # passenger three-wheeler is an auto on these grids; the wheel count itself is
    # already carried by number_of_wheels, so the slug's value here is the body.
    "gccv 3w": "auto-rickshaw",
    "3w auto": "auto-rickshaw",
    # Source spells the segment "3_HARVESTOR"; the master and the enum use the
    # correct "Harvester".
    "harvestor": "harvester",
    # "1_TRAC[RENEWAL]" reduces to a bare "trac" after slug normalization.
    "trac": "tractor",
    "pick up/delivery van": "pick-up-delivery-van",
    "pick-up-delivery-van": "pick-up-delivery-van",
    "pickup van": "pick-up-delivery-van",
    "delivery van": "pick-up-delivery-van",
    "truck": "truck",
    "tractor": "tractor",
    "tipper": "tipper",
    "heavy truck/semi trailer": "heavy-truck-semi-trailer",
    "heavy-truck-semi-trailer": "heavy-truck-semi-trailer",
    "heavy truck": "heavy-truck-semi-trailer",
    "semi trailer": "heavy-truck-semi-trailer",
    "dumper": "dumper",
    "tanker": "tanker",
    "gas tanker": "gas-tanker",
    "gas-tanker": "gas-tanker",
    "trailer": "trailer",
    "flat bed trailer": "flat-bed-trailer",
    "flat-bed-trailer": "flat-bed-trailer",
    "refrigerated van": "refrigerated-van",
    "refrigerated-van": "refrigerated-van",
    "bulker": "bulker",
    "school bus": "school-bus",
    "school-bus": "school-bus",
    "passenger bus": "passenger-bus",
    "passenger-bus": "passenger-bus",
    "staff bus": "staff-bus",
    "staff-bus": "staff-bus",
    "agricultural tractor": "agricultural-tractor",
    "agricultural-tractor": "agricultural-tractor",
    "backhoe loader": "backhoe-loader",
    "backhoe-loader": "backhoe-loader",
    "crane": "crane",
    "ambulance": "ambulance",
    "loader": "loader",
    "tractor loader": "tractor-loader",
    "tractor-loader": "tractor-loader",
    "harvester": "harvester",
    "excavators": "excavators",
    "excavator": "excavators",
    "garbage van": "garbage-van",
    "garbage-van": "garbage-van",
    "motor grader": "motor-grader",
    "motor-grader": "motor-grader",
    "transit mixer": "transit-mixer",
    "transit-mixer": "transit-mixer",
    "fork lift trucks": "fork-lift-trucks",
    "fork-lift-trucks": "fork-lift-trucks",
    "forklift": "fork-lift-trucks",
    "generator van": "generator-van",
    "generator-van": "generator-van",
    "drilling rigs": "drilling-rigs",
    "drilling-rigs": "drilling-rigs",
    "compressors": "compressors",
    "compressor": "compressors",
    "other carts": "other-carts",
    "other-carts": "other-carts",
    "plane loaders and other": "plane-loaders-and-other",
    "plane-loaders-and-other": "plane-loaders-and-other",
    "road rollers": "road-rollers",
    "road-rollers": "road-rollers",
    "road roller": "road-rollers",
    "road sweeper": "road-sweeper",
    "road-sweeper": "road-sweeper",
    "car carrier": "car-carrier",
    "car-carrier": "car-carrier",
    "greater than six seater": "greater-than-six-seater",
    "greater-than-six-seater": "greater-than-six-seater",
    "not available": "not-available",
    "not-available": "not-available",
    "kisan suvidha": "kisan-suvidha",
    "kisan-suvidha": "kisan-suvidha",
    "container": "container",
    "towing van": "towing-van",
    "towing-van": "towing-van",
    "bulldozer": "bulldozer",
    "compactor": "compactor",
    "jetting machine": "jetting-machine",
    "jetting-machine": "jetting-machine",
    "fire vehicles": "fire-vehicles",
    "fire-vehicles": "fire-vehicles",
    # Chola's OWN misspellings, as they appear in their product hierarchy
    # workbook. The master is stored carrier-verbatim on purpose, so the fix
    # belongs here rather than in the CSV: a value that fails to normalize is
    # silently absent from `canonical_subclass_slugs`, which both narrows the
    # MISC-D merge's widened body list AND makes CholaProductHierarchyValidator
    # reject the correctly-spelled slug a rule legitimately produced
    # ("Earth Movers" -> excavators was flagged as invalid for CV/MISC-D because
    # the master only had "Excavaters"). Keep the correct spellings above too.
    "excavaters": "excavators",
    "excavater": "excavators",
    "buldozer": "bulldozer",
    "tranist mixer": "transit-mixer",
    "refridgerated van": "refrigerated-van",
    # Chola MSV / PCCV>6 sub-segment bodies (grid-verbatim aliases).
    "earth movers": "excavators",
    "earth mover": "excavators",
    "bullgraders": "bulldozer",
    "bullgrader": "bulldozer",
    "maxi cab": "maxicab",
    "maxi-cab": "maxicab",
    "maxicab": "maxicab",
    "big taxis": "big-taxi",
    "big taxi": "big-taxi",
    "big-taxi": "big-taxi",
    "others": "others",
}

# Two-wheeler BODY words that can mis-split into vehicle_make from a make-less
# flattened TW column header ("SCOOTER > 0.37"). Deliberately narrow: a subset
# of VEHICLE_SUB_CLASS_MAPPINGS keys that are unambiguously body types and never
# a real make. Excludes make-bucket sentinels like "others".
_MISSPLIT_BODY_TOKENS = frozenset({"scooter", "bike", "moped", "motorcycle"})

# The Car-product slice of VEHICLE_SUB_CLASS_MAPPINGS, used to disambiguate a
# Chola Car segment label before defaulting to "private-car". Deliberately a
# subset: the probe blob is a structural label (product key / segment /
# source_section), and matching it against the FULL dict would let an unrelated
# body word elsewhere in the label win over the segment's own meaning.
CAR_SUB_CLASS_PROBE_MAPPINGS = {
    k: v
    for k, v in VEHICLE_SUB_CLASS_MAPPINGS.items()
    if v in ("private-car", "taxi", "kaali-peeli-taxi")
}

FUEL_TYPE_MAPPINGS = {
    "petrol": "petrol",
    "diesel": "diesel",
    "cng": "cng",
    "lpg": "lpg",
    "electric": "electric",
    "ev": "electric",
    # "Non Petrol" is a group label meaning Diesel + CNG + LPG — expanded at normalisation time
    "non petrol": "non-petrol",
    "non-petrol": "non-petrol",
}

NUMBER_OF_WHEELS_MAPPINGS = {
    "3w": "3w",
    "three wheeler": "3w",
    "three-wheeler": "3w",
    "3 wheeler": "3w",
    "4w": "4w-and-above",
    "4w & above": "4w-and-above",
    "4w and above": "4w-and-above",
    "4w-and-above": "4w-and-above",
    "four wheeler": "4w-and-above",
    "four-wheeler": "4w-and-above",
}

VEHICLE_CARRIER_MAPPINGS = {
    "public": "public",
    "private": "private",
    "commercial": "commercial",
}

SCHOOL_BUS_TYPE_MAPPINGS = {
    "on individual name": "on-individual-name",
    "on-individual-name": "on-individual-name",
    "individual": "on-individual-name",
    "on school name": "on-school-name",
    "on-school-name": "on-school-name",
    "school": "on-school-name",
    "on company name": "on-company-name",
    "on-company-name": "on-company-name",
    "company": "on-company-name",
    "on contract (transporter)": "on-contract-transporter",
    "on contract transporter": "on-contract-transporter",
    "on-contract-transporter": "on-contract-transporter",
    "contract": "on-contract-transporter",
    "transporter": "on-contract-transporter",
}

DEAL_PARAMETER_MAPPINGS = {
    "od": "od",
    "net": "net",
    "regular tp": "regular-tp",
    "regular-tp": "regular-tp",
    "tp with vehicle age": "tp-with-vehicle-age",
    "tp-with-vehicle-age": "tp-with-vehicle-age",
    "flat amount": "flat-amount",
    "flat-amount": "flat-amount",
    "ape": "ape",
    # Chola states the liability payout basis as "TP/PA Premium" on both the TW
    # and Private Car grids ("TW Liability policy is on TP/PA Premium"). It only
    # ever appears on ACT/liability rules, so it is the regular-TP basis.
    # "Total Premium" is the same basis under a looser label — it occurs solely
    # on Private Car ACT rows whose own T&C reads "Private Car Liability policy
    # Pay-out is on TP/PA Premium". Both keys are longer than 5 chars, so
    # _normalize_list_field matches them as plain substrings ahead of "tp".
    "tp/pa premium": "regular-tp",
    "tp/pa": "regular-tp",
    "total premium": "regular-tp",
}

# Maps raw product key substrings → canonical product name shown in output
# Longer/more-specific patterns first so they take priority
PRODUCT_NAME_MAPPINGS = [
    # Commercial Vehicle variants
    ("staff bus", "Commercial Vehicle"),
    ("school bus", "Commercial Vehicle"),
    ("passenger bus", "Commercial Vehicle"),
    ("mini bus", "Commercial Vehicle"),
    ("bus", "Commercial Vehicle"),
    # Chola spells its CV tables with a doubled C (GCCV / PCCV) and abbreviates
    # miscellaneous-D as MSV. These are disjoint from the bare "gcv"/"pcv"
    # substrings below -- "gcv" does not occur inside "gccv" -- so without their
    # own entries a product_name of "GCCV" fell through to the raw key and the
    # export could not resolve its RTO bucket.
    ("gccv", "Commercial Vehicle"),
    ("pccv", "Commercial Vehicle"),
    ("msv", "Commercial Vehicle"),
    ("gcv", "Commercial Vehicle"),
    ("goods carrying", "Commercial Vehicle"),
    ("pcv", "Commercial Vehicle"),
    ("passenger carrying", "Commercial Vehicle"),
    ("commercial vehicle", "Commercial Vehicle"),
    ("misc d", "Commercial Vehicle"),
    ("misc-d", "Commercial Vehicle"),
    ("miscd", "Commercial Vehicle"),
    # Private Car variants
    ("four wheeler", "Private Car"),
    ("private car", "Private Car"),
    ("4w", "Private Car"),
    ("car", "Private Car"),
    ("taxi", "Private Car"),
    # Two Wheeler variants
    ("two wheeler", "Two Wheeler"),
    ("2w", "Two Wheeler"),
    ("bike", "Two Wheeler"),
    ("scooter", "Two Wheeler"),
    ("motorcycle", "Two Wheeler"),
    ("moped", "Two Wheeler"),
]

PRODUCT_CATEGORY_MAPPINGS = {
    "motor": "motor",
    "4w": "motor",
    "2w": "motor",
    "two wheeler": "motor",
    "four wheeler": "motor",
    "gcv": "motor",
    "pcv": "motor",
    "car": "motor",
    "bike": "motor",
    "commercial vehicle": "motor",
    "health": "health",
    "mediclaim": "health",
    "life": "life",
    "term": "life",
    "travel": "travel",
    "home": "home",
    "fire": "other",
    "marine": "other",
}

# Valid Indian state names (canonical lowercase-hyphenated)
VALID_STATE_NAMES = {
    "andaman-and-nicobar",
    "andhra-pradesh",
    "arunachal-pradesh",
    "assam",
    "bihar",
    "chandigarh",
    "chattisgarh",
    "delhi-ncr",
    "goa",
    "gujarat",
    "haryana",
    "jammu-and-kashmir",
    "himachal-pradesh",
    "karnataka",
    "jharkhand",
    "kerala",
    "lakshadweep",
    "madhya-pradesh",
    "mizoram",
    "maharashtra",
    "meghalaya",
    "manipur",
    "nagaland",
    "orissa",
    "pondicherry",
    "rajasthan",
    "punjab",
    "sikkim",
    "tamil-nadu",
    "telangana",
    "tripura",
    "west-bengal",
    "uttar-pradesh",
    "ladakh",
    "uttarakhand",
    "dadra-and-nagar-haveli-and-daman-and-diu",
}

# Maps various state name/abbreviation forms to canonical names
STATE_NAME_MAPPINGS = {
    "andaman and nicobar": "andaman-and-nicobar",
    "andaman & nicobar": "andaman-and-nicobar",
    "a&n": "andaman-and-nicobar",
    "andhra pradesh": "andhra-pradesh",
    "arunachal pradesh": "arunachal-pradesh",
    "assam": "assam",
    "bihar": "bihar",
    "chandigarh": "chandigarh",
    "chhattisgarh": "chattisgarh",
    "chattisgarh": "chattisgarh",
    "delhi": "delhi-ncr",
    "delhi ncr": "delhi-ncr",
    "goa": "goa",
    "gujarat": "gujarat",
    "haryana": "haryana",
    "himachal pradesh": "himachal-pradesh",
    "jammu and kashmir": "jammu-and-kashmir",
    "jammu & kashmir": "jammu-and-kashmir",
    "jharkhand": "jharkhand",
    "karnataka": "karnataka",
    # Misspelling seen in Chola's grids. Long enough to match as a bare
    # substring, so no boundary risk; the correctly-spelled key cannot cover it
    # because the 2-letter "ka" fallback is boundary-anchored and will not fire
    # inside a word.
    "karnatka": "karnataka",
    "kerala": "kerala",
    "ladakh": "ladakh",
    "lakshadweep": "lakshadweep",
    "madhya pradesh": "madhya-pradesh",
    "maharashtra": "maharashtra",
    "manipur": "manipur",
    "meghalaya": "meghalaya",
    "mizoram": "mizoram",
    "nagaland": "nagaland",
    "odisha": "orissa",
    "orissa": "orissa",
    "puducherry": "pondicherry",
    "pondicherry": "pondicherry",
    "punjab": "punjab",
    "rajasthan": "rajasthan",
    "sikkim": "sikkim",
    "tamil nadu": "tamil-nadu",
    "telangana": "telangana",
    "tripura": "tripura",
    "uttar pradesh": "uttar-pradesh",
    "uttarakhand": "uttarakhand",
    "west bengal": "west-bengal",
    "dadra and nagar haveli and daman and diu": "dadra-and-nagar-haveli-and-daman-and-diu",
    "dadra & nagar haveli and daman & diu": "dadra-and-nagar-haveli-and-daman-and-diu",
    "dadra and nagar haveli": "dadra-and-nagar-haveli-and-daman-and-diu",
    "dadra & nagar haveli": "dadra-and-nagar-haveli-and-daman-and-diu",
    "dadra and nagar": "dadra-and-nagar-haveli-and-daman-and-diu",
    "daman and diu": "dadra-and-nagar-haveli-and-daman-and-diu",
    "daman & diu": "dadra-and-nagar-haveli-and-daman-and-diu",
    "dnhdd": "dadra-and-nagar-haveli-and-daman-and-diu",
    "dd": "dadra-and-nagar-haveli-and-daman-and-diu",
    # 2-letter abbreviations.
    # KNOWN LIMITATION: _expand_state_names lowercases and boundary-matches these
    # short keys against free text, so tokens that collide with English words are
    # latently ambiguous — "as"/"an"/"up"/"la"/"ch" can misfire (e.g. "UP TO
    # 1000" resolves to uttar-pradesh today). "or" is deliberately omitted for
    # this reason ("MH or GJ" would acquire orissa; Chola uses OD). The real fix
    # is case-sensitive matching against the original text — its own change, its
    # own reconciliation run.
    "an": "andaman-and-nicobar",
    "ap": "andhra-pradesh",
    "ar": "arunachal-pradesh",
    "as": "assam",
    "bh": "bihar",
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
    "dn": "dadra-and-nagar-haveli-and-daman-and-diu",
    # "Rest of <state>" zone columns. Chola's CV grids pair an explicit metro
    # zone column with a "rest of" remainder column -- "TN-Chennai" beside
    # "ROTN", "Mumbai/GA/Pune/Central MH" beside "ROM". Both halves name the
    # same STATE; what separates them is the RTO set, and the export already
    # models that: the metro rule carries an explicit conditions.rto (seeded
    # from CarrierRTOZoneMaster) and the export's claimed-RTO complement
    # subtracts those codes from the remainder rule's state-derived fill. This
    # is the same shape as the already-shipped "UP" / "UP- East" pair.
    #
    # "rok" is the Two-Wheeler grid's remainder half, and its metro sibling is
    # the row keyed "KA (Bangalore)". That pair was NOT safe to map until the
    # metro row could claim an RTO set of its own: chola_rto_zone.csv seeds no
    # bangalore zone, so the sibling resolved to plain karnataka with no
    # explicit rto and mapping "rok" would have produced two identical
    # karnataka rules at different rates instead of a complement. It is safe
    # now -- _expand_rto_codes falls back to the GLOBAL RTO master's locality
    # index when the carrier's own zone master has no entry, so "KA
    # (Bangalore)" claims the 18 bangalore codes and the complement is real.
    # Removing that fallback means removing "rok" again.
    #
    # Note "rok" travels in a MULTI-STATE cell: the grid keys the row "TN/ROK",
    # i.e. all of Tamil Nadu plus the Karnataka remainder. Before this mapping
    # existed only "tn" matched and the Karnataka half was dropped from the
    # export entirely.
    "rok": "karnataka",
    "rom": "maharashtra",
    "rotn": "tamil-nadu",
}

# Tokens naming a GROUP of states rather than one. Consulted by
# _expand_state_names alongside STATE_NAME_MAPPINGS, with the same
# boundary-matching rules, but contributing several canonical names at once.
STATE_GROUP_MAPPINGS = {
    # Chola's Two-Wheeler grid spells the north-east block "AS/ML/TR/ AR/NL" in
    # one table and bare "NE" in the next table of the SAME sheet (same six
    # NOP/SOD/ANNUAL/ACT tiers, same key column) -- so NE is that group's
    # abbreviation, not a separate geography. Membership is taken verbatim from
    # the spelled-out sibling: Chola's north-east block excludes Manipur and
    # Mizoram, and this must not "helpfully" add them.
    "ne": [
        "assam",
        "meghalaya",
        "tripura",
        "arunachal-pradesh",
        "nagaland",
    ],
}


class _BoundaryKey(NamedTuple):
    """Structured, hashable boundary-normalization group key.

    ``lists`` holds one slot per kept list field (a sorted tuple or None),
    ``scalars`` one slot per scalar field, ``ranges`` the other range bounds.
    Splitting the key by kind lets ``_boundary_keys_compatible`` treat a None
    list slot as a wildcard — an unset ``fuel_type`` exports as all five fuels
    (commission_document.py), so a fuel-less rule genuinely overlaps a
    fuel-tagged ladder's bands and must collect evidence from it.
    """

    lists: Tuple
    scalars: Tuple
    ranges: Tuple


# product_subproduct -> the MMV / product-hierarchy master's product bucket.
# ONE definition, imported by both the transformer (which canonicalizes
# make/model inside a bucket) and CholaProductHierarchyValidator /
# CholaMMVValidator (which validate against the same buckets), for the same
# reason ``product_bucket`` has exactly one definition in the resolver: two
# call sites that disagree about what a product means would validate against a
# different vocabulary than the one that was written.
_SUBPRODUCT_TO_BUCKET = {
    "car": "Car",
    "two-wheeler": "TW",
}


def product_subproduct_bucket(product_subproduct: Optional[str]) -> Optional[str]:
    """Map a ``product_subproduct`` to ``Car`` / ``TW`` / ``CV``, or None.

    Commercial variants all collapse to CV; anything unrecognised returns
    None, which every caller treats as "say nothing about this product"
    rather than guessing a bucket.
    """
    if not product_subproduct:
        return None
    if product_subproduct in _SUBPRODUCT_TO_BUCKET:
        return _SUBPRODUCT_TO_BUCKET[product_subproduct]
    if product_subproduct.startswith("commercial"):
        return "CV"
    return None


def mmv_enabled() -> bool:
    """``CINS_CHOLA_MMV`` — gates BOTH the canonicalization here and
    ``CholaMMVValidator``.

    One switch on purpose: disabling only half would leave the validator
    flagging spellings the transformer would have folded, turning a rollback
    into a wall of findings.

    **Defaults to OFF.** Measured against a dump of 2,237 live Chola deals, the
    fold is now provably non-destructive (zero wrong make rewrites, against 93%
    of deals corrupted before the matcher was fixed) — but it is also barely
    worth its blast radius: it changes 737 of ~1.34M make values, all of them
    corporate-suffix tidy-ups, while half the makes those deals use are absent
    from the master entirely. Turning it on changes CICS output and so needs a
    ``CICS_PIPELINE_VERSION`` bump; leaving it off keeps the cache intact. Opt
    in once the master's coverage justifies it.
    """
    raw = os.environ.get("CINS_CHOLA_MMV")
    if raw is None:
        return False
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def gcv_no_subclass_enabled() -> bool:
    """``CINS_GCV_NO_SUBCLASS`` — GCV rules carry no ``vehicle_sub_class``.

    A goods-carrying grid prices by WEIGHT BAND, not by body type: Chola's
    GCCV sheet puts the weight ladder (``2_UPTO_3.5T``, ``3_3.5T_TO_7.5T``) in
    the column the extractor reads as SUBCLASS, so there is no body class in
    the source to extract. Recording one anyway made the export widen the
    ``not-available`` sentinel into all 15 seeded GCV bodies — a guess the
    grid never made.

    Defaults ON. ``false`` restores the pre-change behaviour without a
    redeploy, and is itself a ``CICS_PIPELINE_VERSION``-relevant change: a
    document cached under one setting keeps serving that setting's cell.
    """
    raw = os.environ.get("CINS_GCV_NO_SUBCLASS")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


# The four commercial-vehicle sub-products, spelled as _SEGMENT_TO_SUBPRODUCT
# emits them. Written out rather than derived from a ``commercial`` prefix so a
# future commercial sub-product has to opt in deliberately.
CV_SUBPRODUCTS = (
    "commercial-gcv",
    "commercial-pcv",
    "commercial-buses",
    "commercial-miscd",
)


def renewal_implies_rollover_enabled() -> bool:
    """``CINS_RENEWAL_IMPLIES_ROLLOVER`` — a CV renewal rate also prices rollover.

    A rollover IS a renewal that changes carrier, and no CV grid in the corpus
    prices the two apart: the carrier publishes one renewal rate card and books
    both against it. Exporting the bare ``renewal`` left a rollover quote with
    no matching deal at all.

    Scoped to ``CV_SUBPRODUCTS`` and carrier-agnostic — it is a fact about how
    commercial-vehicle rate cards are written, not about Chola. Private Car and
    Two Wheeler are deliberately excluded: their grids DO sometimes price
    rollover separately, and widening there would fabricate an overlap.

    Defaults ON. ``false`` restores the pre-change cell without a redeploy, and
    is itself a ``CICS_PIPELINE_VERSION``-relevant change.
    """
    raw = os.environ.get("CINS_RENEWAL_IMPLIES_ROLLOVER")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def miscd_subclass_merge_enabled() -> bool:
    """``CINS_MISCD_SUBCLASS_MERGE`` — MISC-D bodies share one rule.

    The client prices Agricultural Tractor and Harvester as their own deals and
    treats every other MISC-D body as one bucket. The per-body fan-out
    (``_bifurcate_subclass_buckets``) turned a single "Loader, Excavator, Crane,
    Bulldozer, Road Rollers, Fork Lift Trucks" grid cell into six deals at one
    rate, which is six rows to reconcile where the carrier books one.

    Defaults ON. ``false`` restores the per-body fan-out without a redeploy, and
    is itself a ``CICS_PIPELINE_VERSION``-relevant change.
    """
    raw = os.environ.get("CINS_MISCD_SUBCLASS_MERGE")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


class RawToCICSTransformer:
    """
    Transforms RawExtraction to full CICS schema.
    All transformations are deterministic and rule-based.
    """

    def __init__(self):
        """Initialize transformer with empty warnings/notices/errors lists."""
        self.warnings: List[str] = []
        # Informational audit trail of deliberate normalizations (boundary
        # bumps, fan-outs, recovered dimensions). Kept separate from
        # ``warnings``, which means "could not map / lost data": a CV document
        # emits hundreds of boundary bumps, and merging the two buried the real
        # failures and dragged the confidence score down for correct work.
        self.notices: List[str] = []
        self.errors: List[str] = []
        # NCB bands recovered from a label this run, collected so the notice is
        # emitted ONCE per document instead of once per rule (a large grid has
        # hundreds of NCB rows and notices reach the UI unfiltered).
        self._ncb_bands_parsed: List[Tuple[int, int]] = []
        self._trace_counter = 0
        # Short carrier code (e.g. "Chola") for carrier-scoped transforms.
        # Set per-document in transform(); empty until then.
        self._carrier_code: str = ""
        # Subproduct of the product currently being transformed (e.g.
        # "commercial-pcv"). Set per product group in _transform_product so the
        # per-rule helpers can scope themselves; empty outside that loop.
        self._product_subproduct: str = ""
        # Chola UW-zone -> {"rto": [...], "state": [...]} expansion map. Built
        # from a scoped CarrierRTOZoneMaster query in transform() when the
        # carrier is Chola; empty (and inert) for every other carrier or when
        # master_data is unavailable.
        self._carrier_zone_map: Dict[str, Dict[str, List[str]]] = {}
        # Global RTO master locality index: state slug -> locality -> RTO codes.
        # The fallback consulted when a carrier's own zone master has no entry
        # for a metro the grid names (e.g. Chola's "KA (Bangalore)"). Built
        # alongside _carrier_zone_map in transform(); empty (and inert)
        # otherwise.
        self._rto_locality_map: Dict[str, Dict[str, List[str]]] = {}
        # Chola Make/Model catalogue, keyed by product bucket. Built alongside
        # _carrier_zone_map in transform(); empty (and inert) otherwise, which
        # is what lets other modules instantiate this class purely to reuse a
        # normalization helper without touching the DB.
        self._mmv_index: Dict[str, dict] = {}
        # Dedup set for the "zone label not found in master" warning — one
        # warning per distinct unmatched text per document, not per rule.
        self._zone_warned: Set[str] = set()
        # Carrier body-class vocabulary, read lazily by
        # _canonical_subclass_slugs. ``None`` means "not read yet"; the DB must
        # not be touched here, since this class is constructed as a pure object
        # by CholaProductHierarchyValidator and by unit tests.
        self._carrier_subclass_rows: Optional[List[dict]] = None
        self._canonical_subclass_cache: Dict[Tuple[str, str, str], List[str]] = {}

    def transform(
        self, raw: RawExtraction, document_date: Optional[str] = None
    ) -> Tuple[CICSDocument, List[str], List[str]]:
        """
        Transform RawExtraction to CICSDocument.

        Args:
            raw: The raw extraction from AI
            document_date: ISO ``YYYY-MM-DD`` fallback for the commission period
                when the extraction found no date (e.g. the email's own send
                date). Prevents fabricating today's processing date. See
                ``parse_document_date``.

        Returns:
            Tuple of (CICSDocument, warnings, errors)
        """
        self.warnings = []
        self.notices = []
        self.errors = []
        self._trace_counter = 0
        self._zone_warned = set()
        self._product_subproduct = ""

        # Generate document metadata
        doc_metadata = self._transform_metadata(raw)

        # Transform provider
        provider = self._transform_provider(raw.provider)
        # Resolve the carrier code once for carrier-scoped transforms (Fix i).
        self._carrier_code = (
            normalize_carrier(getattr(raw.provider, "name", "") or "") or ""
        )

        # Chola UW-zone -> RTO/state expansion map (scoped query, 4A — not the
        # eager MasterDataResolver, which reloads every table for every
        # carrier). Guarded: DB-unavailable / unseeded degrades to an empty
        # map, i.e. today's behaviour (zone labels simply don't resolve).
        self._carrier_zone_map = {}
        self._rto_locality_map = {}
        self._mmv_index = {}
        if self._carrier_code == "Chola":
            try:
                from master_data.services.resolver import carrier_zone_expansions

                self._carrier_zone_map = carrier_zone_expansions("Chola")
            except Exception as e:
                logger.warning(f"Chola zone expansion map unavailable: {e}")
                self._carrier_zone_map = {}
            # Global-master locality index, the fallback for a metro label the
            # carrier's own zone master does not cover ("KA (Bangalore)").
            # Loaded separately so a failure on either side leaves the other
            # working, and scoped to Chola for now because that is the only
            # carrier whose grids key rows by a metro-vs-remainder pair.
            try:
                from master_data.services.resolver import rto_locality_expansions

                self._rto_locality_map = rto_locality_expansions()
            except Exception as e:
                logger.warning(f"RTO locality index unavailable: {e}")
                self._rto_locality_map = {}
            # Make/Model catalogue, used to fold a rule's free-text make/model
            # onto one spelling. Same shape of guard as the two above: a
            # DB-unavailable or unseeded master leaves the map empty, which
            # means every make/model passes through verbatim — today's
            # behaviour.
            if mmv_enabled():
                try:
                    from master_data.services.mmv import build_mmv_index

                    self._mmv_index = build_mmv_index("Chola")
                except Exception as e:
                    logger.warning(f"Chola MMV index unavailable: {e}")
                    self._mmv_index = {}

        # Transform commission period
        period = self._transform_period(raw, document_date)

        # Group entries by product and transform
        products = self._transform_products(
            raw.commission_entries, raw.master_data, raw
        )

        # Generate source traces
        source_traces = self._generate_source_traces(raw.commission_entries, products)

        # One aggregated NCB-band notice for the whole document. Emitted here
        # rather than per rule in _transform_conditions: notices reach the DB and
        # the detail page unfiltered, so a per-rule append floods the panel and
        # buries the notices that matter. The distinct band list is the useful
        # part — it shows at a glance which tiers a document produced.
        if self._ncb_bands_parsed:
            distinct = sorted(set(self._ncb_bands_parsed))
            bands = ", ".join(f"{lo}-{hi}" for lo, hi in distinct)
            self.notices.append(
                f"NCB band parsed from label on {len(self._ncb_bands_parsed)} "
                f"rule(s); bands: {bands}"
            )

        # Build change reference if inheritance detected
        change_ref = self._transform_change_reference(raw)

        # Generate validation block
        validation = self._generate_validation(raw, products)

        doc = CICSDocument(
            schema_version="1.0",
            document_metadata=doc_metadata,
            provider=provider,
            commission_period=period,
            products=products,
            change_reference=change_ref,
            source_traces=source_traces,
            validation=validation,
        )

        return doc, self.warnings, self.errors

    # ==================== METADATA ====================

    def _transform_metadata(self, raw: RawExtraction) -> DocumentMetadata:
        """Transform metadata from raw extraction."""
        document_id = self._generate_document_id(raw)

        source_type = None
        if raw.source_type:
            st = raw.source_type.lower()
            if st in ["email", "pdf", "excel", "api", "manual"]:
                source_type = st

        return DocumentMetadata(
            document_id=document_id,
            source_type=source_type,
            source_file=raw.source_file,
            extracted_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            extractor="raw_to_cics_transformer",
            notes=raw.extraction_notes,
            additional_info={
                "master_data_count": len(raw.master_data) if raw.master_data else 0
            },
        )

    def _generate_document_id(self, raw: RawExtraction) -> str:
        """
        Generate document_id from provider + period + hash.
        Pattern: {PROVIDER}_{PERIOD}_{HASH}
        """
        provider_slug = self._slugify(raw.provider.name)

        # Parse period
        period_slug = self._extract_period_slug(raw)

        # Short hash for uniqueness
        content_hash = hashlib.md5(
            f"{raw.provider.name}{raw.start_date}".encode()
        ).hexdigest()[:6]

        return f"{provider_slug}_{period_slug}_{content_hash}"

    def _extract_period_slug(self, raw: RawExtraction) -> str:
        """Extract a period slug from raw extraction."""
        if raw.period_label:
            return self._slugify(raw.period_label)

        # Try to parse from start_date
        parsed = self._parse_date(raw.start_date)
        if parsed:
            return parsed[:7].replace("-", "")  # YYYYMM

        return "UNKNOWN"

    # ==================== PROVIDER ====================

    def _transform_provider(self, raw_provider) -> Provider:
        """
        Transform provider from raw extraction.

        Normalises whatever the LLM returned (or the alias the codegen path
        passes through) to one of the 25 canonical short codes via
        `cins.schema.carriers.normalize_carrier`. Anything that fails to map
        becomes `UNKNOWN_PROVIDER` and a transformer warning, so downstream
        callers can flag it.
        """
        raw_name = (raw_provider.name or "").strip()
        short_code = normalize_carrier(raw_name)

        if short_code:
            # `provider_id` must match ^[A-Za-z0-9_-]+$ per CICS schema —
            # spaces in canonical codes (e.g. "New India") are converted to
            # underscores for the ID, but the human-facing `provider_name`
            # uses the canonical full name from the alias table.
            return Provider(
                provider_id=short_code.replace(" ", "_"),
                provider_name=CARRIER_FULL_NAMES.get(short_code, short_code),
            )

        # No alias match — keep the original string visible (so a human can
        # see what the LLM returned) but flag the doc as needing review.
        if raw_name:
            self.warnings.append(
                f"Provider '{raw_name}' did not match any of the 25 valid carriers; "
                f"setting provider_id=UNKNOWN_PROVIDER."
            )
        return Provider(
            provider_id="UNKNOWN_PROVIDER",
            provider_name=raw_name or "Unknown Provider",
        )

    # ==================== PERIOD ====================

    def _transform_period(
        self, raw: RawExtraction, document_date: Optional[str] = None
    ) -> CommissionPeriod:
        """Transform commission period from raw extraction.

        Fallback order for the required ``effective_from``:
        1. the extracted ``start_date`` (LLM/prompt path),
        2. the document's own date (email ``Date:`` header) — deterministic,
        3. today's processing date, only as a last resort and with a warning,
           since it fabricates a period that is always wrong.
        """
        effective_from = self._parse_date(raw.start_date)
        if not effective_from and document_date:
            effective_from = document_date
            self.warnings.append(
                f"No commission period found; using document date "
                f"{document_date} as effective_from."
            )
        if not effective_from:
            self.warnings.append(
                f"No commission period and no document date; falling back to "
                f"today. Raw start_date was: {raw.start_date!r}"
            )
            effective_from = datetime.utcnow().strftime("%Y-%m-%d")

        effective_to = None
        if raw.end_date:
            effective_to = self._parse_date(raw.end_date)
            if not effective_to:
                self.warnings.append(f"Could not parse end_date: {raw.end_date}")

        return CommissionPeriod(
            effective_from=effective_from,
            effective_to=effective_to,
            period_label=raw.period_label,
        )

    # ==================== PRODUCTS & RULES ====================

    def _transform_products(
        self,
        entries: List[RawCommissionEntry],
        master_data: Optional[List[RawMasterDataEntry]],
        raw: RawExtraction,
    ) -> List[Product]:
        """Group entries by product and transform to Product objects."""
        # Group entries by product. Commercial-vehicle entries additionally
        # split by segment (GCV / PCV / Buses / MISC-D) so each becomes its own
        # SubProduct — the source publishes them as separate tables (PROD column
        # GCCV / PCCV<6 / PCCV>6 / MSV) but the LLM normalises product_name to a
        # single "Commercial Vehicle", which would otherwise collapse all four
        # into one product with one SubProduct.
        product_groups: Dict[str, List[RawCommissionEntry]] = {}
        group_meta: Dict[str, tuple] = {}
        stale_gccv_dropped: List[str] = []

        for entry in entries:
            product_key = self._get_product_key(entry)
            segment = self._cv_segment_label(entry) or self._car_segment_label(entry)
            # Stale merged-header guard (Chola). Chola's CV tables reuse one
            # copy-pasted header template, so the GCCV table's trailing
            # "1_GCCV_3W" merged-header values also sit in the PCCV<6 / PCCV>6 /
            # MSV headers as STALE duplicates. When Pass-3 codegen fails to honour
            # the "recover the merged 3W header only for the GCCV table" rule
            # (chola_pass3.md) it emits those values as real rows that segment
            # into PCV / Buses / MISC-D (via a stale source_section) while still
            # carrying a "GCCV ..." rule_text — a phantom copy of a real GCV row.
            # A goods (GCCV) label in a passenger / misc segment is unambiguously
            # a leak; drop the entry before grouping so it never inflates the
            # non-GCV product (and never forms an all-leak empty product). The
            # legitimate GCV copy is untouched. Deterministic backstop for a
            # non-deterministic codegen bug.
            #
            # The token is matched ANYWHERE in the rule text, not just as a
            # prefix. The real leak on the May'26 grid spells it "GCV 1_GCCV_3W
            # AP ACT" — the segment slug carries the GCCV token while the row
            # opens with the shorter "GCV", so a startswith() test missed all
            # 222 phantom rows (74 into each of PCV / Buses / MISC-D) and 148 of
            # them exported as MISC-D auto-rickshaws: a three-wheeled goods
            # carrier filed as a miscellaneous-D deal, which is not a vehicle
            # the carrier sells under that sub-product at all.
            if (
                self._carrier_code == "Chola"
                and segment in ("PCV", "Buses", "MISC-D")
                and self._names_gccv(entry)
            ):
                # Record each dropped row so a false positive is auditable in
                # the validation report — not just an aggregate count.
                stale_gccv_dropped.append(
                    f"{segment} <- {(entry.rule_text or '').strip()[:80]}"
                )
                continue
            group_key = f"{product_key}::{segment}" if segment else product_key
            if group_key not in product_groups:
                product_groups[group_key] = []
                group_meta[group_key] = (product_key, segment)
            product_groups[group_key].append(entry)

        if stale_gccv_dropped:
            preview = "; ".join(stale_gccv_dropped[:10])
            more = (
                f" (+{len(stale_gccv_dropped) - 10} more)"
                if len(stale_gccv_dropped) > 10
                else ""
            )
            self.warnings.append(
                f"Dropped {len(stale_gccv_dropped)} stale GCCV merged-header "
                f"row(s) leaked into non-GCV commercial segments (codegen "
                f"merged-header duplicate): {preview}{more}"
            )

        # Transform each product group
        products = []
        for group_key, group_entries in product_groups.items():
            product_key, segment = group_meta[group_key]
            product = self._transform_product(
                product_key, group_entries, master_data, raw, segment=segment
            )
            products.append(product)

        return products

    # Word-boundary GCCV token. Anchored so it can never fire on a longer word,
    # and deliberately NOT matching the shorter "GCV": that token opens most
    # legitimate commercial rows of every segment, while "GCCV" is the goods
    # table's own PROD label and has no business in a passenger / misc row.
    _GCCV_TOKEN_RE = re.compile(r"(?<![a-z0-9])gccv(?![a-z0-9])")

    def _names_gccv(self, entry: RawCommissionEntry) -> bool:
        """Does this entry carry the goods table's own GCCV label?

        Checked across the rule text AND the raw ``vehicle_sub_class`` slug,
        because the leak is a merged-header value: the slug ("1_GCCV_3W") is
        where the token actually lives, and the rule text merely happens to
        repeat it. Reading only one of the two is how the original prefix test
        came to miss the whole class.
        """
        raw_sub_class = (getattr(entry, "conditions", None) or {}).get(
            "vehicle_sub_class"
        )
        for text in (entry.rule_text, raw_sub_class):
            if not text:
                continue
            if isinstance(text, (list, tuple, set)):
                text = " ".join(str(t) for t in text)
            if self._GCCV_TOKEN_RE.search(str(text).lower()):
                return True
        return False

    def _get_product_key(self, entry: RawCommissionEntry) -> str:
        """Get a product key from an entry."""
        if entry.product_name:
            return entry.product_name
        if entry.product_type:
            return entry.product_type
        return "DEFAULT_PRODUCT"

    # Canonical commercial-vehicle segment label → product_subproduct value.
    _SEGMENT_TO_SUBPRODUCT = {
        "GCV": "commercial-gcv",
        "PCV": "commercial-pcv",
        "Buses": "commercial-buses",
        "MISC-D": "commercial-miscd",
    }
    # Car segment labels (SubProduct for the Car product).
    _CAR_SEGMENTS = ("Private Car", "Taxi")

    def _transform_product(
        self,
        product_key: str,
        entries: List[RawCommissionEntry],
        master_data: Optional[List[RawMasterDataEntry]],
        raw: RawExtraction,
        segment: Optional[str] = None,
    ) -> Product:
        """Transform a group of entries into a Product.

        ``segment`` is the commercial-vehicle sub-product (GCV / PCV / Buses /
        MISC-D) when the caller has already split the group by segment; it
        pins product_subproduct and sub_category so each segment exports as its
        own SubProduct.
        """
        # Determine product code and category
        product_code = self._slugify(product_key).upper()
        if segment:
            seg_slug = self._slugify(segment).upper()
            if not product_code.endswith(seg_slug):
                product_code = f"{product_code}_{seg_slug}"
        product_category = self._detect_product_category(product_key, entries)

        # Detect product-level metadata up-front so we can gate per-rule
        # backfills (e.g. wheel-category extraction) by subproduct. A commercial
        # segment pins the commercial-* subproduct; a car segment (Private Car /
        # Taxi) keeps the "car" subproduct while still splitting the SubProduct.
        if segment in self._SEGMENT_TO_SUBPRODUCT:
            product_subproduct = self._SEGMENT_TO_SUBPRODUCT[segment]
        elif segment in self._CAR_SEGMENTS:
            product_subproduct = "car"
        else:
            product_subproduct = self._detect_product_subproduct(product_key, entries)
        # Expose it for the per-rule helpers (see _apply_chola_pcv_subclass);
        # _transform_conditions is several frames down and has no other route
        # to the product it belongs to.
        self._product_subproduct = product_subproduct or ""
        vehicle_type = self._detect_vehicle_type(product_key, entries)

        # Compute priorities based on specificity
        priorities = self._compute_priorities(entries)

        # Extract condition hints encoded in the product key (e.g. "TW SATP Scooter")
        key_policy_type = self._extract_policy_type_from_key(product_key)
        key_sub_class = self._extract_sub_class_from_key(product_key)
        # Wheel hint is only meaningful for commercial vehicles. "4W" in a
        # Private Car product name does not mean number_of_wheels, it means
        # the product itself.
        # LLM Pass 3 normalises product_name to the canonical "Commercial
        # Vehicle" label and strips slug suffixes, so wheel info baked into
        # the original sheet header (e.g. ``1_GCCV_3W``) survives only in
        # entries' source_section or in the raw global_vehicle_type_text.
        is_commercial = bool(
            product_subproduct and product_subproduct.startswith("commercial-")
        )
        # Chola treats vehicle_sub_class as mandatory on every rule, so its
        # non-commercial products get the same section/rule_text sub-class probe
        # commercial ones already had (see the backfill block below).
        is_chola = self._carrier_code == "Chola"
        product_key_wheels = (
            self._extract_wheels_from_key(product_key) if is_commercial else None
        )
        global_wheels = (
            self._extract_wheels_from_key(
                getattr(raw, "global_vehicle_type_text", "") or ""
            )
            if is_commercial
            else None
        )

        # Transform each entry to a rule
        rules = []
        for i, entry in enumerate(entries):
            priority = priorities.get(i, (i + 1) * 10)
            rule = self._transform_rule(
                entry, product_code, i, priority, master_data, raw
            )
            # Backfill product-key hints. Materialise an empty RuleConditions
            # on rules that had none so commercial-vehicle wheel inference
            # can land. Without this, `_transform_rule` drops the conditions
            # object whenever the LLM emitted only outcome/text fields, and
            # downstream bifurcation never sees the rule.
            # The GCV section token (e.g. "1_3W_AUTO", "1_TRAC", "2_EXCAVATOR")
            # commonly survives only in the rule's free text, not in
            # source_section, so probe both for wheel- and sub-class hints.
            section = getattr(entry, "source_section", "") or ""
            rule_text = getattr(entry, "rule_text", "") or ""
            entry_wheels = None
            entry_sub_class = None
            if is_commercial or is_chola:
                for hint_src in (section, rule_text):
                    if not hint_src:
                        continue
                    if is_commercial:
                        entry_wheels = entry_wheels or self._extract_wheels_from_key(
                            hint_src
                        )
                    entry_sub_class = (
                        entry_sub_class or self._extract_sub_class_from_key(hint_src)
                    )
            inferred_wheels = (
                entry_wheels or product_key_wheels or global_wheels
                if is_commercial
                else None
            )
            inferred_sub_class = key_sub_class or entry_sub_class
            needs_backfill = bool(
                key_policy_type or inferred_sub_class or inferred_wheels
            )
            # GCV with no wheel hint anywhere must still surface a
            # conditions object so bifurcation fans it out into 3W + 4W.
            if product_subproduct == "commercial-gcv" and not inferred_wheels:
                needs_backfill = True
            if needs_backfill and rule.conditions is None:
                rule.conditions = RuleConditions()
            if rule.conditions is not None:
                if key_policy_type and not rule.conditions.policy_type:
                    rule.conditions.policy_type = key_policy_type
                if inferred_sub_class and not rule.conditions.vehicle_sub_class:
                    rule.conditions.vehicle_sub_class = inferred_sub_class
                if inferred_wheels and not rule.conditions.number_of_wheels:
                    rule.conditions.number_of_wheels = inferred_wheels

            # Chola last-resort: derive the sub-class from the product segment
            # when no grid label carried a body word. Only "car" has an
            # unambiguous default — a Chola Car grid with no body word is the
            # Private Car book, and the Taxi / Kaali Peeli books always label
            # themselves. Two-wheeler is deliberately NOT defaulted: scooter,
            # bike, e-bike and moped are genuinely distinct sub-classes with no
            # majority case, so a hint-less TW rule is left empty for
            # CholaProductHierarchyValidator to flag rather than guessed at.
            # Commercial keeps the hint path above for the same reason.
            if (
                is_chola
                and product_subproduct == "car"
                and (rule.conditions is None or not rule.conditions.vehicle_sub_class)
            ):
                probe = " ".join(p for p in (product_key, segment or "", section) if p)
                warn_mark = len(self.warnings)
                derived = self._normalize_list_field(
                    probe, CAR_SUB_CLASS_PROBE_MAPPINGS, "vehicle_sub_class"
                )
                # A no-match here is the default case, not an extraction gap —
                # drop the warning _normalize_list_field appends for it.
                del self.warnings[warn_mark:]
                if rule.conditions is None:
                    rule.conditions = RuleConditions()
                rule.conditions.vehicle_sub_class = derived or ["private-car"]

            rules.append(rule)

        # Recover the electric goods 3-wheeler BEFORE any collapse/dedupe, while
        # rules are still in codegen emission order (base value emitted before
        # its electric twin). Deterministic net for the merged-header 3W cell's
        # second trailing value when codegen fails to tag it Electric.
        if self._carrier_code == "Chola" and product_subproduct == "commercial-gcv":
            self._recover_gcv_3w_electric(rules)

        # Pair NCB>25% rows with their base twins BEFORE any dedupe/bifurcation.
        # ncb is a _DEDUPE_SCALAR_FIELD, so running this first lets the two
        # ladders (True / False) normalize their CC boundaries independently;
        # running after dedupe would fuse them. Depends on Change A2 — without
        # the bracket-qualifier fix the NCB>25% label 'UPTO 1000 CC[ NCB GT 25%]'
        # parses to cc=(1000,25) and never shares a condition key with its base
        # twin, so pairing finds nothing.
        self._pair_ncb_tiers(rules)

        # De-overlap GVW / CC / seating-capacity boundaries within groups of
        # rules that share every other condition. Source documents express
        # buckets with shared boundaries ("UPTO 3.5T" / "3.5T TO 7.5T"), which
        # the parser maps to (0,3500) and (3500,7500). Downstream consumers
        # treat ranges as inclusive on both ends, so a vehicle at 3500 would
        # match two rules. Bump each successive start to prev_end + 1 so the
        # ladder is monotonic and gap-free.
        # Collapse identical-condition + identical-outcome duplicates before
        # boundary dedupe. LLM extraction often emits the same logical rule
        # multiple times with slightly different vehicle_make values (one per
        # source-table row); leaving them confuses the boundary-bump walk,
        # which can leave the second duplicate at the un-bumped boundary
        # (invert-guard hits).
        # Make/model bucket fan-out (R3). The Chola GCCV ≤3.5T grid publishes a
        # sentinel "All Other Make/Models" row next to an explicit make/model
        # row. _collapse_duplicate_rules ignores make/model, so without this it
        # would drop the sentinel — split it into two exclusion buckets FIRST.
        # Chola-gated + commercial-only: a cross-carrier sentinel regex would
        # re-expand the TW junk "Others" rows the collapse exists to kill (same
        # gating pattern as _expand_fuel_group).
        if (
            self._carrier_code == "Chola"
            and product_subproduct
            and product_subproduct.startswith("commercial")
        ):
            rules = self._bifurcate_make_buckets(rules)
            # Fan out MSV / MISC-D body enumerations (relocated into
            # vehicle_sub_class by _transform_conditions) into one distinct deal
            # per subclass. Runs before collapse so identical state/coverage
            # clones dedupe; the collapse key keeps vehicle_sub_class, so the
            # distinct-subclass clones themselves survive.
            rules = self._bifurcate_subclass_buckets(rules, product_subproduct)

        # A CV renewal rate also prices rollover. Runs BEFORE the collapse so a
        # widened "renewal" rule and a natively "renewal,rollover" rule at the
        # same rate dedupe for free; at different rates both survive, as a
        # genuine conflict for the overlap checker to report.
        self._imply_rollover_on_renewal(rules, product_subproduct)

        rules = self._collapse_duplicate_rules(rules)

        self._dedupe_range_overlaps(rules)

        # Evidence-gated cross-group boundary normalization (D1=B). Closes the
        # gap when a CC/GVW ladder is fragmented across states/RTOs so the
        # per-group bump above can't see the lower rung. Runs before GCV
        # bifurcation so wheel clones inherit corrected boundaries.
        self._normalize_boundary_overlaps(rules)

        # GCV rules must be scoped to a single number_of_wheels value.
        # Source documents commonly publish one GCV row that implicitly
        # covers both 3W and 4W & Above (or lists both in one cell), but
        # downstream deal management requires one deal per wheel category.
        if product_subproduct == "commercial-gcv":
            rules = self._bifurcate_gcv_wheels(rules)

        # Dimension-collapse guard: a commercial-vehicle product whose every
        # rule still carries the (0, 0) "no GVW filter" sentinel almost always
        # means the GVW dimension was lost in extraction (and _recover_gvw_range
        # could not recover it). Surface it so the next silently-dropped
        # dimension is visible instead of shipping wrong commission tiers.
        if (
            product_subproduct
            and product_subproduct.startswith("commercial")
            and rules
            and all(
                r.conditions
                and getattr(r.conditions, "gvw_start_range", 0) == 0
                and getattr(r.conditions, "gvw_end_range", 0) == 0
                for r in rules
            )
        ):
            self.warnings.append(
                f"Dimension-collapse: all {len(rules)} rules in commercial "
                f"product '{product_key}' have no GVW band — possible lost "
                f"weight dimension"
            )

        # Fuel bifurcation completeness (commercial). When a commercial product
        # carries a dedicated Electric row, the fuel-agnostic siblings
        # (fuel_type None) implicitly mean "all fuels" — which OVERLAPS the
        # Electric rows, and the export renders None as the full
        # "Petrol,Diesel,CNG,LPG,Electric" default. Pin those siblings to the
        # four non-electric fuels so Electric vs Non-Electric are clean,
        # separate deals (the requested GCV 3W/4W fuel bifurcation).
        if product_subproduct and product_subproduct.startswith("commercial"):
            has_electric = any(
                r.conditions
                and r.conditions.fuel_type
                and "electric" in r.conditions.fuel_type
                for r in rules
            )
            if has_electric:
                for r in rules:
                    if r.conditions and not r.conditions.fuel_type:
                        r.conditions.fuel_type = ["petrol", "diesel", "cng", "lpg"]

        # Second boundary-normalization pass (defect 8 completeness). The pass
        # above runs BEFORE GCV wheel bifurcation so wheel clones inherit
        # corrected boundaries, but a fuel-less band whose only lower rung lives
        # in a fuel-tagged sibling ladder is not evidence-complete until that
        # ladder is fully materialized (bifurcation + fuel completeness). Example
        # (Chola April GCV): 7 third-party fuel-less 40000-43000 bands stayed on
        # 40000 while their comprehensive four-fuel siblings, whose rung ends at
        # 40000, only line up after this point. The pass is evidence-gated and
        # idempotent, so re-running never bumps an already-monotonic ladder.
        self._normalize_boundary_overlaps(rules)

        # GCV carries no body dimension — strip it, LAST. See
        # _strip_gcv_subclass for why this cannot move earlier.
        if self._strip_gcv_subclass(rules, product_subproduct):
            # The strip can make subclass-only clones byte-identical, which
            # RuleOverlapChecker reports as a CRITICAL complete_overlap. Collapse
            # them here; the pass only merges when the OUTCOME matches too, so
            # differently-priced bodies survive as the genuine conflict they now
            # are.
            rules = self._collapse_duplicate_rules(rules)

        # Determine sub-category. For a split commercial group the segment is
        # the canonical SubProduct (GCV / PCV / Buses / MISC-D) — use it
        # verbatim so the export SubProduct column matches PRODUCT_FIELD_VALUES.
        # A two-wheeler group is never segment-split, so without an explicit pin
        # it fell through to the raw grid token and exported "2w"; its canonical
        # SubProduct is the product name itself.
        sub_category = None
        if segment:
            sub_category = segment
        elif product_subproduct == "two-wheeler":
            sub_category = "Two Wheeler"
        elif entries:
            first_entry = entries[0]
            if first_entry.product_type:
                sub_category = first_entry.product_type.lower()

        product_name = self._normalize_product_name(product_key, entries)

        return Product(
            product_code=product_code,
            product_name=product_name,
            product_category=product_category,
            sub_category=sub_category,
            commission_rules=rules,
            product_subproduct=product_subproduct,
            vehicle_type=vehicle_type,
        )

    def _normalize_product_name(
        self, product_key: str, entries: List[RawCommissionEntry]
    ) -> str:
        """Normalize raw product key to a canonical product name.

        Prefers the canonical name extracted by the LLM in Pass 3 (stored in
        entry.product_name when it differs from the raw key). Falls back to
        static PRODUCT_NAME_MAPPINGS substring matching.
        """
        _canonical = {"Private Car", "Two Wheeler", "Commercial Vehicle"}
        for entry in entries:
            if entry.product_name and entry.product_name in _canonical:
                return entry.product_name
        key_lower = product_key.lower()
        for pattern, canonical in PRODUCT_NAME_MAPPINGS:
            if pattern in key_lower:
                return canonical
        return product_key

    def _extract_policy_type_from_key(self, product_key: str) -> Optional[List[str]]:
        """Extract policy type hint encoded in a product key (e.g. 'SATP', 'Comp', 'SAOD')."""
        key_lower = product_key.lower()
        # Order matters — check most specific first
        hints = [
            ("satp", "third-party"),
            ("saod", "saod"),
            ("comp", "comprehensive"),
            (" tp ", "third-party"),
            ("/tp", "third-party"),
            ("third party", "third-party"),
            ("third-party", "third-party"),
        ]
        for pattern, policy in hints:
            if pattern in key_lower:
                return [policy]
        return None

    # A body word that names a DIFFERENT canonical sub-class depending on which
    # sub-product's grid it appears in. The hint table below is shared across
    # sub-products and matches bare tokens, so an abbreviated section key has no
    # way to disambiguate on its own: Chola writes the MISC-D agricultural
    # tractor as "1_TRAC", which falls through the table to the bare "trac" arm
    # and lands on "tractor" — a GOODS body that the carrier's own hierarchy
    # master files under GCV, not MISC-D (CV/GCV/Tractor vs
    # CV/MISC-D/Agricultural Tractor). Two different vehicles at two different
    # payouts. Applied AFTER the shared scan so a GCV "TRAC" row keeps
    # resolving to "tractor" — this narrows a result, it never widens one.
    _SUBPRODUCT_SUBCLASS_OVERRIDES = {
        "commercial-miscd": {"tractor": "agricultural-tractor"},
    }

    def _apply_subproduct_subclass_override(self, values):
        """Re-file a body word that means something else in this sub-product.

        Called from BOTH routes a sub-class can arrive by — the product-key
        hint table and the codegen segment slug in ``conditions`` — because
        "1_TRAC" reaches the rule down the second one. Reads
        ``self._product_subproduct``, which ``_transform_product`` sets before
        either route runs.

        Never widens: an unmapped value is returned untouched, and a
        sub-product with no override table is a no-op.
        """
        overrides = self._SUBPRODUCT_SUBCLASS_OVERRIDES.get(
            getattr(self, "_product_subproduct", "") or ""
        )
        if not overrides or not values:
            return values
        return [overrides.get(v, v) for v in values]

    def _extract_sub_class_from_key(self, product_key: str) -> Optional[List[str]]:
        """Extract vehicle sub-class hint encoded in a product key (e.g. 'Scooter', 'School Bus')."""
        key_lower = product_key.lower()
        # Longer/more-specific patterns first to avoid partial matches.
        # The bare tokens (school / staff / trac / excavator / harvest / maxi)
        # match the abbreviated GCV section keys Chola bakes into the row text
        # (e.g. "1_TRAC", "2_STAFF", "2_EXCAVATOR", "3_HARVESTOR", "4_MAXI").
        # These special-purpose CV bodies carry no GVW band and no wheel token,
        # so without a sub-class hint they collapse into the (0,0) catch-all and
        # get cross-cloned into 3W by bifurcation. The matched sub-class also
        # routes them to 4W-and-Above only (see _is_four_wheel_only_subclass).
        hints = [
            ("school bus", "school-bus"),
            ("staff bus", "staff-bus"),
            ("passenger bus", "passenger-bus"),
            ("mini bus", "mini-bus"),
            ("agri tractor", "agricultural-tractor"),
            ("agricultural tractor", "agricultural-tractor"),
            ("school", "school-bus"),
            ("staff", "staff-bus"),
            ("excavator", "excavator"),
            ("harvest", "harvester"),
            ("maxi", "maxicab"),
            ("trac", "tractor"),
            ("scooter", "scooter"),
            ("e-rickshaw", "e-rickshaw"),
            ("e rickshaw", "e-rickshaw"),
            # Two-wheeler bodies. Chola requires a sub-class on every rule, and
            # the TW grids encode the body in the section/product label only.
            # "e-bike" precedes "bike" so the longer token wins.
            ("e-bike", "e-bike"),
            ("e bike", "e-bike"),
            ("moped", "moped"),
            ("bike", "bike"),
        ]
        for pattern, sub_class in hints:
            if pattern in key_lower:
                return self._apply_subproduct_subclass_override([sub_class])
        return None

    # Stems of special-purpose CV bodies that are always 4-Wheel-and-Above
    # (never 3-wheelers). A wheel-less rule scoped to one of these must
    # bifurcate to 4W only — cloning it to 3W produces spurious rows and (0,0)
    # catch-alls with conflicting rates. Matched by substring so plurals and
    # variants from either our extractor or the LLM codegen all resolve (e.g.
    # "excavator" / "excavators", "tractor" / "agricultural-tractor").
    _FOUR_WHEEL_ONLY_SUBCLASS_STEMS = (
        "bus",
        "tractor",
        "excavator",
        "harvest",
        "maxicab",
        "maxi-cab",
        "dumper",
        "tipper",
        "trailer",
        "tanker",
        "crane",
    )

    @classmethod
    def _is_four_wheel_only_subclass(cls, conditions) -> bool:
        """True when conditions carry a vehicle_sub_class that is 4W-only."""
        if conditions is None:
            return False
        sub = getattr(conditions, "vehicle_sub_class", None)
        if not sub:
            return False
        values = sub if isinstance(sub, (list, tuple)) else [sub]
        return any(
            stem in str(v).lower()
            for v in values
            for stem in cls._FOUR_WHEEL_ONLY_SUBCLASS_STEMS
        )

    # GCV-3W sub-class slug ("1_GCCV_3W", "GCV 3W"). Matches gcv/gccv followed
    # by a 3-wheeler token, tolerant of underscore/space/hyphen separators.
    _GCV_3W_SUBCLASS_RE = re.compile(r"g[c]{1,2}v[_\s-]*3\s*w", re.IGNORECASE)

    @classmethod
    def _is_all_gvw_three_wheeler(cls, number_of_wheels, subclass_probe="") -> bool:
        """True for a goods 3-wheeler row (all-GVW, never weight-graded).

        The GCV-3W merged-header row the Chola overlay recovers is scoped to 3W
        (number_of_wheels="3W", vehicle_sub_class="1_GCCV_3W") and carries NO gvw
        band. Signal is either the explicit 3W wheel token (WITHOUT a paired 4W
        value — a mixed 3W+4W rule may carry a real weight grade) or a GCV-3W
        sub-class slug. The slug is probed from the RAW entry text
        (source_section / rule_text / raw sub-class) because _normalize_list_field
        drops the unmapped "1_GCCV_3W" token before it reaches the transformed
        vehicle_sub_class — same reason the [Electric] backstop probes raw text.
        Used to widen the degenerate (0, 0) gvw default to the open (0, 999999)
        ceiling.
        """
        wheels = [str(w).lower() for w in (number_of_wheels or [])]
        if "3w" in wheels and "4w-and-above" not in wheels:
            return True
        return bool(cls._GCV_3W_SUBCLASS_RE.search(subclass_probe or ""))

    def _extract_wheels_from_key(self, product_key: str) -> Optional[List[str]]:
        """Extract number_of_wheels hint encoded in a commercial-vehicle product key.

        Source documents commonly publish CV grids with the wheel category
        baked into the sheet/section header, e.g. ``"1_GCCV_3W"``,
        ``"GCCV 4W & ABOVE"``, ``"PCV 3-Wheeler"``. The wheel value never
        appears as a per-row condition in such grids, so without this
        extractor the entire sheet's rules end up with no number_of_wheels.

        Callers must gate this on a commercial subproduct: "4W" inside a
        Private Car product key means the product itself, not a wheel count.
        """
        # Use a tokenised match so substrings inside other tokens don't trip
        # the pattern (e.g. "g4w" or "34W"). Word-ish boundaries treat
        # underscores, hyphens, spaces, and slashes as separators.
        boundary = r"(?:^|[\s_\-/])"
        after = r"(?=$|[\s_\-/])"
        key = product_key.lower()

        three_patterns = [
            rf"{boundary}3\s*w{after}",
            rf"{boundary}3\s*-?\s*wheeler{after}",
            rf"{boundary}three\s*-?\s*wheeler{after}",
        ]
        four_patterns = [
            rf"{boundary}4\s*w(?:\s*(?:&|and)\s*above)?{after}",
            rf"{boundary}4\s*-?\s*wheeler{after}",
            rf"{boundary}four\s*-?\s*wheeler{after}",
        ]

        wheels: List[str] = []
        if any(re.search(p, key) for p in three_patterns):
            wheels.append("3w")
        if any(re.search(p, key) for p in four_patterns):
            wheels.append("4w-and-above")
        return wheels or None

    def _detect_product_category(
        self, product_key: str, entries: List[RawCommissionEntry]
    ) -> Optional[str]:
        """Detect product category from product key and entries."""
        key_lower = product_key.lower()

        for pattern, category in PRODUCT_CATEGORY_MAPPINGS.items():
            if pattern in key_lower:
                return category

        # Check entries
        for entry in entries:
            if entry.product_type:
                type_lower = entry.product_type.lower()
                for pattern, category in PRODUCT_CATEGORY_MAPPINGS.items():
                    if pattern in type_lower:
                        return category

        return None

    def _detect_product_subproduct(
        self, product_key: str, entries: List[RawCommissionEntry]
    ) -> Optional[str]:
        """Detect product-subproduct from product key."""
        key_lower = product_key.lower()
        # Longer/more-specific patterns first
        mappings = [
            ("two wheeler", "two-wheeler"),
            ("2w", "two-wheeler"),
            ("goods carrying", "commercial-gcv"),
            ("gcv", "commercial-gcv"),
            ("gccv", "commercial-gcv"),
            ("passenger carrying", "commercial-pcv"),
            ("pcv", "commercial-pcv"),
            ("pccv", "commercial-pcv"),
            ("staff bus", "commercial-buses"),
            ("school bus", "commercial-buses"),
            ("passenger bus", "commercial-buses"),
            ("mini bus", "commercial-buses"),
            ("bus", "commercial-buses"),
            ("misc d", "commercial-miscd"),
            ("miscd", "commercial-miscd"),
            ("msv", "commercial-miscd"),
            ("tractor", "commercial-miscd"),
            ("tipper", "commercial-miscd"),
            ("truck", "commercial-miscd"),
            ("car", "car"),
        ]
        for pattern, value in mappings:
            if pattern in key_lower:
                return value
        # Fallback 1: probe each entry's product_type and source_section
        # (LLM Pass 3 normalises product_name to the canonical label and
        # strips the original slug — but the slug usually survives on
        # product_type ("GCV") or source_section ("1_GCCV_3W")).
        for entry in entries:
            for source in (
                getattr(entry, "product_type", "") or "",
                getattr(entry, "source_section", "") or "",
            ):
                if not source:
                    continue
                source_lower = source.lower()
                for pattern, value in mappings:
                    if pattern in source_lower:
                        return value
        # Fallback 2: canonical product names (TW / Car derivable; CV needs
        # subproduct context which the patterns above already exhausted).
        for entry in entries:
            if entry.product_name == "Two Wheeler":
                return "two-wheeler"
            if entry.product_name == "Private Car":
                return "car"
        return None

    # Canonical PROD tokens the overlay writes onto product_type, plus tolerant
    # aliases, mapped to the canonical SubProduct segment label.
    _SEGMENT_CANON = {
        "gcv": "GCV",
        "gccv": "GCV",
        "pcv": "PCV",
        "pccv": "PCV",
        "pccv<6": "PCV",
        "pccv < 6": "PCV",
        "buses": "Buses",
        "bus": "Buses",
        "pccv>6": "Buses",
        "pccv > 6": "Buses",
        "misc-d": "MISC-D",
        "miscd": "MISC-D",
        "misc d": "MISC-D",
        "msv": "MISC-D",
    }

    # Body-slug backstops when only the sub-class survived (no PROD token).
    _SEGMENT_BODY_BUSES = (
        "school",
        "staff bus",
        "staff-bus",
        "passenger bus",
        "mini bus",
        "greater than six",
        ">6 seat",
    )
    # Taxi-bodied passenger CV rows that sit under the ``PCCV > 6`` section
    # (BIG_TAXIS, MAXI_CAB). They are PCV (taxi), NOT Buses — but the section
    # token ``pccv>6`` maps to Buses, so this body check MUST run before
    # ``_prod_token_segment`` in ``_cv_segment_label`` or the taxi identity is
    # lost. (School/staff bus rows under the same section still route to Buses.)
    _SEGMENT_BODY_PCV_TAXI = (
        "big_tax",
        "big tax",
        "big taxi",
        "maxi_cab",
        "maxi cab",
        "maxicab",
    )
    _SEGMENT_BODY_MISCD = (
        "tractor",
        "trac",
        "excavator",
        "harvest",
        "tipper",
        "dumper",
        "crane",
        "loader",
        "grader",
        "mixer",
        "dozer",
        "compactor",
        "rig",
        "compressor",
        "road roller",
        "road sweeper",
        "fork lift",
        "forklift",
    )
    _SEGMENT_BODY_PCV = (
        "maxicab",
        "maxi",
        "3w_auto",
        "3w auto",
        "auto rickshaw",
        "auto-rickshaw",
    )

    @staticmethod
    def _prod_token_segment(text: str) -> Optional[str]:
        """Map a PROD / table-name token to a canonical segment label.

        Order matters: the >6 / <6 passenger variants must be tested before the
        bare ``pccv`` / ``pcv`` token, and ``gccv`` before the ``gcv`` substring.
        """
        if "msv" in text or "misc" in text:
            return "MISC-D"
        if "pccv>6" in text or "pccv > 6" in text:
            return "Buses"
        if "pccv<6" in text or "pccv < 6" in text:
            return "PCV"
        if "gccv" in text or "gcv" in text:
            return "GCV"
        if "pccv" in text or "pcv" in text:
            return "PCV"
        return None

    def _cv_segment_label(self, entry: RawCommissionEntry) -> Optional[str]:
        """Return the commercial-vehicle SubProduct segment for an entry.

        One of ``GCV`` / ``PCV`` / ``Buses`` / ``MISC-D`` for commercial
        entries, else ``None`` (Car / Two Wheeler are never segmented).

        The authoritative signal is ``source_section`` — the codegen tags each
        rule with its source table name ("Table 1: GCCV Commission",
        "Table 3: PCCV > 6 Commission", "Table 4: MSV Commission"), which names
        the segment directly. Body sub-classes and an explicit canonical
        ``product_type`` are used only as backstops.
        """
        section = (getattr(entry, "source_section", "") or "").lower()
        blob = " ".join(
            str(v)
            for v in (
                getattr(entry, "product_type", "") or "",
                getattr(entry, "product_name", "") or "",
                getattr(entry, "source_section", "") or "",
                getattr(entry, "rule_text", "") or "",
                (getattr(entry, "conditions", None) or {}).get("vehicle_sub_class"),
            )
            if v
        ).lower()

        if not self._looks_commercial(entry, blob):
            return None

        # 0) Taxi-bodied rows (BIG_TAXIS / MAXI_CAB) live under the "PCCV > 6"
        # section whose PROD token maps to Buses. Detect the taxi body FIRST so
        # the section token cannot steal their PCV (taxi) identity. School/staff
        # bus rows in the same section have no taxi body and fall through to the
        # PROD-token step below (→ Buses).
        if any(b in blob for b in self._SEGMENT_BODY_PCV_TAXI):
            return "PCV"

        # 1) Authoritative table / PROD token (source_section names the segment).
        for text in (section, blob):
            seg = self._prod_token_segment(text)
            if seg:
                return seg
        # 2) Body sub-class backstop (when no PROD token survived).
        if any(b in blob for b in self._SEGMENT_BODY_BUSES):
            return "Buses"
        if any(b in blob for b in self._SEGMENT_BODY_MISCD):
            return "MISC-D"
        if any(b in blob for b in self._SEGMENT_BODY_PCV):
            return "PCV"
        # 3) Explicit canonical product_type (overlay may set it).
        pt = (getattr(entry, "product_type", "") or "").strip().lower()
        if pt in self._SEGMENT_CANON:
            return self._SEGMENT_CANON[pt]
        # 4) Commercial but unclassified → goods carrier (the GCCV main table).
        return "GCV"

    def _car_segment_label(self, entry: RawCommissionEntry) -> Optional[str]:
        """Return the Car SubProduct segment for an entry.

        ``Taxi`` when the row is a taxi (incl. kaali-peeli), ``Private Car`` for
        any other car row, else ``None`` (non-car). Splitting here gives the Car
        product two distinct SubProducts; a document with no taxi grid simply
        yields no Taxi group.
        """
        blob = " ".join(
            str(v)
            for v in (
                getattr(entry, "product_type", "") or "",
                getattr(entry, "product_name", "") or "",
                getattr(entry, "source_section", "") or "",
                getattr(entry, "rule_text", "") or "",
                (getattr(entry, "conditions", None) or {}).get("vehicle_sub_class"),
            )
            if v
        ).lower()
        if "taxi" in blob or "kaali" in blob:
            return "Taxi"
        sub = self._detect_product_subproduct(self._get_product_key(entry), [entry])
        if sub == "car":
            return "Private Car"
        if (getattr(entry, "product_name", "") or "").strip().lower() in (
            "private car",
            "pvt car",
        ):
            return "Private Car"
        return None

    def _looks_commercial(self, entry: RawCommissionEntry, blob: str) -> bool:
        """Whether an entry belongs to the Commercial Vehicle product."""
        if "commercial" in blob or any(
            t in blob for t in ("gccv", "pccv", "gcv", "pcv", "msv", "misc")
        ):
            return True
        sub = self._detect_product_subproduct(self._get_product_key(entry), [entry])
        return bool(sub and sub.startswith("commercial"))

    def _detect_vehicle_type(
        self, product_key: str, entries: List[RawCommissionEntry]
    ) -> Optional[str]:
        """Detect vehicle type from product key."""
        key_lower = product_key.lower()
        mappings = {
            "car": "car-taxi",
            "taxi": "car-taxi",
            "two wheeler": "two-wheeler",
            "2w": "two-wheeler",
            "bike": "two-wheeler",
            "scooter": "two-wheeler",
            "gcv": "gcv",
            "goods carrying": "gcv",
            "pcv": "pcv",
            "passenger carrying": "pcv",
            "bus": "buses",
            "misc": "misc-d",
        }
        for pattern, value in mappings.items():
            if pattern in key_lower:
                return value
        return None

    def _transform_rule(
        self,
        entry: RawCommissionEntry,
        product_code: str,
        index: int,
        priority: int,
        master_data: Optional[List[RawMasterDataEntry]],
        raw: RawExtraction,
    ) -> CommissionRule:
        """Transform a raw entry into a CommissionRule."""
        rule_id = self._generate_rule_id(product_code, entry, index)

        # Transform conditions
        conditions = self._transform_conditions(entry, master_data, raw)

        # Transform outcome
        outcome = self._transform_outcome(entry, raw)

        # Parse rule-specific validity dates (derived from global period only)
        valid_from = None
        valid_to = None

        return CommissionRule(
            rule_id=rule_id,
            priority=priority,
            conditions=conditions if self._has_conditions(conditions) else None,
            outcome=outcome,
            valid_from=valid_from,
            valid_to=valid_to,
            rule_description=entry.rule_text[:200] if entry.rule_text else None,
        )

    def _generate_rule_id(
        self, product_code: str, entry: RawCommissionEntry, index: int
    ) -> str:
        """Generate rule_id from product + conditions + index."""
        parts = [product_code]

        # Add condition identifiers
        if entry.customer_type_text:
            parts.append(self._slugify(entry.customer_type_text)[:3].upper())
        if entry.channel_text:
            parts.append(self._slugify(entry.channel_text)[:3].upper())
        if entry.coverage_type_text:
            parts.append(self._slugify(entry.coverage_type_text)[:3].upper())

        # Ensure uniqueness with index
        parts.append(f"R{index + 1}")

        return "_".join(parts)

    def _has_conditions(self, conditions: RuleConditions) -> bool:
        """Check if conditions object has any non-null values."""
        return any(
            [
                conditions.vehicle_sub_class,
                conditions.policy_type,
                conditions.state,
                conditions.rto,
                conditions.number_of_wheels,
                conditions.gvw_start_range is not None,
                conditions.gvw_end_range is not None,
                conditions.cc_start_range is not None,
                conditions.cc_end_range is not None,
                conditions.seating_capacity_start_range is not None,
                conditions.seating_capacity_end_range is not None,
                conditions.school_bus_type,
                conditions.booking_mode,
                conditions.zero_dep is not None,
                conditions.ncb is not None,
                conditions.ncb_start_range is not None,
                conditions.ncb_end_range is not None,
                conditions.cpa is not None,
                conditions.fuel_type,
                conditions.vehicle_age is not None,
                conditions.vehicle_make,
                conditions.vehicle_model,
                conditions.vehicle_carrier,
                conditions.business_type,
                conditions.od_term is not None,
                conditions.tp_term is not None,
                conditions.deal_parameter,
            ]
        )

    # ==================== CONDITIONS ====================

    def _transform_conditions(
        self,
        entry: RawCommissionEntry,
        master_data: Optional[List[RawMasterDataEntry]],
        raw: RawExtraction,
    ) -> RuleConditions:
        """Transform entry conditions to RuleConditions."""
        # Normalize top-level text fields
        booking_mode = self._normalize_list_field(
            entry.channel_text, BOOKING_MODE_MAPPINGS, "booking_mode"
        )
        business_type = self._normalize_list_field(
            entry.customer_type_text, BUSINESS_TYPE_MAPPINGS, "business_type"
        )
        coverage_text = self._recover_composite_coverage(entry)
        policy_type = self._normalize_list_field(
            coverage_text, POLICY_TYPE_MAPPINGS, "policy_type"
        )

        # Normalize vehicle sub-class from global_vehicle_type_text
        vehicle_sub_class = self._normalize_list_field(
            getattr(raw, "global_vehicle_type_text", None),
            VEHICLE_SUB_CLASS_MAPPINGS,
            "vehicle_sub_class",
        )

        # Extract domain-specific fields from entry.conditions dict. Read before
        # the state block below, which needs it for the conditions fallback.
        entry_conditions = getattr(entry, "conditions", None) or {}

        # Normalize states
        location_text = entry.state_text or getattr(raw, "global_state_text", None)
        if not location_text:
            # Fallback: recover a state the codegen filed under the conditions
            # dict instead of the top-level state_text field. `conditions` is a
            # free-form Dict[str, str], so such a key survives on the entry and
            # was simply never read -- the blank State (and, downstream, blank
            # RTO) column on Chola's Two-Wheeler and commercial rows. That grid
            # carries its state in a per-row leading cell ("State (RTO) > AP")
            # rather than a column header, which is exactly where a generated
            # parser routes it into the row's base conditions.
            #
            # Mirrors the RTO alias list further down: accept the spellings a
            # generated parser plausibly picks, not just one.
            for key in ("state", "states", "state_group", "region"):
                candidate = entry_conditions.get(key)
                # Must be a string: `conditions` is codegen output, and a
                # plural alias ("states") is exactly the shape a generated
                # parser emits as a list. _expand_state_names calls .lower()
                # on this, so a non-string would crash the whole transform.
                if isinstance(candidate, (list, tuple)):
                    candidate = ", ".join(str(v) for v in candidate if v)
                elif candidate is not None and not isinstance(candidate, str):
                    candidate = str(candidate)
                if candidate:
                    location_text = candidate
                    # Warn ONCE per alias key, not once per rule: this fires on
                    # every row of the grid it targets, and warnings are
                    # persisted into the document's validation_notes.
                    warning = (
                        f"State recovered from conditions['{key}']: the "
                        f"extraction should emit it as the top-level "
                        f"state_text field"
                    )
                    if warning not in self.warnings:
                        self.warnings.append(warning)
                    break
        state = self._expand_state_names(location_text, master_data)

        # Extract/expand RTO codes from location_text. `state` is threaded in so
        # a metro label the carrier's zone master does not cover ("KA
        # (Bangalore)") can still resolve against the global RTO master, scoped
        # to the state this very text just produced.
        rto = self._expand_rto_codes(location_text, master_data, state_slugs=state)

        # Built once and threaded through the probe-based recoveries below.
        # _segment_probe re-joins and re-lowercases four fields on every call,
        # and this function consults it more than once per entry.
        probe = self._segment_probe(entry_conditions, entry)

        # Fuel type — expand grid-group tokens to their component fuels.
        fuel_type = self._normalize_list_field(
            entry_conditions.get("fuel_type"), FUEL_TYPE_MAPPINGS, "fuel_type"
        )
        fuel_type = self._expand_fuel_group(fuel_type, entry.product_name)

        # Electric-variant suffix recovery (Chola commercial-vehicle positional
        # rows). The source marks the electric sibling of a weight/wheel band
        # with a trailing "[Electric]" token on the SUBCLASS label (e.g.
        # "2_UPTO_3.5T[Electric]", "1_GCCV_3W[Electric]"). On the codegen path
        # this survives only in the raw sub-class slug / rule text and is never
        # mapped to a fuel, so the electric and non-electric rows become
        # indistinguishable and cannot form separate deals. Tag fuel_type when
        # the marker is present and no fuel was parsed; the non-suffixed sibling
        # stays fuel-agnostic (applies to all fuels).
        if not fuel_type:
            elec_probe = " ".join(
                str(v)
                for v in (
                    entry_conditions.get("vehicle_sub_class"),
                    getattr(entry, "rule_text", ""),
                    getattr(entry, "source_section", ""),
                )
                if v
            ).lower()
            if "[electric]" in elec_probe:
                fuel_type = ["electric"]

        # Renewal-variant suffix recovery (Chola MSV "1_TRAC[RENEWAL]" row).
        # Like the [Electric] probe, the codegen path preserves the "[RENEWAL]"
        # marker only in the sub-class slug / rule text / section, never mapped
        # to a business type. Tag business_type when the marker is present and
        # none was parsed from customer_type_text. Unlike the electric probe,
        # this also accepts a bare "renewal" word (the marker survives without
        # brackets in some section labels).
        if not business_type:
            if "renewal" in probe:
                business_type = ["renewal"]

        # Number of wheels
        number_of_wheels = self._normalize_list_field(
            entry_conditions.get("number_of_wheels"),
            NUMBER_OF_WHEELS_MAPPINGS,
            "number_of_wheels",
        )

        # Vehicle carrier
        vehicle_carrier = self._normalize_list_field(
            entry_conditions.get("vehicle_carrier"),
            VEHICLE_CARRIER_MAPPINGS,
            "vehicle_carrier",
        )

        # School bus type
        school_bus_type = self._normalize_list_field(
            entry_conditions.get("school_bus_type"),
            SCHOOL_BUS_TYPE_MAPPINGS,
            "school_bus_type",
        )

        # Deal parameter — fall back to applied_on_text or global_applied_on
        # so every rule has one of: od, net, regular-tp, tp-with-vehicle-age, flat-amount, ape
        deal_parameter_text = (
            entry_conditions.get("deal_parameter")
            or entry.applied_on_text
            or getattr(raw, "global_applied_on", None)
        )
        deal_parameter = self._normalize_list_field(
            deal_parameter_text,
            DEAL_PARAMETER_MAPPINGS,
            "deal_parameter",
        )

        # Vehicle sub-class from conditions dict (may override global).
        # Codegen writes the raw CV segment slug here ("1_GCCV_3W",
        # "1_TRAC[RENEWAL]"), so strip the slug scaffolding first and skip the
        # slugs that are really a weight/CC dimension rather than a body type.
        cond_sub_class_raw = entry_conditions.get("vehicle_sub_class")
        if not self._subclass_slug_is_dimension(cond_sub_class_raw):
            cond_vehicle_sub_class = self._apply_subproduct_subclass_override(
                self._normalize_list_field(
                    self._normalize_subclass_slug(cond_sub_class_raw),
                    VEHICLE_SUB_CLASS_MAPPINGS,
                    "vehicle_sub_class",
                )
            )
            if cond_vehicle_sub_class:
                vehicle_sub_class = cond_vehicle_sub_class

        # NCB percentage band (+ the derived legacy boolean).
        #
        # Chola PC ships a base grid and a "… - NCB GT 25%" grid at higher rates;
        # left alone both land with no NCB dimension and collide on an identical
        # condition key. Parse the band from the raw value first, then from the
        # marker in source_section / rule_text (which is where it survives after
        # codegen). _pair_ncb_tiers then gives the base twin the complement.
        #
        # Absent NCB stays (None, None) — deliberately NOT the open (0, 100) used
        # by gvw/cc/seating. _is_range_constrained and _has_conditions both key
        # off null-ness, so an open sentinel would make ncb a constrained
        # dimension on every rule of every document and shift the overlap
        # checker's complete-overlap verdict globally.
        raw_ncb = entry_conditions.get("ncb")
        ncb = self._parse_bool_field(raw_ncb)
        ncb_start = ncb_end = None
        parsed = self._parse_ncb_range(raw_ncb) or self._parse_ncb_range(probe)
        if parsed is not None:
            ncb_start, ncb_end = parsed
            self._ncb_bands_parsed.append((ncb_start, ncb_end))
        elif ncb is True:
            # Legacy raw "Yes" with no numeric label — reconstruct the historic
            # split so pre-band behaviour is preserved bit-for-bit.
            ncb_start = self._NCB_LEGACY_THRESHOLD + 1
            ncb_end = self._NCB_MAX
        elif ncb is False:
            ncb_start = self._NCB_MIN
            ncb_end = self._NCB_LEGACY_THRESHOLD
        ncb = self._ncb_bool_from_range(ncb_start, ncb_end, fallback=ncb)

        # CPA
        cpa = self._normalize_single_field(
            entry_conditions.get("cpa"),
            {
                "with cpa": "with-cpa",
                "with-cpa": "with-cpa",
                "without cpa": "without-cpa",
                "without-cpa": "without-cpa",
                "yes": "with-cpa",
                "no": "without-cpa",
            },
            "cpa",
        )

        # Zero dep
        zero_dep = self._normalize_single_field(
            entry_conditions.get("zero_dep"),
            {"yes": "yes", "no": "no", "true": "yes", "false": "no"},
            "zero_dep",
        )

        # Vehicle make/model (free-text arrays)
        vehicle_make = self._parse_text_list(entry_conditions.get("vehicle_make"))
        vehicle_model = self._parse_text_list(entry_conditions.get("vehicle_model"))
        # Chola CV body-row guard: the GCCV / MSV grid repeats the SUBCLASS body
        # enumeration verbatim in BOTH the MAKE and MODEL columns — e.g. MSV
        # "2_EXCAVATOR" publishes make == model == "Loader, Excavators/Earth
        # Movers, Crane, Bulldozers/Bullgraders, Road Rollers, Fork Lift Trucks",
        # and the heavier weight bands repeat the slug in both cells. Those
        # tokens are vehicle sub-classes, not a real make/model split (the
        # enumeration is already captured in vehicle_sub_class), and codegen
        # faithfully copies them into make+model via the generic CV column rule
        # (row[4]->make, row[5]->model). A rule whose make list is identical to
        # its model list is never a genuine make x model grading — real makes
        # and models are disjoint vocab — so drop both. Chola-gated to match the
        # carrier-specific bifurcation guards below (_bifurcate_make_buckets).
        # Exempt the "All Other Make/Models" sentinel (make == model by design):
        # _bifurcate_make_buckets needs its make/model list intact to fan it out.
        if (
            self._carrier_code == "Chola"
            and vehicle_make
            and vehicle_make == vehicle_model
            and not any(
                self._SENTINEL_MAKE_MODEL_RE.search(str(m)) for m in vehicle_make
            )
        ):
            # The enumeration IS a vehicle sub-class list (Loader / Excavators /
            # Earth Movers / Crane / Bulldozers / Road Rollers / Fork Lift
            # Trucks), not a genuine make x model grading. Relocate it into
            # vehicle_sub_class — mapping each body to its canonical subclass via
            # VEHICLE_SUB_CLASS_MAPPINGS — so the enumeration survives instead of
            # being dropped; _bifurcate_subclass_buckets then fans it out into
            # one distinct deal per subclass. This REPLACES the coarse section
            # bucket ("2_EXCAVATOR" -> ["excavators"]) with the fine list. Falls
            # back to the historical null-both behaviour when no body token maps
            # (a genuine but unrecognised enumeration — never silently kept as a
            # bogus make/model split).
            relocated = self._normalize_list_field(
                ", ".join(str(m) for m in vehicle_make),
                VEHICLE_SUB_CLASS_MAPPINGS,
                "vehicle_sub_class",
            )
            if relocated:
                vehicle_sub_class = relocated
            vehicle_make = None
            vehicle_model = None
        # Make/model EXCLUSION lists (rule matches when policy make/model is NOT
        # in the list). Populated by the overlay for explicit "except/excluding"
        # rows and synthesized by _bifurcate_make_buckets for the sentinel
        # "All Other Make/Models" bucket.
        vehicle_make_exclude = self._parse_text_list(
            entry_conditions.get("vehicle_make_exclude")
        )
        vehicle_model_exclude = self._parse_text_list(
            entry_conditions.get("vehicle_model_exclude")
        )

        # Split composite make cells ("TATA/ Maruti/ Mahindra", "Hero vs Bajaj")
        # into their constituent makes. Deliberately runs AFTER the make==model
        # body-row guard above, which compares the two lists as parsed — splitting
        # only the make side first would break that equality and let sub-class
        # enumerations survive as bogus makes. Models are left alone: a slash in a
        # model cell is usually part of the variant name.
        vehicle_make = self._split_composite_makes(vehicle_make)
        vehicle_make_exclude = self._split_composite_makes(vehicle_make_exclude)

        # Fold each make/model onto the master's spelling. Runs AFTER the
        # composite split — a cell holding three makes has to become three
        # entries before any of them can be looked up. Include and exclude
        # lists get identical treatment: an exclusion names real vehicles too,
        # and leaving one side un-canonicalized would mean the same
        # manufacturer is spelled two ways within one document.
        vehicle_make, vehicle_model = self._canonicalize_make_model(
            vehicle_make, vehicle_model
        )
        vehicle_make_exclude, vehicle_model_exclude = self._canonicalize_make_model(
            vehicle_make_exclude, vehicle_model_exclude
        )

        # RTO from conditions dict — expand categories via master data.
        # The LLM may use "rto", "rto_category", "rto_zone", or similar — accept any.
        cond_rto_text = None
        for k in ("rto", "rto_category", "rto_zone", "rto_categories", "rto_cluster"):
            v = entry_conditions.get(k)
            if v:
                cond_rto_text = v if cond_rto_text is None else f"{cond_rto_text}, {v}"
        if cond_rto_text:
            expanded = self._expand_rto_codes(cond_rto_text, master_data)
            if expanded:
                rto = sorted(set((rto or []) + expanded))
            else:
                raw_items = self._parse_text_list(cond_rto_text)
                if raw_items:
                    rto = sorted(set((rto or []) + raw_items))

        # Integer range fields
        gvw_start, gvw_end = self._parse_range_field(entry_conditions.get("gvw"))
        if gvw_start is None and gvw_end is None:
            gvw_start, gvw_end = 0, 0
        elif gvw_start is None:
            gvw_start = 0
        elif gvw_end is None:
            # Open-ended upper bound ("ABOVE 40T") — use the canonical
            # gvwRange ceiling from PRODUCT_FIELD_VALUES.md (999999 kg).
            gvw_end = 999999

        # GVW recovery: when the parse above hit the (0, 0) "no GVW filter"
        # sentinel, the weight band may still be baked into a sub-class slug
        # ("2_UPTO_3.5T"). Codegen stores grid dims under conditions["subclass"]
        # — a key the transformer never reads — so the band is dropped at the
        # key level on the codegen path. Recover it deterministically here.
        if gvw_start == 0 and gvw_end == 0:
            recovered = self._recover_gvw_range(entry_conditions, raw)
            if recovered is not None:
                gvw_start, gvw_end = recovered

        # GVW-3W open-ceiling backstop (Chola goods 3-wheeler). GVW is the only
        # range field whose "absent" default is the degenerate (0, 0) instead of
        # an open (0, ceiling) — cf. CC (0, 99999), seating (0, 99), NOP
        # (0, 999999). A goods 3-wheeler (auto) is all-GVW, never weight-graded,
        # so the overlay correctly sets no gvw and the row lands on (0, 0) — but
        # (0, 0) exports as a 0-0 band, not an open ceiling. Widen it to the
        # canonical gvwRange ceiling (999999). Mirrors the EV-TW CC reset below;
        # guarded tightly on the 3W signal so a real 4W weight grade that
        # legitimately parsed to (0, 0) is never widened.
        if (gvw_start, gvw_end) == (0, 0) and self._is_all_gvw_three_wheeler(
            number_of_wheels,
            " ".join(
                str(v)
                for v in (
                    entry_conditions.get("vehicle_sub_class"),
                    getattr(entry, "source_section", ""),
                    getattr(entry, "rule_text", ""),
                )
                if v
            ),
        ):
            gvw_start, gvw_end = 0, 999999

        cc_start, cc_end = self._parse_range_field(
            self._cc_or_leaked_rate(entry_conditions.get("cc"))
        )
        if cc_start is None and cc_end is None:
            cc_start, cc_end = 0, 99999
        elif cc_start is None:
            cc_start = 0
        elif cc_end is None:
            cc_end = 99999

        # CC recovery: Chola PCCV<6 4W taxi rows bake the CC band into the
        # sub-class slug ("2_4W_LT_1500CC", "3_4W_GT_1500CC"). On the codegen
        # path the band survives only in the slug, so conditions["cc"] is empty
        # and the parse above lands on the (0, 99999) sentinel. Recover it.
        if (cc_start, cc_end) == (0, 99999):
            recovered_cc = self._recover_cc_range(entry_conditions, entry)
            if recovered_cc is not None:
                cc_start, cc_end = recovered_cc

        # Electric two-wheelers are kW-rated, not CC-rated, so they never carry
        # a CC filter. Deterministic backstop: if a kW power band ("7 KW",
        # "> 16 KW") leaked into conditions["cc"] on the codegen path it would
        # yield a bogus tight range (e.g. cc=(7,7)) that collapses every EV
        # band into one. Reset electric two-wheeler rules to the open sentinel.
        if (
            entry.product_name == "Two Wheeler"
            and fuel_type
            and "electric" in fuel_type
            and (cc_start, cc_end) != (0, 99999)
        ):
            cc_start, cc_end = 0, 99999

        # Mis-split guard (Chola flattened Two-Wheeler grid). The codegen splits
        # each "<make> > <band> > <value>" header cell positionally; when the
        # make is not repeated for a sub-column the band lands in vehicle_make
        # (e.g. "150_350cc") and a rate lands in cc (e.g. "0.205"). Detect a
        # vehicle_make item that is really a CC band, move it back to cc, and
        # drop the bogus cc value.
        if vehicle_make:
            real_makes: List[str] = []
            band_from_make: Optional[str] = None
            body_from_make: List[str] = []
            for m in vehicle_make:
                ml = str(m).strip().lower()
                # A CC band is fully numeric with an optional range + "cc"
                # suffix ("150", "150_350", "150cc", "150_350cc"). Match the
                # WHOLE token so real makes that merely contain "cc"
                # (e.g. "CCM" motorcycles) are never dropped.
                if re.fullmatch(r"\d+(?:[_\-]\d+)?(?:cc)?", ml):
                    band_from_make = band_from_make or ml
                # A two-wheeler BODY token (scooter/bike/moped) landed in
                # vehicle_make from a MAKE-LESS flattened column header
                # ("SCOOTER > 0.37" with no preceding make, so the codegen's
                # carry-forward had no make to inherit). It is a sub-class, not a
                # make — route it to vehicle_sub_class. Scoped to genuine body
                # words: NOT the whole VEHICLE_SUB_CLASS_MAPPINGS, which also
                # holds make-bucket sentinels ("others") and non-two-wheeler
                # bodies that must stay in vehicle_make.
                elif ml in _MISSPLIT_BODY_TOKENS:
                    body_from_make.append(VEHICLE_SUB_CLASS_MAPPINGS.get(ml, ml))
                else:
                    real_makes.append(m)
            if band_from_make is not None or body_from_make:
                vehicle_make = real_makes or None
            if band_from_make is not None:
                band_norm = band_from_make.replace("cc", "").replace("_", "-")
                bs, be = self._parse_range_field(band_norm)
                cc_start = bs if bs is not None else 0
                cc_end = be if be is not None else 99999
            if body_from_make:
                merged = list(vehicle_sub_class or [])
                for b in body_from_make:
                    if b not in merged:
                        merged.append(b)
                vehicle_sub_class = merged or None
                # The same mis-split parked the header value in cc (e.g. "0.37"
                # or "-0.05", which parse_cc_range collapsed to 0-0). When no
                # real CC band came from this row, treat cc as unconstrained
                # (0-99999, all-CC) — matching the make-qualified scooter rows —
                # instead of a bogus 0-0 band.
                if band_from_make is None:
                    cc_start, cc_end = 0, 99999

        seat_start, seat_end = self._parse_range_field(
            entry_conditions.get("seating_capacity")
            or getattr(raw, "global_seating_capacity_text", None)
        )
        if seat_start is None and seat_end is None:
            seat_start, seat_end = 0, 99
        elif seat_start is None:
            seat_start = 0
        elif seat_end is None:
            seat_end = 99

        # Seating recovery: Chola PCCV rows encode the seating band only in the
        # section/PROD token ("PCCV < 6" / "PCCV > 6"), never as a per-row
        # seating column, so the parse above lands on the (0, 99) "no seating
        # filter" sentinel. Recover the grid-verbatim band deterministically
        # (G3a: <6 → (0,5), >6 → (6,99); 6-seater belongs to the upper band).
        if (seat_start, seat_end) == (0, 99):
            recovered_seat = self._recover_seating_range(entry_conditions, entry)
            if recovered_seat is not None:
                seat_start, seat_end = recovered_seat

        # Policies count (NOP) — number of policies sold tier. Common Chola TW
        # qualifier on row labels: "NEW(UPTO 30 NOP)", "NEW(30-100 NOP)",
        # "NEW(100-500 NOP)". When absent the rule applies to any count, so
        # default to (0, 999999) — the canonical open range used elsewhere.
        nop_start, nop_end = self._parse_range_field(
            entry_conditions.get("policies_count")
        )
        if nop_start is None and nop_end is None:
            nop_start, nop_end = 0, 999999
        elif nop_start is None:
            nop_start = 0
        elif nop_end is None:
            nop_end = 999999

        # Vehicle age
        vehicle_age = self._parse_int_field(entry_conditions.get("vehicle_age"))

        # OD term / TP term
        od_term = self._parse_int_field(entry_conditions.get("od_term"))
        tp_term = self._parse_int_field(entry_conditions.get("tp_term"))

        # Chola PCV body-class rules (client-supplied). Runs last: it needs the
        # resolved fuel_type, seating band and wheel count together.
        vehicle_sub_class, seat_start, seat_end = self._apply_chola_pcv_subclass(
            vehicle_sub_class,
            cond_sub_class_raw,
            fuel_type,
            number_of_wheels,
            seat_start,
            seat_end,
        )
        vehicle_sub_class = self._apply_chola_tw_subclass(
            vehicle_sub_class, entry, cc_start, cc_end
        )

        return RuleConditions(
            vehicle_sub_class=vehicle_sub_class,
            policy_type=policy_type,
            state=state,
            rto=rto,
            number_of_wheels=number_of_wheels,
            gvw_start_range=gvw_start,
            gvw_end_range=gvw_end,
            cc_start_range=cc_start,
            cc_end_range=cc_end,
            seating_capacity_start_range=seat_start,
            seating_capacity_end_range=seat_end,
            policies_count_start_range=nop_start,
            policies_count_end_range=nop_end,
            school_bus_type=school_bus_type,
            booking_mode=booking_mode,
            zero_dep=zero_dep,
            ncb=ncb,
            ncb_start_range=ncb_start,
            ncb_end_range=ncb_end,
            cpa=cpa,
            fuel_type=fuel_type,
            vehicle_age=vehicle_age,
            vehicle_make=vehicle_make,
            vehicle_model=vehicle_model,
            vehicle_make_exclude=vehicle_make_exclude,
            vehicle_model_exclude=vehicle_model_exclude,
            vehicle_carrier=vehicle_carrier,
            business_type=business_type,
            od_term=od_term,
            tp_term=tp_term,
            deal_parameter=deal_parameter,
        )

    # ==================== NORMALIZATION HELPERS ====================

    @staticmethod
    def _is_numeric_text(text) -> bool:
        """True when the whole string parses as a number (a rate, not a label)."""
        if text is None:
            return False
        try:
            float(str(text).strip())
        except (TypeError, ValueError):
            return False
        return True

    def _recover_composite_coverage(self, entry: RawCommissionEntry) -> Optional[str]:
        """Recover the coverage token from a mis-split composite state header.

        Chola grids publish the coverage type as a sub-column of the state
        header: ``"UP- East > ACT"``. The Pass-3 recipe splits that with
        ``rsplit(">", 1)`` — correct for the two-segment Private Car form, but
        the commercial-vehicle grids carry the rate columns in the same cell
        (``"AP/TS > ACT > 0.325"``), so the split lands one segment right:
        ``state_text`` keeps ``"AP/TS > ACT"`` and ``coverage_type_text`` gets
        the rate. `policy_type` then normalises to None and the whole
        comprehensive-vs-liability dimension is lost on the CV products.

        When ``coverage_type_text`` is numeric and ``state_text`` still carries
        a ``>``, peel the trailing numeric segments off ``state_text`` and take
        the last remaining segment as the coverage token. ``_expand_state_names``
        needs no matching fix — it is a substring scan, so the inert
        ``"> ACT"`` tail never stopped AP/TS from resolving.

        Returns ``coverage_type_text`` unchanged whenever it is not a
        recoverable mis-split, so a genuinely unmappable value still warns.
        """
        coverage_text = entry.coverage_type_text
        # Chola-scoped: only its grids use the "STATE > COVERAGE" header form,
        # and a cross-carrier rewrite could mangle a legitimately numeric field.
        if self._carrier_code != "Chola" or not self._is_numeric_text(coverage_text):
            return coverage_text

        state_text = getattr(entry, "state_text", "") or ""
        if ">" not in state_text:
            return coverage_text

        segments = [s.strip() for s in state_text.split(">")]
        # A four-segment header (state > coverage > rate > rate) shifts twice,
        # so peel every trailing rate, not just one.
        while len(segments) > 1 and self._is_numeric_text(segments[-1]):
            segments.pop()
        if len(segments) < 2:
            return coverage_text

        recovered = segments[-1]
        self.notices.append(
            f"Coverage recovered from composite state header: "
            f"{state_text!r} -> {recovered!r} (was {coverage_text!r})"
        )
        return recovered

    def _normalize_list_field(
        self, text: Optional[str], mappings: Dict[str, str], field_name: str
    ) -> Optional[List[str]]:
        """Normalize a free-text value to a list of enum values using mappings."""
        if not text:
            return None

        normalized = []
        text_lower = text.lower().strip()

        # Mask matched spans so shorter overlapping keys cannot re-match inside a
        # longer match. Without this, "Non Petrol" → "non petrol" matches AND
        # "petrol" matches the substring within, producing both "non-petrol" and
        # "petrol" in the output. Use a sentinel char that no key contains.
        masked = list(text_lower)
        sentinel = "\x00"

        # Sort by key length descending so longer keys match first (e.g., "new customer" before "new")
        sorted_keys = sorted(mappings.keys(), key=len, reverse=True)
        for key in sorted_keys:
            value = mappings[key]
            # Use word-boundary matching for short keys to avoid substring false positives
            if len(key) <= 5:
                pattern = rf"(?:^|[\s,;/\-])({re.escape(key)})(?=$|[\s,;/\-])"
                group_index = 1
            else:
                pattern = re.escape(key)
                group_index = 0

            current = "".join(masked)
            for match in re.finditer(pattern, current):
                if value not in normalized:
                    normalized.append(value)
                start, end = match.span(group_index)
                for i in range(start, end):
                    masked[i] = sentinel

        if not normalized:
            self.warnings.append(f"Unknown {field_name}: {text}")
            return None

        return normalized

    def _normalize_single_field(
        self, text: Optional[str], mappings: Dict[str, str], field_name: str
    ) -> Optional[str]:
        """Normalize a free-text value to a single enum value."""
        if not text:
            return None

        text_lower = text.lower().strip()

        for key, value in mappings.items():
            if key in text_lower:
                return value

        self.warnings.append(f"Unknown {field_name}: {text}")
        return None

    def _parse_bool_field(self, text: Optional[str]) -> Optional[bool]:
        """Parse a text value to a boolean.

        Returns None for None/empty/whitespace silently (blank NCB cells are
        the common case and would flood the warnings list). Warns on genuinely
        unrecognised non-empty input so a mis-encoded NCB value surfaces instead
        of collapsing to None."""
        if text is None:
            return None
        if isinstance(text, bool):
            return text

        text_lower = str(text).lower().strip()
        if text_lower in ("yes", "true", "1", "y"):
            return True
        if text_lower in ("no", "false", "0", "n"):
            return False

        if text_lower:
            self.warnings.append(f"Unrecognised boolean value: {text}")
        return None

    def _parse_int_field(self, text: Optional[str]) -> Optional[int]:
        """Parse a text value to an integer."""
        if text is None:
            return None
        if isinstance(text, (int, float)):
            return int(text)

        match = re.search(r"(\d+)", str(text))
        if match:
            return int(match.group(1))
        return None

    def _parse_text_list(self, text: Optional[str]) -> Optional[List[str]]:
        """Parse a comma-separated text or list value into a string list."""
        if not text:
            return None
        if isinstance(text, list):
            return text if text else None

        items = [item.strip() for item in str(text).split(",") if item.strip()]
        return items if items else None

    # Separators that join SEVERAL makes inside one grid cell. Chola's GCCV
    # "2_UPTO_3.5T" MAKE cell is the newline-joined "TATA/\nMaruti/\nMahindra"
    # (the indexer flattens the newlines to spaces), and the TW sheet ships a
    # "Hero vs Bajaj" comparison column. Both arrived as a single opaque make
    # string, so any downstream match on "TATA" or "Bajaj" missed the rule.
    # Slash form tolerates surrounding whitespace; the "vs" form is
    # word-anchored (also "v/s", "vs.") so a make merely containing those
    # letters is untouched.
    _COMPOSITE_MAKE_SPLIT_RE = re.compile(r"\s*/\s*|\s+(?:vs\.?|v/s)\s+", re.IGNORECASE)

    def _split_composite_makes(self, makes: Optional[List[str]]) -> Optional[List[str]]:
        """Split `/`- and `vs`-joined composite make cells into separate makes.

        Order-preserving and duplicate-free. Two guards keep it conservative:

        - A **sentinel** item ("All Other Make/Models") is left verbatim. Its
          slash is part of the phrase, and `_bifurcate_make_buckets` matches the
          intact string to fan the bucket out into exclusion rules.
        - A split is only accepted when EVERY resulting segment still looks like
          a make (non-empty, carries a letter). "TATA/ Maruti/ Mahindra" splits;
          a cell ending in a stray "/" does not degrade into a blank make.
        """
        if not makes:
            return makes

        out: List[str] = []
        seen = set()
        for raw in makes:
            item = str(raw).strip()
            if not item:
                continue
            if self._SENTINEL_MAKE_MODEL_RE.search(item):
                segments = [item]
            else:
                candidates = [
                    s.strip() for s in self._COMPOSITE_MAKE_SPLIT_RE.split(item)
                ]
                # Reject the split unless every segment is a plausible make.
                if len(candidates) > 1 and all(
                    s and re.search(r"[A-Za-z]", s) for s in candidates
                ):
                    segments = candidates
                else:
                    segments = [item]
            for s in segments:
                key = s.casefold()
                if key not in seen:
                    seen.add(key)
                    out.append(s)
        return out or None

    def _canonicalize_make_model(
        self,
        makes: Optional[List[str]],
        models: Optional[List[str]],
    ) -> Tuple[Optional[List[str]], Optional[List[str]]]:
        """Fold a rule's make/model lists onto the seeded master's spelling.

        SPELLING ONLY. Every entry is either replaced by its canonical string
        or left exactly as it was: list lengths never change, entries are
        never added, dropped or reordered. That invariant is what keeps this
        out of the rule-count and coverage machinery — it can shift a dedupe
        group key (see below), but it can never make a rule appear or vanish.

        Inert unless the document is Chola AND the master seeded AND
        ``CINS_CHOLA_MMV`` is on — ``self._mmv_index`` is empty in every other
        case, and an empty index returns the inputs unchanged.

        Sentinels ("All Other Make/Models") pass through verbatim: the bucket
        fan-out in ``_clone_make_bucket`` matches on that literal text.

        Note for the exclude lists: ``vehicle_make_exclude`` /
        ``vehicle_model_exclude`` ARE part of ``_DEDUPE_SCALAR_FIELDS``, so
        canonicalizing them can merge two dedupe groups that previously
        differed only by spelling. That is the intended effect — they name the
        same excluded vehicles — but it is a real behaviour change, which is
        why ``CICS_PIPELINE_VERSION`` moves with it.
        """
        if not self._mmv_index:
            return makes, models

        bucket = product_subproduct_bucket(self._product_subproduct)
        if bucket is None:
            return makes, models

        from master_data.services.mmv import match_make, match_model, scope_make

        canonical_makes = None
        matched_makes = []
        if makes:
            canonical_makes = []
            for item in makes:
                text = str(item)
                if self._SENTINEL_MAKE_MODEL_RE.search(text):
                    canonical_makes.append(item)
                    continue
                hit = match_make(self._mmv_index, bucket, text)
                matched_makes.append(hit)
                # Unmatched text stays verbatim in the OUTPUT, but must not
                # become the model lookup's scope — see scope_make's docstring.
                canonical_makes.append(hit or item)

        if not models:
            return canonical_makes if makes else makes, models

        # Scope model lookups by the rule's make when exactly one RESOLVED —
        # a model name is only unique within its manufacturer. Shared with
        # CholaMMVValidator so the two cannot disagree about what "unknown"
        # means for a model.
        scope = scope_make(matched_makes)
        canonical_models = []
        for item in models:
            text = str(item)
            if self._SENTINEL_MAKE_MODEL_RE.search(text):
                canonical_models.append(item)
                continue
            canonical_models.append(
                match_model(self._mmv_index, bucket, scope, text) or item
            )

        return (canonical_makes if makes else makes), canonical_models

    def _parse_range_field(
        self, text: Optional[str]
    ) -> Tuple[Optional[int], Optional[int]]:
        """Parse a range text into start/end integers.

        Handles decimals and tonnage units. Tonnage suffixes (T, TON, TONNE,
        TONNES, MT) trigger a ×1000 conversion so commercial-vehicle GVW
        labels like "UPTO 3.5T" land on 3500 kg, matching the canonical
        gvwRange buckets in PRODUCT_FIELD_VALUES.md. Underscore separators
        from indexer slugs ("3.5T_TO_7.5T") are normalised to whitespace
        before pattern matching.
        """
        if text is None:
            return None, None
        if isinstance(text, dict):
            return text.get("min"), text.get("max")

        text_str = str(text).strip()
        if not text_str:
            return None, None

        # Normalise indexer slug separators ("3.5T_TO_7.5T" -> "3.5T TO 7.5T").
        normalized = text_str.replace("_", " ")

        # Tonnage detection: a digit immediately followed (optional whitespace)
        # by T / TON / TONNE / TONNES / MT at a word boundary. Word-boundary
        # guard prevents "TO", "TRACTOR", "TOTAL" from triggering.
        tonne_pattern = r"\d\s*(?:TONNES|TONNE|TONS|TON|MT|T)\b"
        multiplier = 1000 if re.search(tonne_pattern, normalized, re.IGNORECASE) else 1

        # Strip tonnage units attached to numbers so the range regexes see
        # plain decimals ("3.5T TO 7.5T" -> "3.5 TO 7.5").
        stripped = re.sub(
            r"(\d+(?:\.\d+)?)\s*(?:TONNES|TONNE|TONS|TON|MT|T)\b",
            r"\1",
            normalized,
            flags=re.IGNORECASE,
        )

        def _to_int(num: str) -> int:
            return int(round(float(num) * multiplier))

        # Pattern: "1-2500", "1 – 2500", "1 to 2500", "3.5 TO 7.5".
        range_match = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)",
            stripped,
            re.IGNORECASE,
        )
        if range_match:
            return _to_int(range_match.group(1)), _to_int(range_match.group(2))

        # Pattern: "> 12", ">= 12", "above 12", "above 20T".
        above_match = re.search(
            r"(?:>=|>|above|greater than|more than)\s*(\d+(?:\.\d+)?)",
            stripped,
            re.IGNORECASE,
        )
        if above_match:
            return _to_int(above_match.group(1)), None

        # Pattern: "< 12", "<= 12", "upto 12", "up to 12", "UPTO 3.5T".
        below_match = re.search(
            r"(?:<=|<|below|less than|upto|up to)\s*(\d+(?:\.\d+)?)",
            stripped,
            re.IGNORECASE,
        )
        if below_match:
            return None, _to_int(below_match.group(1))

        # Single bare number (optionally decimal). Use fullmatch so we don't
        # accidentally swallow a fragment from a larger string.
        single_match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", stripped)
        if single_match:
            val = _to_int(single_match.group(1))
            return val, val

        return None, None

    # Non-range condition fields used to decide which rules share a "group"
    # for boundary de-overlap. Two rules collide on a range only if every
    # other dimension matches — same logic as RuleOverlapChecker.
    # vehicle_make and vehicle_model are deliberately excluded: source docs
    # commonly use them for sentinel values ("All Other Make/Models") and
    # indexer-slug leakage ("2_UPTO_3.5T[Electric]") that split otherwise-
    # identical ladder rules into separate groups, defeating boundary dedupe.
    # Real make-specific commission rules are rare; the validator will still
    # surface any genuine make/model overlap.
    _DEDUPE_LIST_FIELDS = (
        "vehicle_sub_class",
        "policy_type",
        "state",
        "rto",
        "number_of_wheels",
        "school_bus_type",
        "booking_mode",
        "fuel_type",
        "vehicle_carrier",
        "business_type",
        "deal_parameter",
        # Exclusion lists are part of a rule's identity — they distinguish the
        # make/model buckets (a)/(b)/(c) and must NOT be collapse-ignored.
        "vehicle_make_exclude",
        "vehicle_model_exclude",
    )
    _DEDUPE_SCALAR_FIELDS = (
        "zero_dep",
        "cpa",
        "ncb",
        # The NCB band joins the GROUP KEY only — deliberately not
        # _DEDUPE_RANGES, which would let the boundary walk rewrite bands parsed
        # verbatim from the grid. Without them here, a THREE-tier ladder breaks:
        # bands 25-45 and 45+ both derive ncb=True, so the bool alone cannot
        # separate them, they share a group key, and the CC boundary-bump walk
        # fuses two distinct NCB bands' ladders. (A two-tier ladder is saved by
        # the bool being True vs False — which is why it is still here.)
        "ncb_start_range",
        "ncb_end_range",
        "vehicle_age",
        "od_term",
        "tp_term",
    )
    # (start_field, end_field) tuples to de-overlap. The "other" ranges in
    # this list participate in the group key, so different CC buckets don't
    # get conflated when we bump GVW boundaries.
    _DEDUPE_RANGES = (
        ("gvw_start_range", "gvw_end_range"),
        ("cc_start_range", "cc_end_range"),
        ("seating_capacity_start_range", "seating_capacity_end_range"),
        ("policies_count_start_range", "policies_count_end_range"),
    )

    # Open-range ceilings per range type. When a ladder ends with a bare
    # singleton (e.g. CC header "350cc" meaning ">350"), promotion extends
    # its end to this ceiling — matching the defaults used in
    # _build_conditions when a range field is absent entirely.
    _RANGE_CEILINGS = {
        "gvw_end_range": 999999,
        "cc_end_range": 99999,
        "seating_capacity_end_range": 99,
        "policies_count_end_range": 999999,
    }

    # Sentinel (start, end) pairs that mean "no filter on this dimension".
    # _transform_conditions emits these when the source entry omits the
    # field entirely. They must NOT participate in dedupe — bumping their
    # start would shift the boundary of an unrelated real-range rule that
    # shares the same default lower bound (e.g. a (0, 0) gvw sentinel next
    # to a real (0, 3500) gvw rule would push the real rule to (1, 3500)).
    _RANGE_SENTINELS = {
        ("gvw_start_range", "gvw_end_range"): {(0, 0)},
        ("cc_start_range", "cc_end_range"): {(0, 99999)},
        ("seating_capacity_start_range", "seating_capacity_end_range"): {(0, 99)},
        ("policies_count_start_range", "policies_count_end_range"): {(0, 999999)},
    }

    # Canonical GCV wheel categories per PRODUCT_FIELD_VALUES.md.
    def _expand_fuel_group(self, fuel_type, product_name=None):
        """Expand grid-group fuel tokens to their component fuels.

        Default (all carriers): the diesel grid token "non-petrol" expands to
        {diesel, cng, lpg} — the historical behavior.

        Chola (Cholamandalam): CNG/LPG (and, for Private Car only, Electric)
        follow the *Petrol* grid, not the Diesel grid (the carrier's grid-alias
        rule, confirmed by client reconciliation). So for Chola the diesel grid
        is diesel-only and the petrol grid carries the alternative fuels:
            [D] non-petrol -> {diesel}
            [P] petrol     -> {petrol, cng, lpg}          (+ electric for Private Car)

        Electric is folded into the petrol grid ONLY for Private Car, whose grid
        note says "Electric Vehicle or LPG/CNG pay-out grid is same as Petrol
        grid". Commercial Vehicle (and Two Wheeler) publish a SEPARATE electric
        column/row, so adding electric here would overlap those dedicated
        electric deals — do not.

        Idempotent: the "non-petrol" group token is consumed on first pass, and
        re-adding the petrol set is a stable union, so a second pass is a no-op.
        Returns the input unchanged when no group token is present.
        """
        if not fuel_type:
            return fuel_type
        fuels = set(fuel_type)
        if self._carrier_code == "Chola":
            changed = False
            if "non-petrol" in fuels:
                fuels = (fuels - {"non-petrol"}) | {"diesel"}
                changed = True
            if "petrol" in fuels:
                add = {"petrol", "cng", "lpg"}
                if product_name == "Private Car":
                    add |= {"electric"}
                fuels |= add
                changed = True
            return sorted(fuels) if changed else fuel_type
        # Default carriers: preserve historical non-petrol -> {diesel, cng, lpg}.
        if "non-petrol" in fuels:
            return sorted((fuels - {"non-petrol"}) | {"diesel", "cng", "lpg"})
        return fuel_type

    _GCV_WHEEL_VALUES = ("3w", "4w-and-above")
    _GCV_WHEEL_SUFFIX = {"3w": "3W", "4w-and-above": "4WAA"}

    # GVW upper bounds at/above this are treated as "all-GVW" (open-ended), not
    # a real weight grade. Real Chola GCV bands top out at 47000kg; client
    # all-GVW rows use 999999. Anything in between is a genuine weight grade.
    _ALL_GVW_END_THRESHOLD = 90000

    @classmethod
    def _is_weight_graded_band(cls, conditions) -> bool:
        """True when conditions carry a real (bounded) GVW weight grade.

        A goods 3-wheeler (auto) is never weight-graded — only 4-wheel LCVs and
        trucks carry GVW bands. So a wheel-less rule with any genuine band, light
        (``0-3500``) or heavy (``3500-7500`` …), belongs to 4W-and-Above only.
        Cloning such a rule to 3W produced spurious 3W rows the client never
        publishes (client 3W exists only as all-GVW auto). The ``(0,0)`` "no
        band" sentinel and open all-GVW bands are NOT weight grades and still
        fan out to both wheel categories.
        """
        if conditions is None:
            return False
        start = getattr(conditions, "gvw_start_range", None)
        end = getattr(conditions, "gvw_end_range", None)
        try:
            start = int(start) if start is not None else 0
            end = int(end) if end is not None else 0
        except (TypeError, ValueError):
            return False
        if start > 0:
            return True  # lower-bounded heavy band
        # start == 0: a finite positive upper bound is a real light grade
        # (e.g. 0-3500). end == 0 is the sentinel; a very large end is all-GVW.
        return 0 < end < cls._ALL_GVW_END_THRESHOLD

    def _bifurcate_gcv_wheels(
        self, rules: List[CommissionRule]
    ) -> List[CommissionRule]:
        """Expand each GCV rule to one rule per canonical wheel category.

        Bifurcates when number_of_wheels is missing/empty (rule applies to
        both 3W and 4W & Above) or contains more than one value. Rules
        already scoped to a single wheel category pass through unchanged.

        GVW-aware (Fix iii): a wheel-less rule carrying a *real heavy* GVW band
        (``gvw_start_range > 0`` — a lower-bounded band above the light/3W
        range) maps to 4W-and-Above only. A 3-wheeler cannot carry a band that
        starts above zero, so cloning it to 3W produced spurious rows (e.g. 3W
        paired with a 40-tonne band) and (0,0) catch-alls with conflicting
        rates. Bands that start at 0 (all-GVW, light bands like 0-3500, or the
        (0,0) unknown sentinel) still fan out to both categories. An explicit
        multi-value ``number_of_wheels`` is authoritative and always respected.

        Cloned rules inherit priority, outcome, dates, and description from
        the source; rule_id gets a wheel-category suffix to stay unique.
        """
        expanded: List[CommissionRule] = []
        for rule in rules:
            wheels = rule.conditions.number_of_wheels if rule.conditions else None
            if wheels and len(wheels) == 1:
                expanded.append(rule)
                continue

            if wheels:
                # Explicit multi-value list — authoritative, fan to each.
                targets = wheels
            elif self._is_four_wheel_only_subclass(rule.conditions):
                # Wheel-less special-purpose body (tractor, bus, excavator,
                # harvester, maxicab) -> 4W & Above only. These carry no GVW
                # band; cloning them to 3W produced the (0,0) catch-alls with
                # conflicting rates.
                targets = ["4w-and-above"]
            elif self._is_weight_graded_band(rule.conditions):
                # Wheel-less + any real GVW weight grade (light or heavy) ->
                # 4W & Above only. 3-wheelers are never weight-graded.
                targets = ["4w-and-above"]
            else:
                targets = list(self._GCV_WHEEL_VALUES)
            for wheel in targets:
                if rule.conditions is not None:
                    cloned_conditions = rule.conditions.model_copy(deep=True)
                else:
                    cloned_conditions = RuleConditions()
                cloned_conditions.number_of_wheels = [wheel]
                suffix = self._GCV_WHEEL_SUFFIX.get(wheel, wheel.upper())
                expanded.append(
                    CommissionRule(
                        rule_id=f"{rule.rule_id}_{suffix}",
                        priority=rule.priority,
                        conditions=cloned_conditions,
                        outcome=rule.outcome,
                        valid_from=rule.valid_from,
                        valid_to=rule.valid_to,
                        rule_description=rule.rule_description,
                    )
                )
        return expanded

    # Sentinel "All Other Make/Models" markers. Word-boundary anchored so a
    # legit make/model that merely ends in "…brothers" is NOT matched.
    _SENTINEL_MAKE_MODEL_RE = re.compile(r"\ball other\b|\bothers?$", re.IGNORECASE)

    # ==================== NCB PERCENTAGE BAND ====================
    #
    # Chola PC grids ship parallel table sets split on a No Claim Bonus
    # threshold: a base set and a "… - NCB GT 25%" set at higher rates. The
    # marker survives into source_section / rule_text on codegen, so it is
    # recovered from the lower-cased _segment_probe blob.
    #
    # This replaces the old _NCB_GT25_RE, whose 25 was hardcoded: any grid split
    # at another threshold (20/35/45/50%) left BOTH tiers ncb=None, so they
    # collided on an identical condition key and collapsed onto one rate.
    #
    # Parsed into an inclusive (start, end) band, clamped to 0-100:
    #
    #   form                            example                    band
    #   ------------------------------  -------------------------  ----------
    #   prefix GTE  >= ≥ GTE            "NCB GT OR Equal to 25%"   (25, 100)
    #   prefix GT   >  GT above over    "NCB GT 25%"               (26, 100)
    #   prefix LTE  <= ≤ LTE UPTO max   "NCB UPTO 20%"             (0,  20)
    #   prefix LT   <  LT below under   "NCB LESS THAN 20%"        (0,  19)
    #   postfix ABOVE                   "NCB 25% AND ABOVE"        (25, 100)
    #   postfix BELOW                   "NCB 20% OR LESS"          (0,  20)
    #   band                            "NCB 25%-45%"              (25, 45)
    #   bare                            "NCB 20%"                  (20, 20)
    #
    # The GT/GTE distinction is why _parse_range_field is NOT reused: it cannot
    # tell ">" from ">=", and for NCB that one-off decides the band AND the
    # complement handed to the base twin in _pair_ncb_tiers.
    #
    # Two guards, both load-bearing (this parser runs against the probe of EVERY
    # rule of EVERY carrier, not just Chola):
    #   1. The bare arm requires a trailing "%" — "NCB Yes 2024" cannot stamp a
    #      band.
    #   2. The bare arm declines (returns None) when an alphabetic word follows
    #      the percentage, so an unknown future phrasing becomes a VISIBLE MISS
    #      instead of the silently-wrong point band (25, 25). Learned from
    #      _parse_range_field, whose above-regex needs the qualifier BEFORE the
    #      number: "ABOVE 6" parses, "6 AND ABOVE" falls to a sentinel. NCB's
    #      version of that bug would be worse — a point band is well-formed, in
    #      range, and passes every validator while matching almost no policy.
    _NCB_MIN = 0
    _NCB_MAX = 100
    # The historic Chola split. Only used to reconstruct a band for a legacy raw
    # ``"ncb": "Yes"``/``"No"`` that carries no numeric label; never used to
    # decide whether a parsed band is the high tier.
    _NCB_LEGACY_THRESHOLD = 25

    # Anchor: the literal token, then optional separator punctuation.
    _NCB_ANCHOR = r"ncb\b[\s:\-–—]*"
    # % optional once an operator has anchored the match; mandatory for the bare
    # arm (see guard 1 above).
    _NCB_NUM = r"(\d{1,3})\s*%?"
    _NCB_NUM_PCT = r"(\d{1,3})\s*%"

    # Ordered — FIRST MATCH WINS. Digit-first arms (band, postfix) precede the
    # bare arm; GTE precedes GT so "GT OR EQUAL TO 25" is not read as strict GT.
    _NCB_ARMS = [
        # band: "25%-45%", "25 to 45"
        (
            rf"{_NCB_ANCHOR}{_NCB_NUM}\s*(?:-|–|—|to)\s*{_NCB_NUM}",
            lambda a, b: (min(a, b), max(a, b)),
        ),
        # postfix ABOVE: "25% AND ABOVE", "25% & above", "25%+", "25% OR MORE"
        (
            rf"{_NCB_ANCHOR}{_NCB_NUM}\s*(?:\+|(?:and|or|&)?\s*"
            r"(?:above|over|more|plus|upward|upwards|onward|onwards|beyond))",
            lambda a: (a, None),  # None -> _NCB_MAX
        ),
        # postfix BELOW: "20% AND BELOW", "20% OR LESS", "20% AND UNDER"
        (
            rf"{_NCB_ANCHOR}{_NCB_NUM}\s*(?:and|or|&)?\s*"
            r"(?:below|less|under|lower)",
            lambda a: (None, a),  # None -> _NCB_MIN
        ),
        # prefix GTE
        (
            rf"{_NCB_ANCHOR}(?:>=|≥|g\.?t\.?e\.?|(?:gt|>|greater\s*than)"
            rf"\s*or\s*equal\s*to|greater\s*than\s*or\s*equal\s*to|"
            rf"not\s*less\s*than|at\s*least|minimum\s*(?:of)?)\s*{_NCB_NUM}",
            lambda a: (a, None),
        ),
        # prefix GT (strict)
        (
            rf"{_NCB_ANCHOR}(?:>|gt|above|over|greater\s*than|more\s*than|"
            rf"exceeding)\s*{_NCB_NUM}",
            lambda a: (a + 1, None),
        ),
        # prefix LTE
        (
            rf"{_NCB_ANCHOR}(?:<=|≤|l\.?t\.?e\.?|upto|up\s*to|"
            rf"less\s*than\s*or\s*equal\s*to|not\s*more\s*than|at\s*most|"
            rf"max(?:imum)?\s*(?:of)?)\s*{_NCB_NUM}",
            lambda a: (None, a),
        ),
        # prefix LT (strict)
        (
            rf"{_NCB_ANCHOR}(?:<|lt|below|under|less\s*than)\s*{_NCB_NUM}",
            lambda a: (None, a - 1),
        ),
        # bare: mandatory %, and no alphabetic word may follow (guard 2).
        # The lookahead skips whitespace first — a trailing " SOMETHING" must
        # decline, not just a directly-adjacent letter.
        (
            rf"{_NCB_ANCHOR}{_NCB_NUM_PCT}(?!\s*[^\W\d_])",
            lambda a: (a, a),
        ),
    ]
    _NCB_ARMS_COMPILED = [
        (re.compile(pattern, re.IGNORECASE), band_fn) for pattern, band_fn in _NCB_ARMS
    ]

    @classmethod
    def _parse_ncb_range(cls, text: Any) -> Optional[Tuple[int, int]]:
        """Parse an NCB percentage expression into an inclusive (start, end)
        band clamped to 0-100. Returns None when no NCB expression is present.

        See the operator table above _NCB_ARMS for the full mapping. Returns
        None rather than guessing — a miss is recoverable, a wrong band is not.
        """
        if text is None:
            return None
        s = str(text).replace("_", " ")
        if not s.strip():
            return None
        for pattern, band_fn in cls._NCB_ARMS_COMPILED:
            match = pattern.search(s)
            if match is None:
                continue
            nums = [int(g) for g in match.groups() if g is not None]
            lo, hi = band_fn(*nums)
            lo = cls._NCB_MIN if lo is None else lo
            hi = cls._NCB_MAX if hi is None else hi
            lo = max(cls._NCB_MIN, min(cls._NCB_MAX, lo))
            hi = max(cls._NCB_MIN, min(cls._NCB_MAX, hi))
            if lo > hi:
                lo, hi = hi, lo
            return lo, hi
        return None

    @classmethod
    def _ncb_bool_from_range(
        cls,
        start: Optional[int],
        end: Optional[int],
        fallback: Optional[bool] = None,
    ) -> Optional[bool]:
        """Derive the legacy ``ncb`` boolean from the numeric band.

        ``start > 0`` means "constrained ABOVE THE BASE FLOOR" — it does NOT
        mean "above 25". The 25 in _NCB_LEGACY_THRESHOLD reconstructs a band for
        a legacy label; it plays no part in this derivation, so the two cannot
        drift apart when a carrier ships a different threshold.

            (25, 100) -> True    a band above the base floor
            (0,   24) -> False   the base band
            (0,  100) -> fallback   unconstrained: not a tier at all
            (None, None) -> fallback   no NCB dimension
        """
        if start is None and end is None:
            return fallback
        if (start or cls._NCB_MIN) > cls._NCB_MIN:
            return True
        if end is not None and end < cls._NCB_MAX:
            return False
        return fallback

    @classmethod
    def _is_make_model_sentinel(cls, rule: CommissionRule) -> bool:
        """True when a rule's make or model list is the generic
        'All Other Make/Models' sentinel bucket."""
        cond = getattr(rule, "conditions", None)
        if cond is None:
            return False
        for field in ("vehicle_make", "vehicle_model"):
            for v in getattr(cond, field, None) or []:
                if cls._SENTINEL_MAKE_MODEL_RE.search(str(v)):
                    return True
        return False

    def _clone_make_bucket(
        self,
        rule: CommissionRule,
        suffix: str,
        make: Optional[List[str]],
        make_exclude: Optional[List[str]],
        model_exclude: Optional[List[str]],
        outcome: Optional[CommissionOutcome] = None,
    ) -> CommissionRule:
        """Clone a rule into one make/model exclusion bucket, sharing its
        dates/priority. Make/model include+exclude fields are set explicitly;
        the source rule's own make/model text is cleared. ``outcome`` defaults
        to the source rule's outcome (sentinel fan-out); pass an explicit
        outcome to override it (e.g. the synthesized 0% all-other buckets)."""
        if rule.conditions is not None:
            cond = rule.conditions.model_copy(deep=True)
        else:
            cond = RuleConditions()
        cond.vehicle_make = make
        cond.vehicle_model = None
        cond.vehicle_make_exclude = make_exclude
        cond.vehicle_model_exclude = model_exclude
        return CommissionRule(
            rule_id=f"{rule.rule_id}{suffix}",
            priority=rule.priority,
            conditions=cond,
            outcome=outcome if outcome is not None else rule.outcome,
            valid_from=rule.valid_from,
            valid_to=rule.valid_to,
            rule_description=rule.rule_description,
        )

    @staticmethod
    def _zero_outcome(source: Optional[CommissionOutcome]) -> CommissionOutcome:
        """Build a 0% percentage outcome for a synthesized all-other bucket,
        keeping only the source's payout basis (``applied_on``). All
        deduction-bearing fields are stripped — a zero-payout deal has nothing
        to deduct from, and the CPA-not-collected footnote (carried in
        ``qualifying_clauses`` / ``other_conditions``) would otherwise drive
        ``materialize_cpa_split`` to compute a negative without-CPA payout
        (0% − 1.5% = −1.5%, which fails schema validation). Leaving
        ``qualifying_clauses``/``other_conditions``/``deductions`` unset keeps
        the rule a single 0% deal (CPA-unconstrained = applies to both)."""
        return CommissionOutcome(
            commission_type="percentage",
            percentage_value=0.0,
            applied_on=source.applied_on if source is not None else None,
        )

    def _make_bucket_match_key(self, rule: CommissionRule) -> str:
        """Conditions-only key (make/model AND their exclude siblings removed,
        outcome IGNORED) used to pair a sentinel row with its explicit sibling.
        Unlike _duplicate_collapse_key this excludes the outcome — the sentinel
        and its sibling deliberately carry DIFFERENT rates."""
        cond_dump: Dict[str, Any] = {}
        if rule.conditions is not None:
            cond_dump = rule.conditions.model_dump(exclude_none=False)
            for field in (
                "vehicle_make",
                "vehicle_model",
                "vehicle_make_exclude",
                "vehicle_model_exclude",
            ):
                cond_dump.pop(field, None)
            for k, v in list(cond_dump.items()):
                if isinstance(v, list):
                    cond_dump[k] = sorted(v, key=str)
        return json.dumps(cond_dump, sort_keys=True, default=str)

    def _ncb_tier_pair_key(self, rule: CommissionRule) -> str:
        """Conditions-only key with every NCB field excluded, for NCB-tier twin
        pairing. Outcome is deliberately excluded — the twins differ only in
        rate, which is the entire reason they must be distinguished (a marked
        NCB rule and its base twin share every condition except NCB)."""
        cond_dump: Dict[str, Any] = {}
        if rule.conditions is not None:
            cond_dump = rule.conditions.model_dump(exclude_none=False)
            for field in ("ncb", "ncb_start_range", "ncb_end_range"):
                cond_dump.pop(field, None)
            for k, v in list(cond_dump.items()):
                if isinstance(v, list):
                    cond_dump[k] = sorted(v, key=str)
        return json.dumps(cond_dump, sort_keys=True, default=str)

    def _gcv3w_pair_key(self, rule: CommissionRule) -> str:
        """Conditions-only key with ``fuel_type`` excluded, for pairing the
        base and electric goods 3-wheeler twins. Outcome is excluded — the twins
        differ only in fuel and rate, which is exactly what must be recovered."""
        cond_dump: Dict[str, Any] = {}
        if rule.conditions is not None:
            cond_dump = rule.conditions.model_dump(exclude_none=False)
            cond_dump.pop("fuel_type", None)
            for k, v in list(cond_dump.items()):
                if isinstance(v, list):
                    cond_dump[k] = sorted(v, key=str)
        return json.dumps(cond_dump, sort_keys=True, default=str)

    def _recover_gcv_3w_electric(self, rules: List[CommissionRule]) -> None:
        """Deterministic safety net for the electric goods 3-wheeler.

        The GCCV merged-header 3W cell carries two trailing values
        (``> v_base > v_elec``); codegen emits BOTH as fuel-agnostic
        (``fuel_type=None``) rows and does not reliably tag the electric one
        (the overlay asks for a ``1_GCCV_3W[Electric]`` sub-class + Electric
        fuel, but codegen is non-deterministic). Left alone, the pair is two
        same-condition rows with different rates — an ambiguous overlap that
        drops the electric deal.

        Recover it deterministically: group GCV-3W rows by every condition
        except ``fuel_type``. In a group with exactly TWO distinct rates and no
        fuel tag, the source column order is ``[base, electric]``, so the
        later-first-emitted rate is the electric one — tag those rows
        ``fuel_type=["electric"]``. The base rows stay fuel-None and are pinned
        to the four non-electric fuels by the fuel-bifurcation completeness step,
        yielding two clean deals. Runs before collapse/dedupe, while ``rules`` is
        still in codegen emission order, so the base-before-electric ordering
        holds. Chola GCV-3W only: a goods auto is all-GVW with no other grading
        dimension, so two rates on an otherwise-identical row can only be the
        base/electric column pair.
        """
        groups: "OrderedDict[str, List[Tuple[int, CommissionRule]]]" = OrderedDict()
        for idx, rule in enumerate(rules):
            cond = rule.conditions
            if cond is None or cond.fuel_type:
                continue
            wheels = [str(w).lower() for w in (cond.number_of_wheels or [])]
            if "3w" not in wheels:
                continue
            groups.setdefault(self._gcv3w_pair_key(rule), []).append((idx, rule))

        n_tagged = 0
        for members in groups.values():
            # Distinct rates keyed by first emission index (source column order).
            rate_first_idx: Dict[Any, int] = {}
            for idx, rule in members:
                rate = rule.outcome.percentage_value if rule.outcome else None
                if rate is None:
                    continue
                if rate not in rate_first_idx:
                    rate_first_idx[rate] = idx
            if len(rate_first_idx) != 2:
                continue
            # base = earlier-emitted rate; electric = the later one.
            elec_rate = sorted(rate_first_idx, key=lambda r: rate_first_idx[r])[1]
            for _idx, rule in members:
                if (
                    rule.outcome
                    and rule.outcome.percentage_value == elec_rate
                    and rule.conditions is not None
                ):
                    rule.conditions.fuel_type = ["electric"]
                    n_tagged += 1
        if n_tagged:
            self.notices.append(
                f"GCV-3W electric recovery: tagged {n_tagged} row(s) "
                f"fuel=electric from the base/electric rate pair"
            )

    def _pair_ncb_tiers(self, rules: List[CommissionRule]) -> None:
        """Give the unbanded base twin of a marked NCB rule the complement band.

        Chola PC grids ship parallel table sets: a base set and one or more
        marked sets ('… - NCB GT 25%') at higher rates. The marked rows get a
        band in _transform_conditions; their base twin has none. Both would
        collide on an identical condition key and become ambiguous. Group by a
        conditions-only key with every NCB field excluded; in any group holding
        a marked member, the unbanded members are the base twins.

            group (same conditions except NCB)
            ├── marked   NCB >= 25%  ->  (25, 100)   ncb=True   ─┐
            ├── marked   NCB >= 45%  ->  (45, 100)   ncb=True    │ min start = 25
            └── unbanded (base grid) ->  (0,  24)    ncb=False  ─┘ base_end = 24

        base_end is min(marked starts) - 1, NOT max: the base grid is everything
        below the LOWEST marked tier, so a three-tier ladder complements to
        (0, 24) here, not (0, 44).

        Scoped by condition collision, NOT by product: a marked Package rule
        must not stamp a band on unrelated SOD rules with a different condition
        key (that would under-match in prod). Runs before dedupe so the tier
        ladders normalize their CC boundaries independently."""
        groups: Dict[str, List[CommissionRule]] = {}
        for rule in rules:
            groups.setdefault(self._ncb_tier_pair_key(rule), []).append(rule)

        n_paired = 0
        n_backfilled = 0
        base_ends: Set[int] = set()
        for group in groups.values():
            # A rule is "marked" if it carries a band above the base floor OR,
            # for a rule that predates the band (a legacy blob, or a bool set
            # directly), if ncb is True. The legacy case has no start of its
            # own, so it contributes the historic threshold + 1.
            marked_starts = []
            for r in group:
                cond = r.conditions
                if cond is None:
                    continue
                if (cond.ncb_start_range or self._NCB_MIN) > self._NCB_MIN:
                    marked_starts.append(cond.ncb_start_range)
                elif cond.ncb is True and cond.ncb_start_range is None:
                    marked_starts.append(self._NCB_LEGACY_THRESHOLD + 1)
            if not marked_starts:
                continue
            base_end = min(marked_starts) - 1

            for r in group:
                cond = r.conditions
                if cond is None:
                    continue
                if cond.ncb_start_range is not None or cond.ncb_end_range is not None:
                    continue
                if cond.ncb is True:
                    # Marked but band-less (legacy bool): backfill its implied
                    # band rather than mistaking it for a base twin.
                    cond.ncb_start_range = self._NCB_LEGACY_THRESHOLD + 1
                    cond.ncb_end_range = self._NCB_MAX
                    n_backfilled += 1
                    continue
                cond.ncb_start_range = self._NCB_MIN
                cond.ncb_end_range = base_end
                cond.ncb = False
                n_paired += 1
                base_ends.add(base_end)
        if n_paired:
            # One aggregated line, not one per rule: notices flow unfiltered to
            # the DB and the detail page, and a large grid has hundreds of twins.
            bands = ", ".join(f"0-{e}" for e in sorted(base_ends))
            self.notices.append(
                f"NCB tier: {n_paired} base twin(s) given the complement band "
                f"{bands} (ncb=False), paired with a marked NCB rule"
            )

    def _bifurcate_make_buckets(
        self, rules: List[CommissionRule]
    ) -> List[CommissionRule]:
        """Expand each sentinel 'All Other Make/Models' row into two explicit
        make/model exclusion buckets (R3).

        The Chola GCCV ≤3.5T grid publishes THREE logical buckets sharing one
        rate ladder shape but with distinct rates:
          (a) TATA/Maruti/Mahindra + <model list>            (explicit sibling)
          (b) TATA/Maruti/Mahindra, NOT those models         (sentinel → _XMODEL)
          (c) makes OTHER than TATA/Maruti/Mahindra          (sentinel → _XMAKE)

        (a) is already emitted as an explicit make/model rule. The sentinel row
        carries (b)+(c)'s shared rate. We locate the explicit sibling by the
        collapse key (identical modulo make/model — O(1), no pairwise scan),
        union its makes M and models L, then replace the sentinel with clones
        (b) [make=M, model_exclude=L] and (c) [make_exclude=M].

        No silent constant fallback: when a sentinel has no explicit sibling
        (or the sibling carries no make list) the sentinel is kept verbatim and
        a loud validator WARNING is emitted — buckets are never fabricated."""
        if not rules:
            return rules

        # Index explicit make/model-bearing rules by collapse key (make/model
        # ignored) so each sentinel finds its sibling in O(1).
        explicit_by_key: Dict[str, List[CommissionRule]] = {}
        # Match-keys that already carry an all-other exclusion bucket on input
        # (e.g. a prior fan-out on a double transform) — so the 0% synthesis
        # below stays idempotent and never re-fabricates existing buckets.
        covered_keys: Set[str] = set()
        for r in rules:
            cond = r.conditions
            if cond is None:
                continue
            if cond.vehicle_make_exclude or cond.vehicle_model_exclude:
                covered_keys.add(self._make_bucket_match_key(r))
            if self._is_make_model_sentinel(r):
                continue
            if not (cond.vehicle_make or cond.vehicle_model):
                continue
            explicit_by_key.setdefault(self._make_bucket_match_key(r), []).append(r)

        result: List[CommissionRule] = []
        n_split = 0
        # Match-keys whose all-other buckets came from a real source sentinel —
        # so the 0% synthesis pass below does NOT also fabricate them.
        fanned_keys: Set[str] = set()
        for r in rules:
            if not self._is_make_model_sentinel(r):
                result.append(r)
                continue
            siblings = explicit_by_key.get(self._make_bucket_match_key(r))
            if not siblings:
                self.warnings.append(
                    f"Make-bucket sentinel '{r.rule_id}' has no explicit "
                    f"sibling — buckets not derivable; kept verbatim"
                )
                result.append(r)
                continue
            makes = sorted(
                {m for s in siblings for m in (s.conditions.vehicle_make or [])}
            )
            models = sorted(
                {m for s in siblings for m in (s.conditions.vehicle_model or [])}
            )
            if not makes:
                self.warnings.append(
                    f"Make-bucket sentinel '{r.rule_id}' sibling carries no "
                    f"make list — kept verbatim"
                )
                result.append(r)
                continue
            # bucket (b): the explicit makes, excluding the enumerated models.
            result.append(
                self._clone_make_bucket(
                    r,
                    "_XMODEL",
                    make=makes,
                    make_exclude=None,
                    model_exclude=models or None,
                )
            )
            # bucket (c): all OTHER makes.
            result.append(
                self._clone_make_bucket(
                    r,
                    "_XMAKE",
                    make=None,
                    make_exclude=makes,
                    model_exclude=None,
                )
            )
            fanned_keys.add(self._make_bucket_match_key(r))
            n_split += 1
        if n_split:
            self.notices.append(
                f"Make-bucket fan-out: split {n_split} sentinel row(s) into "
                f"make/model exclusion buckets"
            )

        # 0% synthesis (inverse of the sentinel path). An explicit make/model
        # row with NO source sentinel means the grid graded only the named
        # makes (e.g. TATA/Maruti/Mahindra) and left the "All Other
        # Make/Models" cell BLANK. Per the client, a blank all-other rate is a
        # 0% deal, so materialize the missing all-other buckets at 0% — every
        # graded cell then carries the full make/model distinction. Structural
        # gate (an explicit make/model row without an all-other sibling), so it
        # only fires where grading exists and never fabricates rows for
        # un-graded bands. Group per match-key so each cell synthesizes once.
        n_synth = 0
        for match_key, siblings in explicit_by_key.items():
            if match_key in fanned_keys or match_key in covered_keys:
                continue
            makes = sorted(
                {m for s in siblings for m in (s.conditions.vehicle_make or [])}
            )
            models = sorted(
                {m for s in siblings for m in (s.conditions.vehicle_model or [])}
            )
            if not makes:
                # Only a model list, no make to exclude — cannot derive the
                # "all other makes" bucket; leave the cell as-is.
                continue
            src = siblings[0]
            zero = self._zero_outcome(src.outcome)
            # bucket (b): named makes, excluding the enumerated models, at 0%.
            result.append(
                self._clone_make_bucket(
                    src,
                    "_XMODEL0",
                    make=makes,
                    make_exclude=None,
                    model_exclude=models or None,
                    outcome=zero,
                )
            )
            # bucket (c): all OTHER makes, at 0%.
            result.append(
                self._clone_make_bucket(
                    src,
                    "_XMAKE0",
                    make=None,
                    make_exclude=makes,
                    model_exclude=None,
                    outcome=zero,
                )
            )
            n_synth += 1
        if n_synth:
            self.notices.append(
                f"Make-bucket 0% synthesis: added {n_synth} all-other 0% "
                f"bucket pair(s) for make/model-graded cells with no source "
                f"all-other rate"
            )
        return result

    # MISC-D bodies the client prices as their own deals. Everything else in
    # the sub-product is one bucket — see _bifurcate_subclass_buckets.
    _MISCD_KEEP_OWN_RULE = ("agricultural-tractor", "harvester")

    def _miscd_rest_bucket(self, rule: CommissionRule) -> bool:
        """Is this rule a MISC-D "everything else" rule the merge would widen?

        True when it names at least one body that is not Agricultural Tractor or
        Harvester. Singletons count — the real MSV grid is one row per body.
        """
        cond = rule.conditions
        sub = cond.vehicle_sub_class if cond is not None else None
        if not sub:
            return False
        return any(sc not in self._MISCD_KEEP_OWN_RULE for sc in sub)

    def _canonical_subclass_slugs(self, product: str, sub_product: str) -> List[str]:
        """The carrier's whole body list for a bucket, in rule slug space.

        Lazily read and memoized per instance — once per document in practice.
        The DB read cannot move to ``__init__``: this class is constructed as a
        pure object by ``CholaProductHierarchyValidator`` and by unit tests that
        want only ``_normalize_list_field``, neither of which has a database.

        Fails open to ``[]``; every caller must treat that as "say nothing".
        """
        key = (self._carrier_code or "", product, sub_product)
        cached = self._canonical_subclass_cache.get(key)
        if cached is not None:
            return cached
        if self._carrier_subclass_rows is None:
            self._carrier_subclass_rows = carrier_subclass_rows(
                self._carrier_code or ""
            )

        def _normalize(text: str) -> Optional[List[str]]:
            # Warnings are discarded: this is master-data seed text, not
            # extracted content, and a spelling the dict cannot fold simply
            # drops out of the vocabulary (same contract as the validator).
            keep = self.warnings
            self.warnings = []
            try:
                return self._normalize_list_field(
                    text, VEHICLE_SUB_CLASS_MAPPINGS, "carrier_subclass_seed"
                )
            finally:
                self.warnings = keep

        slugs = canonical_subclass_slugs(
            self._carrier_code or "",
            product,
            sub_product,
            _normalize,
            rows=self._carrier_subclass_rows,
        )
        self._canonical_subclass_cache[key] = slugs
        return slugs

    def _bifurcate_subclass_buckets(
        self,
        rules: List[CommissionRule],
        product_subproduct: Optional[str] = None,
    ) -> List[CommissionRule]:
        """Fan out a multi-subclass rule into one distinct deal per subclass.

        Chola MSV / MISC-D grids publish a body enumeration in a single
        ``2_EXCAVATOR`` row ("Loader, Excavators/Earth Movers, Crane,
        Bulldozers/Bullgraders, Road Rollers, Fork Lift Trucks"), repeated in
        the make and model cells. ``_transform_conditions`` relocates that
        enumeration into ``vehicle_sub_class``; the client wants each body as
        its own deal (Earth Mover / Bulldozer / Road Rollers …), so clone the
        rule once per subclass with a single-value ``vehicle_sub_class``.

        **MISC-D is the exception** (``CINS_MISCD_SUBCLASS_MERGE``, default ON):
        Agricultural Tractor and Harvester still get their own deal, and every
        other body of the source rule collapses into ONE rule carrying a
        multi-value ``vehicle_sub_class``. The merge is per SOURCE rule, never
        per rate: two differently-priced grid rows stay two rules, because the
        thing being suppressed is one rule's fan-out, not a merge across rules.
        That is also why it lives here rather than in a CICS post-processor —
        after the fan-out the only handle on "same source rule" is the ``_SC_``
        suffix, and ``cpa_split`` emits both its variants under one ``rule_id``.

        Outside MISC-D, a rule already scoped to a single subclass passes
        through unchanged — it is not a fan-out candidate. **Inside MISC-D a
        singleton IS widened**, and that is the whole point on real grids: the
        May'26 Chola MSV table does not enumerate its bodies in one cell, it
        publishes one row per body ("2_EXCAVATOR", "3_HARVESTOR", "1_TRAC"), so
        every MISC-D rule reaches here already a singleton. Skipping them made
        the merge a no-op on the only document it exists for.

        The clones keep priority / outcome / dates / description and share the
        same rate; ``rule_id`` gets a subclass suffix to stay unique. Runs
        before duplicate collapse (whose key retains ``vehicle_sub_class``), so
        identical state/coverage clones dedupe while the distinct subclasses
        survive.
        """
        if not rules:
            return rules
        merge_miscd = (
            product_subproduct == "commercial-miscd" and miscd_subclass_merge_enabled()
        )

        # Widening the "rest" bucket to the carrier's FULL body list replaces
        # every such rule's bodies with the SAME list, so body type stops
        # telling those rules apart. That is fine while every other dimension
        # still does — a MISC-D grid always has an ACT rule and a PACK rule, and
        # they differ on coverage. It is NOT fine when two rest-bucket rules are
        # identical in every dimension BUT body ({crane, loader} at 10% vs
        # {excavator} at 12%, same state, same coverage): widening those makes
        # them overlap on every body and price one vehicle twice — a HIGH from
        # rule_overlap_checker, which blocks the cache pin. So the guard keys on
        # the sub-class-free signature, not on a bare rule count.
        #
        # Two refinements, both needed to keep the guard from disabling itself:
        #   - the collision needs DIFFERENT bodies. Two rules with the same
        #     signature and the same body already conflict today; widening does
        #     not create that, and treating it as a collision would let one
        #     pre-existing duplicate switch off the merge for the whole product.
        #   - it needs DIFFERENT rates. Same slot, same rate, different bodies is
        #     precisely the case the merge exists to fold into one deal — those
        #     rules collapse together right after this.
        collision = False
        if merge_miscd:
            by_signature: Dict[str, List[Tuple[str, str]]] = {}
            for rule in rules:
                if not self._miscd_rest_bucket(rule):
                    continue
                bodies = json.dumps(
                    sorted(rule.conditions.vehicle_sub_class or []), default=str
                )
                rate = json.dumps(
                    (
                        rule.outcome.model_dump(exclude_none=False)
                        if rule.outcome is not None
                        else {}
                    ),
                    sort_keys=True,
                    default=str,
                )
                group = by_signature.setdefault(self._subclass_free_signature(rule), [])
                if any(b != bodies and r != rate for b, r in group):
                    collision = True
                    break
                group.append((bodies, rate))
        widen_to_master = merge_miscd and not collision

        def _clone(rule: CommissionRule, sub_classes: List[str], suffix: str):
            clone_cond = rule.conditions.model_copy(deep=True)
            clone_cond.vehicle_sub_class = list(sub_classes)
            # Deep-copy the outcome too: the clones share one source rule, so
            # a by-reference outcome would let a later in-place mutation on
            # one subclass bleed into its siblings (conditions are already
            # deep-copied above — keep the two symmetric).
            clone_outcome = (
                rule.outcome.model_copy(deep=True) if rule.outcome is not None else None
            )
            return CommissionRule(
                rule_id=f"{rule.rule_id}_SC_{suffix}",
                priority=rule.priority,
                conditions=clone_cond,
                outcome=clone_outcome,
                valid_from=rule.valid_from,
                valid_to=rule.valid_to,
                rule_description=rule.rule_description,
            )

        result: List[CommissionRule] = []
        n_split = 0
        n_merged = 0
        for rule in rules:
            cond = rule.conditions
            sub = cond.vehicle_sub_class if cond is not None else None
            # A MISC-D singleton is still a merge candidate — see the docstring:
            # the real MSV grid publishes one row per body, so every rule
            # arrives here already scoped to one.
            if not sub or (len(sub) <= 1 and not merge_miscd):
                result.append(rule)
                continue

            if merge_miscd:
                rest = [sc for sc in sub if sc not in self._MISCD_KEEP_OWN_RULE]
                keep = [sc for sc in sub if sc in self._MISCD_KEEP_OWN_RULE]
                if not rest and len(keep) <= 1:
                    # A lone Agricultural Tractor / Harvester row: already the
                    # shape the client asked for. Pass it through untouched
                    # rather than re-issuing it under a _SC_ rule_id.
                    result.append(rule)
                    continue
                for sc in keep:
                    result.append(_clone(rule, [sc], sc))
                if rest:
                    merged = rest
                    if widen_to_master:
                        # Fail open: an unseeded/unreadable master leaves the
                        # rule's own bodies, never an empty sub-class.
                        master = [
                            sc
                            for sc in self._canonical_subclass_slugs("CV", "MISC-D")
                            if sc not in self._MISCD_KEEP_OWN_RULE
                        ]
                        merged = master or rest
                    result.append(_clone(rule, sorted(merged), "MISCD"))
                    n_merged += 1
                if len(sub) > 1:
                    # Only count a real fan-out here; a widened singleton is
                    # reported by the merge notice below, not as a split.
                    n_split += 1
                continue

            for sc in sub:
                result.append(_clone(rule, [sc], sc))
            n_split += 1

        if n_split:
            self.notices.append(
                f"Subclass fan-out: split {n_split} multi-subclass rule(s) "
                f"into one distinct deal per subclass"
            )
        if n_merged:
            detail = (
                "the carrier's full MISC-D body list"
                if widen_to_master
                else (
                    "each rule's own bodies — two rest buckets differ ONLY by "
                    "body type, so widening would price a vehicle twice"
                )
            )
            self.notices.append(
                f"MISC-D sub-class merge: collapsed {n_merged} rest-bucket "
                f"fan-out(s) into one multi-value rule each, carrying {detail}"
            )
        return result

    def _imply_rollover_on_renewal(
        self,
        rules: List[CommissionRule],
        product_subproduct: Optional[str],
    ) -> None:
        """A CV rule priced for renewals also prices rollovers.

        See ``renewal_implies_rollover_enabled`` for why this is CV-only and
        carrier-agnostic. Mutates in place; idempotent (the membership test
        makes a second run a no-op). A rule with no ``business_type`` at all is
        left alone — it exports a blank BusinessType today, and filling that
        would be a different claim entirely.
        """
        if not rules or product_subproduct not in CV_SUBPRODUCTS:
            return
        if not renewal_implies_rollover_enabled():
            return
        n = 0
        for rule in rules:
            cond = rule.conditions
            bt = cond.business_type if cond is not None else None
            if not bt or "renewal" not in bt or "rollover" in bt:
                continue
            # Appended, so the cell reads "renewal,rollover" in grid order.
            cond.business_type = list(bt) + ["rollover"]
            n += 1
        if n:
            self.notices.append(
                f"Renewal implies rollover: tagged {n} {product_subproduct} "
                f"rule(s) with rollover"
            )

    # Condition fields excluded from duplicate-collapse identity. Same
    # rationale as _DEDUPE_LIST_FIELDS: vehicle_make / vehicle_model commonly
    # carry sentinel ("All Other Make/Models") or indexer-slug junk that
    # makes two otherwise-identical rules appear distinct.
    _COLLAPSE_IGNORE_CONDITION_FIELDS = ("vehicle_make", "vehicle_model")

    def _collapse_duplicate_rules(
        self, rules: List[CommissionRule]
    ) -> List[CommissionRule]:
        """Drop rules with identical conditions (modulo make/model) AND
        identical outcome, keeping the highest-priority duplicate (lowest
        priority number). Preserves rule order otherwise.

        Lossless de-duplication: if two rules have different outcomes the
        collapse skips them so rule_overlap_checker can surface the genuine
        ambiguity. Source: TW July sheet emitted 1953 duplicate rules
        differing only by vehicle_make values like 'Suzuki', 'Honda',
        'Others' on identical commission ladders.
        """
        if not rules:
            return rules

        seen: Dict[str, int] = {}
        winning_priority: Dict[str, int] = {}
        keep = [True] * len(rules)

        for i, rule in enumerate(rules):
            key = self._duplicate_collapse_key(rule)
            if key not in seen:
                seen[key] = i
                winning_priority[key] = rule.priority
                continue
            if rule.priority < winning_priority[key]:
                keep[seen[key]] = False
                seen[key] = i
                winning_priority[key] = rule.priority
            else:
                keep[i] = False

        return [r for r, k in zip(rules, keep) if k]

    def _duplicate_collapse_key(self, rule: CommissionRule) -> str:
        """Build a stable string key from a rule's conditions+outcome for
        duplicate collapse. Excludes make/model fields from conditions.
        """
        cond_dump: Dict[str, Any] = {}
        if rule.conditions is not None:
            cond_dump = rule.conditions.model_dump(exclude_none=False)
            for field in self._COLLAPSE_IGNORE_CONDITION_FIELDS:
                cond_dump.pop(field, None)
            # Normalize list ordering so [a, b] and [b, a] collapse.
            for k, v in list(cond_dump.items()):
                if isinstance(v, list):
                    cond_dump[k] = sorted(v, key=str)

        outcome_dump: Dict[str, Any] = {}
        if rule.outcome is not None:
            outcome_dump = rule.outcome.model_dump(exclude_none=False)

        return json.dumps(
            {"conditions": cond_dump, "outcome": outcome_dump},
            sort_keys=True,
            default=str,
        )

    def _subclass_free_signature(self, rule: CommissionRule) -> str:
        """A rule's grid slot with body type and rate removed.

        Two rules sharing this signature are told apart by ``vehicle_sub_class``
        and nothing else — which is exactly the case where the MISC-D merge must
        NOT widen them to a shared body list. Outcome is excluded on purpose:
        same slot at the same rate would collapse harmlessly, but same slot at
        different rates is the double-pricing case, and both are unsafe to
        widen. Same list-sorting and make/model exclusions as
        ``_duplicate_collapse_key``, so the two cannot disagree about what
        counts as a dimension.
        """
        cond_dump: Dict[str, Any] = {}
        if rule.conditions is not None:
            cond_dump = rule.conditions.model_dump(exclude_none=False)
            for field in self._COLLAPSE_IGNORE_CONDITION_FIELDS:
                cond_dump.pop(field, None)
            cond_dump.pop("vehicle_sub_class", None)
            for k, v in list(cond_dump.items()):
                if isinstance(v, list):
                    cond_dump[k] = sorted(v, key=str)
        return json.dumps(cond_dump, sort_keys=True, default=str)

    def _dedupe_range_overlaps(self, rules: List[CommissionRule]) -> None:
        """Bump successive start = prev_end + 1 for GVW/CC/seating/policies
        ranges within rules that share all other conditions. Mutates rules
        in place.

        Also promotes ladder-edge singletons: if the lowest rule in a group
        has start == end and shares its boundary with the next rule's
        start, it's the "UPTO N" bucket — extend its start to 0. Same for
        the highest rule against the prior rule's end — extend to the
        canonical ceiling. Source: Chola TW July grid encodes CC buckets
        as headers "150cc | 150_350cc | 350cc" where the bare singletons
        are open-ended boundary buckets, not point ranges.
        """
        for start_field, end_field in self._DEDUPE_RANGES:
            sentinels = self._RANGE_SENTINELS.get((start_field, end_field), set())
            groups: Dict[Tuple, List[CommissionRule]] = {}
            for rule in rules:
                if not rule.conditions:
                    continue
                start = getattr(rule.conditions, start_field, None)
                end = getattr(rule.conditions, end_field, None)
                if start is None or end is None:
                    continue
                # Skip "no filter" placeholders so they don't shift adjacent
                # real-range rules.
                if (start, end) in sentinels:
                    continue
                key = self._dedupe_group_key(rule.conditions, start_field, end_field)
                groups.setdefault(key, []).append(rule)

            ceiling = self._RANGE_CEILINGS.get(end_field)

            for group_rules in groups.values():
                if len(group_rules) < 2:
                    continue
                group_rules.sort(
                    key=lambda r: (
                        getattr(r.conditions, start_field),
                        getattr(r.conditions, end_field),
                    )
                )

                # Ladder-edge singleton promotion (before overlap-bump so
                # the bump step normalises the resulting shared boundary).
                first = group_rules[0].conditions
                first_start = getattr(first, start_field)
                first_end = getattr(first, end_field)
                if (
                    first_start == first_end
                    and getattr(group_rules[1].conditions, start_field) == first_end
                ):
                    setattr(first, start_field, 0)

                last = group_rules[-1].conditions
                last_start = getattr(last, start_field)
                last_end = getattr(last, end_field)
                if (
                    ceiling is not None
                    and last_start == last_end
                    and getattr(group_rules[-2].conditions, end_field) == last_start
                ):
                    setattr(last, end_field, ceiling)

                prev_end = getattr(group_rules[0].conditions, end_field)
                for rule in group_rules[1:]:
                    start = getattr(rule.conditions, start_field)
                    end = getattr(rule.conditions, end_field)
                    if start <= prev_end:
                        new_start = prev_end + 1
                        # Don't create an inverted range; skip the bump
                        # and let validator surface the genuine overlap.
                        if new_start <= end:
                            setattr(rule.conditions, start_field, new_start)
                            start = new_start
                    prev_end = max(prev_end, end)

    def _dedupe_group_key(
        self, conditions: RuleConditions, skip_start: str, skip_end: str
    ) -> Tuple:
        """Build a hashable key from all non-range conditions plus the
        OTHER two range fields. The range field being de-overlapped is
        excluded so its buckets get grouped together.
        """
        parts: List[Any] = []
        for field in self._DEDUPE_LIST_FIELDS:
            value = getattr(conditions, field, None)
            parts.append(tuple(sorted(value)) if value else None)
        for field in self._DEDUPE_SCALAR_FIELDS:
            parts.append(getattr(conditions, field, None))
        for start_field, end_field in self._DEDUPE_RANGES:
            if start_field == skip_start:
                continue
            parts.append(getattr(conditions, start_field, None))
            parts.append(getattr(conditions, end_field, None))
        return tuple(parts)

    # Fields dropped from the evidence-group key for cross-group boundary
    # normalization. One vehicle's CC/GVW ladder is commonly split across
    # states / RTO zones with identical engine bands; dropping these regroups
    # the fragmented ladder so a shared tier boundary present anywhere in the
    # same product+coverage counts as evidence. make/model already excluded
    # from _DEDUPE_LIST_FIELDS for the same reason.
    _BOUNDARY_DROP_FIELDS = ("state", "rto")

    def _boundary_group_key(
        self, conditions: RuleConditions, skip_start: str, skip_end: str
    ) -> _BoundaryKey:
        """Relaxed _dedupe_group_key: same key minus the fields that legitimately
        fragment a single vehicle's range ladder (state, rto, make, model). Used
        only by _normalize_boundary_overlaps so a tier boundary present anywhere
        in the same product+coverage is treated as evidence — without collapsing
        genuinely-different vehicle contexts (fuel, sub_class, wheels stay).

        Returns a _BoundaryKey split by field kind so evidence collection can
        treat a None list slot (unset = all values) as a wildcard."""
        lists: List[Any] = []
        for field in self._DEDUPE_LIST_FIELDS:
            if field in self._BOUNDARY_DROP_FIELDS:
                continue
            value = getattr(conditions, field, None)
            lists.append(tuple(sorted(value)) if value else None)
        scalars = [getattr(conditions, f, None) for f in self._DEDUPE_SCALAR_FIELDS]
        ranges: List[Any] = []
        for start_field, end_field in self._DEDUPE_RANGES:
            if start_field == skip_start:
                continue
            ranges.append(getattr(conditions, start_field, None))
            ranges.append(getattr(conditions, end_field, None))
        return _BoundaryKey(tuple(lists), tuple(scalars), tuple(ranges))

    @staticmethod
    def _boundary_keys_compatible(a: _BoundaryKey, b: _BoundaryKey) -> bool:
        """Two boundary keys describe the same ladder for evidence purposes when
        every slot is compatible. A list slot is compatible if either side is
        None (wildcard: unset means all values) or the two sets intersect; a
        scalar/range slot is compatible if either side is None or they are equal.

        This keeps ['petrol'] and ['electric'] ladders disjoint (empty
        intersection) while matching a fuel-less None to both — the exact case
        the fuel-less 40000-43000 band needs to see the fuel-tagged 40001 rung.
        """
        for la, lb in zip(a.lists, b.lists):
            if la is None or lb is None:
                continue
            if not (set(la) & set(lb)):
                return False
        for sa, sb in zip(a.scalars, b.scalars):
            if sa is None or sb is None:
                continue
            if sa != sb:
                return False
        for ra, rb in zip(a.ranges, b.ranges):
            if ra is None or rb is None:
                continue
            if ra != rb:
                return False
        return True

    def _normalize_boundary_overlaps(self, rules: List[CommissionRule]) -> None:
        """Evidence-gated boundary normalization (D1=B). Mutates rules in place.

        Closes the gap left by _dedupe_range_overlaps when a CC/GVW ladder is
        fragmented across states/RTOs so the per-group bump can't see the lower
        rung. A band's start is bumped to start+1 ONLY when that start value
        coincides with some band's END within the same product+coverage ladder
        (evidence the boundary is a real shared tier edge). A start that merely
        equals an unrelated context's end is left alone — the relaxed-but-not-
        empty group key prevents the unsound global flip.

        Floor point-range promotion (X,X) -> (0,X) only when a sibling band in
        the same group starts at X with a wider end (the real ladder rung) — a
        lone (X,X) point rate is never silently widened. Every change is logged.
        """
        for start_field, end_field in self._DEDUPE_RANGES:
            sentinels = self._RANGE_SENTINELS.get((start_field, end_field), set())
            groups: Dict[_BoundaryKey, List[CommissionRule]] = {}
            for rule in rules:
                if not rule.conditions:
                    continue
                start = getattr(rule.conditions, start_field, None)
                end = getattr(rule.conditions, end_field, None)
                if start is None or end is None:
                    continue
                if (start, end) in sentinels:
                    continue
                key = self._boundary_group_key(rule.conditions, start_field, end_field)
                groups.setdefault(key, []).append(rule)

            # Per-key snapshot of original boundaries so evidence is
            # order-independent even as we mutate rules below.
            group_pairs: Dict[_BoundaryKey, List[Tuple[int, int]]] = {}
            for key, group_rules in groups.items():
                group_pairs[key] = [
                    (
                        getattr(r.conditions, start_field),
                        getattr(r.conditions, end_field),
                    )
                    for r in group_rules
                ]

            keys = list(groups.keys())
            # O(G^2) over distinct keys. A pathological document (hundreds of
            # distinct ladders) falls back to exact-key evidence so transform
            # time can't blow up; the wildcard widening is a precision boost,
            # never a correctness prerequisite.
            wildcard_evidence = len(keys) <= 500
            if not wildcard_evidence:
                self.warnings.append(
                    f"Boundary normalization: {len(keys)} distinct groups for "
                    f"{start_field}; using exact-key evidence (wildcard skipped)"
                )

            for key, group_rules in groups.items():
                if len(group_rules) < 2:
                    continue
                if wildcard_evidence:
                    evidence_keys = [
                        k for k in keys if self._boundary_keys_compatible(key, k)
                    ]
                else:
                    evidence_keys = [key]
                pairs: List[Tuple[int, int]] = []
                for k in evidence_keys:
                    pairs.extend(group_pairs[k])
                ends = {e for _s, e in pairs}
                # Values where a wider band starts — confirms a ladder floor.
                floor_starts = {s for s, e in pairs if e != s}

                for rule in group_rules:
                    cond = rule.conditions
                    start = getattr(cond, start_field)
                    end = getattr(cond, end_field)
                    # Floor point-range: (X,X) with a sibling band starting at X.
                    if start == end and start > 0 and start in floor_starts:
                        setattr(cond, start_field, 0)
                        self.notices.append(
                            f"Boundary floor-promotion {start_field} "
                            f"({start},{end}) -> (0,{end})"
                        )
                        continue
                    # Evidence-gated bump: start sits on another band's end.
                    if 0 < start < end and start in ends:
                        new_start = start + 1
                        setattr(cond, start_field, new_start)
                        self.notices.append(
                            f"Boundary bump {start_field} {start} -> {new_start} "
                            f"(shared tier edge in ladder)"
                        )

    # Chola PCV seating bands, supplied by the client: 0-5 is a Normal Taxi
    # (split further by CC), 6-12 a Maxi Cab, 13-16 a Big Taxi. The grids only
    # ever say "UPTO 5" / "ABOVE 6", so the segment label picks the body class
    # and these bands supply the real seating range the label implies — never
    # the reverse, since "ABOVE 6" spans both upper bands.
    _PCV_MAXI_CAB_SEATING = (6, 12)
    _PCV_BIG_TAXI_SEATING = (13, 16)

    def _apply_chola_pcv_subclass(
        self,
        vehicle_sub_class,
        slug,
        fuel_type,
        number_of_wheels,
        seat_start,
        seat_end,
    ):
        """Resolve the Chola PCV body class and its implied seating band.

        Client rules:
        * PCV 3-wheelers are Auto Rickshaw and E-Rickshaw. The grid already
          publishes them as SEPARATE rows at different rates (a fuel-tagged
          Electric row beside the plain one), so this labels each row rather
          than fanning one row into two — a fan-out would copy one rate onto
          both bodies.
        * A PCV taxi seating 6-12 is a Maxi Cab.
        * A PCV taxi seating 13-16 is a Big Taxi. Chola's hierarchy master has
          no row for it — its only >6 body is "Greater Than Six Seater", one
          bucket its MMV book maps BOTH a 12- and a 16-seater onto, so it cannot
          express the client's 6-12 / 13-16 split. Emitting the client's name
          raises a MEDIUM from CholaProductHierarchyValidator, exactly as
          ``maxicab`` already does; the alternative is exporting a label that
          merges the two bands.
        * A 4-wheel PCV seating <=5 is an ordinary Taxi. The grid identifies
          those rows only by their CC band ("2_4W_LT_1500CC"), which carries no
          body word at all.

        Returns ``(vehicle_sub_class, seat_start, seat_end)`` unchanged for every
        other carrier, product and row shape.
        """
        if (
            self._carrier_code != "Chola"
            or self._product_subproduct != "commercial-pcv"
        ):
            return vehicle_sub_class, seat_start, seat_end

        wheels = {str(w).lower() for w in (number_of_wheels or [])}
        current = set(vehicle_sub_class or ())
        slug_text = str(slug or "").lower()
        is_electric = any("electric" in str(f).lower() for f in (fuel_type or ()))

        # 3-wheeler: Electric row -> E-Rickshaw, plain row -> Auto Rickshaw.
        if "3w" in wheels or "3w_auto" in slug_text.replace(" ", "_"):
            return (
                ["e-rickshaw"] if is_electric else ["auto-rickshaw"],
                seat_start,
                seat_end,
            )

        # The two body-keyed arms run BEFORE the 4W gate: they key on a body the
        # source already named, which is a stronger signal than the wheel count,
        # and the "PCCV > 6" rows carry NO number_of_wheels at all (the overlay
        # sets wheels only on the "PCCV < 6" table). Gating them on
        # "4w-and-above" made both bands dead code on the only rows that can
        # reach them — every Maxi Cab and Big Taxi kept the raw "ABOVE 6" (6, 99)
        # sentinel, and the Big Taxis kept the bare "taxi" slug and were then
        # cloned into Private Car by pcv_taxi_mirror. Both bodies are 4-wheelers
        # by definition (cf. _FOUR_WHEEL_ONLY_SUBCLASS_STEMS, which lists
        # maxicab), so there is nothing for a wheel check to protect here.
        if "maxicab" in current:
            return vehicle_sub_class, *self._PCV_MAXI_CAB_SEATING
        if "big-taxi" in current or ("taxi" in current and (seat_start or 0) >= 6):
            # "Big Taxi" — the source says only "ABOVE 6", i.e. (6, 99), which
            # spans both upper bands; the segment label is what tells them
            # apart, so the band is supplied here rather than read off seats.
            # The seat_start >= 6 test is what keeps the "PCCV < 6" taxis, which
            # carry (0, 5), out of this arm. The explicit "big-taxi" arm catches
            # the other spelling: a grid that writes "Big Taxis" in the body cell
            # normalizes straight to the slug and never passes through "taxi",
            # and it must still pick up the band.
            return ["big-taxi"], *self._PCV_BIG_TAXI_SEATING

        # The remaining arm INFERS a body the source did not name, so it does
        # need the 4W signal.
        if "4w-and-above" not in wheels:
            return vehicle_sub_class, seat_start, seat_end

        # 4-wheeler <=5 seats: an ordinary taxi. Only claim the rows that have
        # no body class of their own, so "maxi cab" is never overwritten.
        if not current and (seat_end is not None and seat_end <= 5):
            return ["taxi"], seat_start, seat_end

        return vehicle_sub_class, seat_start, seat_end

    # A cc-band token in a TW column header ("150cc", "150_350cc", "350cc").
    _TW_CC_BAND_RE = re.compile(r"\d+\s*(?:_\s*\d+\s*)?cc\b", re.IGNORECASE)

    def _apply_chola_tw_subclass(self, vehicle_sub_class, entry, cc_start, cc_end):
        """Derive the Chola two-wheeler body from its grid column.

        Each make group publishes four columns — a ``SCOOTER`` column beside
        three motorcycle displacement bands::

            | Hero OEM > 150cc | Hero OEM > SCOOTER | Hero OEM > 150_350cc | Hero OEM > 350cc |

        Scooters are not cc-graded here, motorcycles are, so a cc-graded column
        is a Bike. Codegen only carries the body word through when the header
        said ``SCOOTER``; the cc columns arrive with no sub-class at all (1101
        entries on doc 733711bd), which is what left 1523 rules failing the
        mandatory-sub-class check.

        The ``All Electric Make`` group already resolves to bike/scooter with
        ``fuel_type`` Electric and is left exactly as-is — this only fills an
        EMPTY value.
        """
        if self._carrier_code != "Chola" or self._product_subproduct != "two-wheeler":
            return vehicle_sub_class
        if vehicle_sub_class:
            return vehicle_sub_class

        probe = " ".join(
            str(v)
            for v in (
                getattr(entry, "rule_text", "") or "",
                getattr(entry, "source_section", "") or "",
            )
            if v
        )

        # An explicit body word always wins. _extract_sub_class_from_key already
        # owns that hint table (scooter / e-bike / moped / bike, longest-first),
        # so a "TW E-BIKE" section is never overruled by the cc fallback below —
        # its rows still read "UPTO 150 CC".
        hinted = self._extract_sub_class_from_key(probe)
        if hinted:
            return hinted

        # No body word: fall back to the column's grading axis. Scooters are not
        # cc-graded in this grid, motorcycles are.
        if self._TW_CC_BAND_RE.search(probe):
            return ["bike"]
        # Sanitised cc bands survive as a real range; the (0, 99999) sentinel
        # means "no cc filter" and proves nothing about the body.
        if (cc_start, cc_end) != (0, 99999) and (cc_start or cc_end):
            return ["bike"]
        return vehicle_sub_class

    # Smallest plausible engine displacement, in cc. Below this a "cc" value is
    # a rate that leaked out of a payout column, not a displacement — the TW
    # grid produced cc=0.37 / 0.22 / -0.205 / -0.055 on 124 rows.
    _MIN_PLAUSIBLE_CC = 50

    def _cc_or_leaked_rate(self, cc_text):
        """Drop a "cc" value that is really a commission rate.

        The TW grid's payout cells sometimes land in the cc condition, giving
        fractional (and occasionally negative) displacements. Left alone they
        become a bogus tight band like (0, 0) that silently excludes every real
        policy. A single-valued cc below 50cc cannot be an engine size, so treat
        it as absent and let the open (0, 99999) sentinel apply; the row's real
        band, when it has one, is recovered from the slug further down.

        Ranges are left untouched — a genuine band always has a plausible upper
        bound, and dropping half of one would be worse than keeping it.
        """
        if cc_text is None or isinstance(cc_text, (list, tuple, dict)):
            return cc_text
        text = str(cc_text).strip()
        if not text:
            return cc_text
        try:
            value = float(text)
        except ValueError:
            return cc_text  # "UPTO 150", "150-350" etc — a real range expression
        if abs(value) >= self._MIN_PLAUSIBLE_CC:
            return cc_text
        self.warnings.append(
            f"Discarded implausible cc value {text!r} (below {self._MIN_PLAUSIBLE_CC}cc "
            f"— a commission rate leaked into the cc column)"
        )
        return None

    @staticmethod
    def _rule_id_token(bodies) -> str:
        """Body slugs as a rule_id-legal token (``^[A-Za-z0-9_-]+$``)."""
        joined = "-".join(str(b) for b in bodies if b)
        return re.sub(r"[^A-Za-z0-9_-]+", "-", joined).strip("-") or "NA"

    def _strip_gcv_subclass(self, rules, product_subproduct) -> int:
        """Clear ``vehicle_sub_class`` on every GCV rule. Returns the count.

        A goods-carrying grid prices by WEIGHT BAND, not by body type. The
        GCCV sheet's own SUBCLASS column holds the weight ladder::

            | GCCV | 2_UPTO_3.5T    | TATA/ Maruti/ Mahindra | Ace, Super Ace, … |
            | GCCV | 3_3.5T_TO_7.5T | 3_3.5T_TO_7.5T         | 3_3.5T_TO_7.5T    |

        so there is no body class in the source at all — the weight band is
        already captured as the gvw range. The old behaviour recorded the
        ``not-available`` sentinel here, which the export then WIDENED into all
        15 seeded GCV bodies (Truck, Tipper, Tanker, …), shipping a guess the
        grid never made.

        The strip is absolute: a body genuinely resolved from a slug (the
        ``auto-rickshaw`` / ``e-rickshaw`` off ``1_GCCV_3W``) goes too. And it
        is carrier-agnostic — this is a fact about goods-carrying grids, not a
        Chola quirk — so it gates on ``product_subproduct`` alone.

        **Ordering is load-bearing: this must run LAST in ``_transform_product``**,
        after every writer of the field, and in particular after
        ``_bifurcate_gcv_wheels``:

        - ``_transform_product``'s backfill re-adds an ``inferred_sub_class``
          from the source section AFTER ``_transform_conditions`` returns, so a
          strip inside the conditions chain would simply be undone.
        - ``_bifurcate_subclass_buckets`` clones body enumerations.
        - ``_is_four_wheel_only_subclass`` is the SOLE 4W-only signal for the
          special-purpose bodies that carry no GVW band (tractor, excavator,
          harvester, bus, maxicab). Strip before bifurcation and every one of
          them falls through to the both-wheels arm and doubles into spurious
          3W clones — the (0,0) catch-all regression that logic exists to kill.
          Run after it and the scoping survives in ``number_of_wheels``.

        Known limitation: when two differently-bodied clones carry the SAME
        rate, the follow-up collapse keeps one rule and the other body's token
        leaves the coverage blob with it. Rare on a GCV grid, whose sub-class
        column is the weight ladder rather than a body enumeration.
        """
        if product_subproduct != "commercial-gcv" or not gcv_no_subclass_enabled():
            return 0
        stripped = 0
        for rule in rules:
            if rule.conditions is not None and rule.conditions.vehicle_sub_class:
                bodies = rule.conditions.vehicle_sub_class
                rule.conditions.vehicle_sub_class = None
                # Keep the body word as PROVENANCE on the rule_id.
                # RowCoverageValidator's blob is built from the rule's identity
                # fields, and its declined-row check (the empty-rate "4_OTHERS"
                # row, a HIGH) was satisfied by the "not-available" sentinel
                # this strip deletes. rule_id is excluded from
                # _duplicate_collapse_key, so the suffix cannot block the
                # follow-up collapse, and _bifurcate_subclass_buckets already
                # suffixes rule_ids the same way.
                rule.rule_id = f"{rule.rule_id}_SC_{self._rule_id_token(bodies)}"
                stripped += 1
        if stripped:
            self.notices.append(
                f"GCV: cleared vehicle_sub_class on {stripped} rule(s) — a "
                f"goods-carrying grid prices by weight band, not body type"
            )
        return stripped

    # Leading enumeration index on a CV segment slug ("1_GCCV_3W", "3_HARVESTOR").
    _SUBCLASS_SLUG_INDEX_RE = re.compile(r"^\s*\d+\s*_")
    # Bracketed qualifier ("1_TRAC[RENEWAL]", "1_GCCV_3W[Electric]") — the
    # renewal/electric dimension is picked up elsewhere, never a body type.
    _SUBCLASS_SLUG_QUALIFIER_RE = re.compile(r"\[[^\]]*\]")
    # CC-band slug ("2_4W_LT_1500CC"), matching _recover_cc_range's anchoring.
    _SUBCLASS_CC_BAND_RE = re.compile(r"(?:^|[_\s])(?:lt|gt)[_\s]*\d+\s*cc", re.I)

    @classmethod
    def _normalize_subclass_slug(cls, text) -> Optional[str]:
        """Strip CV segment-slug scaffolding so the mappings can see the body word.

        Codegen writes the grid's segment label verbatim into
        ``conditions["vehicle_sub_class"]``: ``"1_GCCV_3W"``, ``"1_TRAC[RENEWAL]"``,
        ``"3_HARVESTOR"``. ``_normalize_list_field`` word-boundary-matches keys of
        5 characters or fewer against ``[\\s,;/\\-]`` only, so an underscore-joined
        slug can never match a short key like "trac". Drop the leading index and
        the bracketed qualifier, and turn underscores into spaces, leaving plain
        text for the normal mapping pass. Non-slug input is returned unchanged.
        """
        if text is None:
            return None
        if isinstance(text, (list, tuple)):
            text = ", ".join(str(t) for t in text if t)
        s = str(text)
        s = cls._SUBCLASS_SLUG_QUALIFIER_RE.sub(" ", s)
        s = cls._SUBCLASS_SLUG_INDEX_RE.sub("", s)
        s = s.replace("_", " ")
        return re.sub(r"\s+", " ", s).strip()

    def _subclass_slug_is_dimension(self, text) -> bool:
        """True when a "sub-class" slug actually encodes a weight or CC band.

        The Chola CV grids enumerate their GVW ladder as segment labels
        ("2_UPTO_3.5T", "7_20T_TO_40T") and the 4W taxi CC split as
        "2_4W_LT_1500CC". Codegen files those under ``vehicle_sub_class``, but
        they are dimensions, not body types — ``_recover_gvw_range`` and
        ``_recover_cc_range`` already harvest them into the gvw/cc ranges. They
        were then re-read as a sub-class and emitted 223 bogus
        "Unknown vehicle_sub_class" warnings per document. Shape-guarded by the
        same ``_gvw_slug_or_empty`` the recovery path uses, so a real body word
        is never suppressed.
        """
        if not text:
            return False
        if isinstance(text, (list, tuple)):
            return all(self._subclass_slug_is_dimension(t) for t in text if t)
        probe = str(text)
        if self._SUBCLASS_CC_BAND_RE.search(probe):
            return True
        # A GVW band only counts as a dimension when there is no body word left
        # once the scaffolding is stripped — "1_GCCV_3W" carries digits but is a
        # body slug, while "2_UPTO_3.5T" reduces to pure weight tokens.
        if not self._gvw_slug_or_empty(probe):
            return False
        residue = self._normalize_subclass_slug(probe) or ""
        return not self._normalize_list_field_quiet(residue)

    def _normalize_list_field_quiet(self, text) -> Optional[List[str]]:
        """`_normalize_list_field` without the unknown-value warning side effect."""
        mark = len(self.warnings)
        try:
            return self._normalize_list_field(
                text, VEHICLE_SUB_CLASS_MAPPINGS, "vehicle_sub_class"
            )
        finally:
            del self.warnings[mark:]

    def _gvw_slug_or_empty(self, header: Optional[str]) -> str:
        """Return the verbatim label if it encodes a GVW range (tonnage / kg /
        qualified weight), else ''. Mirrors code_gen_executor.parse_gvw_range so
        the deterministic recovery path applies the same shape-guard — CC/KW
        engine-displacement headers are rejected (they belong to cc)."""
        if header is None:
            return ""
        h = str(header).strip()
        if not h:
            return ""
        hu = h.upper().replace("_", " ")
        # Neutralise bracketed qualifiers so 'UPTO 1000 CC[ NCB GT 25%]' is
        # rejected by the CC guard below. Depth-tracking manual scan, matching
        # code_gen_executor._strip_bracket_qualifiers exactly (parity is pinned
        # by test_range_parsing).
        _out, _depth = [], 0
        for _ch in hu:
            if _ch == "[":
                if _depth == 0:
                    _out.append(" ")
                _depth += 1
            elif _ch == "]":
                if _depth > 0:
                    _depth -= 1
            elif _depth == 0:
                _out.append(_ch)
        hu = "".join(_out)
        if not any(c.isdigit() for c in hu):
            return ""
        if (
            "CC" in hu.split()
            or hu.endswith("CC")
            or "KW" in hu.split()
            or hu.endswith("KW")
        ):
            return ""
        if "GVW" in hu or "KG" in hu:
            return h
        # Tonnage scan: a digit immediately followed by 'T' not part of another
        # word (so "SEATER", "TRACTOR", "TO" don't trigger).
        i = 0
        while i < len(hu):
            if hu[i] == "T":
                j = i - 1
                while j >= 0 and hu[j] == " ":
                    j -= 1
                prev_digit = j >= 0 and hu[j].isdigit()
                nxt = hu[i + 1] if i + 1 < len(hu) else ""
                if prev_digit and not nxt.isalpha():
                    return h
            i += 1
        qualifiers = (
            "UPTO",
            "UP TO",
            "ABOVE",
            "BELOW",
            "GREATER THAN",
            "MORE THAN",
            "LESS THAN",
            ">",
            "<",
        )
        if any(q in hu for q in qualifiers):
            return h
        return ""

    def _recover_gvw_range(
        self, entry_conditions: Dict[str, Any], raw: RawExtraction
    ) -> Optional[Tuple[int, int]]:
        """Recover a GVW (start, end) from sub-class slugs when conditions["gvw"]
        was absent. Codegen stores grid dims under conditions["subclass"] (a key
        the transformer otherwise ignores) and weight bands are sometimes baked
        into the sub-class slug ("2_UPTO_3.5T"). Shape-guarded via
        _gvw_slug_or_empty so a real sub-class ("3 WHEELER", "16 SEATER") is
        never misread. Open ends normalise to 0 / 999999. Returns None on miss."""
        candidates = [
            entry_conditions.get("subclass"),
            entry_conditions.get("vehicle_sub_class"),
            getattr(raw, "global_vehicle_type_text", None),
        ]
        for cand in candidates:
            slug = self._gvw_slug_or_empty(cand)
            if not slug:
                continue
            rs, re_ = self._parse_range_field(slug)
            if rs is None and re_ is None:
                continue
            start = 0 if rs is None else rs
            end = 999999 if re_ is None else re_
            self.notices.append(
                f"GVW recovered from sub-class slug '{cand}' -> ({start}, {end})"
            )
            return start, end
        return None

    @staticmethod
    def _segment_probe(entry_conditions: Dict[str, Any], entry) -> str:
        """Lower-cased blob of the fields that carry a Chola CV section token."""
        return " ".join(
            str(v)
            for v in (
                entry_conditions.get("vehicle_sub_class"),
                getattr(entry, "source_section", "") or "",
                getattr(entry, "product_type", "") or "",
                getattr(entry, "rule_text", "") or "",
            )
            if v
        ).lower()

    def _recover_seating_range(
        self, entry_conditions: Dict[str, Any], entry
    ) -> Optional[Tuple[int, int]]:
        """Recover the seating band from a Chola ``PCCV < 6`` / ``PCCV > 6``
        section token when no per-row seating survived (sentinel ``(0, 99)``).

        Grid-verbatim bands (G3a): ``<6`` → ``(0, 5)``, ``>6`` → ``(6, 99)``
        (the 6-seater belongs to the upper band). Fires only on explicit token
        presence, so non-PCCV rows are never touched. Returns None on miss."""
        probe = self._segment_probe(entry_conditions, entry).replace(" ", "")
        if "pccv>6" in probe or "pccv>=6" in probe:
            self.notices.append("Seating recovered from PCCV>6 token -> (6, 99)")
            return 6, 99
        if "pccv<6" in probe:
            self.notices.append("Seating recovered from PCCV<6 token -> (0, 5)")
            return 0, 5
        return None

    def _recover_cc_range(
        self, entry_conditions: Dict[str, Any], entry
    ) -> Optional[Tuple[int, int]]:
        """Recover the CC band from a Chola PCCV<6 4W taxi sub-class slug
        (``2_4W_LT_1500CC`` / ``3_4W_GT_1500CC``) when conditions["cc"] was
        absent (sentinel ``(0, 99999)``). ``LT`` → ``(0, N)``; ``GT`` →
        ``(N, 99999)`` (the boundary de-overlap bumps the GT start to N+1).
        Chola-gated and slug-anchored: the ``lt``/``gt`` token must sit at a
        word boundary (start or after ``_``/whitespace) so a legit make/model
        that merely contains those letters before a CC number (e.g. a
        ``"Bolt 1500cc"`` variant) can never spuriously narrow an all-CC rule.
        Returns None on miss."""
        if self._carrier_code != "Chola":
            return None
        probe = self._segment_probe(entry_conditions, entry)
        lt = re.search(r"(?:^|[_\s])lt[_\s]*(\d+)\s*cc", probe)
        if lt:
            n = int(lt.group(1))
            self.notices.append(f"CC recovered from LT slug -> (0, {n})")
            return 0, n
        gt = re.search(r"(?:^|[_\s])gt[_\s]*(\d+)\s*cc", probe)
        if gt:
            n = int(gt.group(1))
            self.notices.append(f"CC recovered from GT slug -> ({n}, 99999)")
            return n, 99999
        return None

    def _expand_state_names(
        self,
        state_text: Optional[str],
        master_data: Optional[List[RawMasterDataEntry]],
        _top: bool = True,
    ) -> Optional[List[str]]:
        """
        Expand state text to canonical lowercase-hyphenated state names.
        Handles exclusion patterns by expanding to the complement set.

        ``_top`` marks the outermost call. The function recurses for exclusion
        lists and per master-data entry; the "unknown state" warning and the
        empty-text fallback only make sense at the top, so recursive call sites
        pass ``_top=False`` to keep the warnings list from flooding.
        """
        if not state_text:
            if master_data:
                # Only inherit master-data states when exactly one state_mapping
                # entry exists. With CAT A = AP,TS and CAT B = MH,GJ, blindly
                # taking the first entry stamps every state-less rule with CAT
                # A's states — silently wrong.
                mappings = [
                    m
                    for m in master_data
                    if m.entry_type == "state_mapping" and m.values
                ]
                if len(mappings) == 1:
                    return self._expand_state_names(
                        mappings[0].values, None, _top=False
                    )
                if len(mappings) > 1 and _top:
                    self.warnings.append(
                        "Ambiguous state fallback: multiple state_mapping "
                        "entries, no state text on rule"
                    )
            return None

        text = state_text.lower()
        discovered_names = []

        # Handle exclusions ("All states except...")
        if "except" in text or "excluding" in text or "other than" in text:
            exclude_pattern = (
                r"(?:except|excluding|other than)\s+([^,;]+(?:[,;][^,;]+)*)"
            )
            match = re.search(exclude_pattern, state_text, re.IGNORECASE)
            if match:
                exclude_text = match.group(1)
                excluded_names = self._expand_state_names(
                    exclude_text, None, _top=False
                )
                if excluded_names:
                    return sorted(VALID_STATE_NAMES - set(excluded_names))

        # Proactive master data expansion — only for state-grouping entries.
        # Skip 'declined' and other non-state entries whose values may contain
        # exclusion phrases that would over-expand to all states.
        if master_data:
            upper_text = state_text.upper()
            for m in master_data:
                if not m.key:
                    continue
                if m.entry_type not in ("state_mapping", "rto_mapping"):
                    continue
                key_upper = m.key.upper()
                if re.search(rf"\b{re.escape(key_upper)}\b", upper_text):
                    if m.values:
                        names = self._expand_state_names(m.values, None, _top=False)
                        if names:
                            discovered_names.extend(names)

        # Standard state name/abbreviation mapping, then the multi-state group
        # tokens ("NE" -> the north-east block). One loop over both tables: the
        # boundary rule is the load-bearing part and must not drift between
        # them. A short key is boundary-matched so it cannot fire inside a word
        # -- "NE" must not match the "NEW(100-500 NOP)" tier label sitting in
        # the very next cell of the grid that token comes from.
        for name, canonicals in chain(
            ((k, [v]) for k, v in STATE_NAME_MAPPINGS.items()),
            STATE_GROUP_MAPPINGS.items(),
        ):
            if len(name) <= 3:
                pattern = rf"(?:^|[\s,_\-\/]){re.escape(name)}(?=$|[\s,_\-\/])"
            else:
                pattern = rf"{re.escape(name)}"

            if re.search(pattern, text, re.IGNORECASE):
                discovered_names.extend(canonicals)

        # Chola UW-zone label -> state expansion (scoped query built once in
        # transform()). Only at _top: recursive calls process master-data
        # values / exclusion lists, not the rule's own location text, so zone
        # matching (and its warning) would be noise there. Uses the SAME
        # normalization as _expand_rto_codes so "UP- East" (hyphen) resolves
        # a state here exactly like it resolves RTO codes there — an earlier
        # asymmetry (plain .lower() here vs. the RTO side's hyphen-collapsing
        # normalizer) silently dropped the state for that zone label.
        if _top and self._carrier_code == "Chola" and self._carrier_zone_map:
            normalized_zone_text = self._normalize_zone_label(state_text)
            matched_zones = self._match_carrier_zones(normalized_zone_text)
            for zone_slug in matched_zones:
                discovered_names.extend(
                    self._carrier_zone_map[zone_slug].get("state", [])
                )
            if not matched_zones:
                self._warn_unmatched_zone(state_text, normalized_zone_text)

        if discovered_names:
            return sorted(set(discovered_names))

        if _top:
            self.warnings.append(f"Unknown state: {state_text}")
        return None

    @staticmethod
    def _normalize_zone_label(s: Optional[str]) -> str:
        """Collapse common abbreviations, hyphens and whitespace so that
        "CAT 0"/"Category 0"/"category-0"/"cat0" — and "UP EAST"/"UP- East"/
        "up_east" — all normalize equivalently. Shared by ``_expand_rto_codes``
        and ``_expand_state_names`` so a Chola zone label matches identically
        on both paths (an earlier asymmetry here meant the RTO side resolved
        a hyphenated zone label while the state side silently missed it)."""
        s = (s or "").upper()
        s = re.sub(r"\bCATEGORY\b", "CAT", s)
        s = re.sub(r"\bZONES?\b", "ZONE", s)
        s = re.sub(r"\bTIERS?\b", "TIER", s)
        s = re.sub(r"\bCLUSTERS?\b", "CLUSTER", s)
        s = re.sub(r"\bGROUPS?\b", "GROUP", s)
        s = re.sub(r"[-_]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _match_carrier_zones(self, normalized_text: str) -> List[str]:
        """Zone slugs from ``self._carrier_zone_map`` whose label (slug with
        hyphens replaced by spaces, upper-cased) appears word-boundary
        anchored in ``normalized_text``.

        A two-token label also matches with its tokens REVERSED. The same Chola
        zone is spelled "UP- East" in the Private Car / CV headers and "East UP"
        in the Two-Wheeler key column; only the forward order matched, so 115
        Two-Wheeler rules resolved no zone, claimed no explicit RTO, and fell
        back to a full Uttar Pradesh fill -- silently disabling the UP /
        UP- East complement on that sheet.

        Kept deliberately narrow. Only exactly-two-token labels are reversed
        (full permutations of 3+ tokens are combinatorial and would start
        matching text nobody wrote), the reversed form is still one adjacent
        word-boundary-anchored phrase (so it can never match MORE text than the
        forward form does -- it is an extra specific alternative, not a
        loosening), and a reversal that collides with another zone's forward
        label is dropped rather than allowed to claim that zone's rows.
        """
        forward_labels = {
            slug.replace("-", " ").upper() for slug in self._carrier_zone_map
        }
        matched = []
        for zone_slug in self._carrier_zone_map:
            label = zone_slug.replace("-", " ").upper()
            variants = [label]
            parts = label.split(" ")
            if len(parts) == 2:
                flipped = f"{parts[1]} {parts[0]}"
                if flipped not in forward_labels:
                    variants.append(flipped)
            if any(
                re.search(rf"\b{re.escape(v)}\b", normalized_text) for v in variants
            ):
                matched.append(zone_slug)
        return matched

    def _warn_unmatched_zone(self, raw_text: str, normalized_text: str) -> None:
        """Chola-only: surface a rule whose location text looks like it names
        a UW zone (contains the word ZONE, post-normalization) but matched no
        configured zone. Converts a silent under-fill into a visible signal.
        Deduped per distinct normalized text per document."""
        if "ZONE" not in normalized_text:
            return
        if normalized_text in self._zone_warned:
            return
        self._zone_warned.add(normalized_text)
        self.warnings.append(f"Chola zone label not found in master: {raw_text!r}")

    def _expand_rto_codes(
        self,
        rto_text: Optional[str],
        master_data: Optional[List[RawMasterDataEntry]],
        state_slugs: Optional[List[str]] = None,
    ) -> Optional[List[str]]:
        """
        Expand RTO text to canonical RTO codes.
        Resolves category labels (CAT 0, Zone A, Category 1) via rto_mapping master data entries.
        Accepts both hyphenated (MH-01) and concatenated (MH01) code formats.
        Chola-gated: also resolves a configured UW-zone label (e.g. "Central
        MH", "UP- East") to the RTO codes it represents via a scoped
        ``CarrierRTOZoneMaster`` query built once in ``transform()``.

        ``state_slugs`` — the states ``_expand_state_names`` resolved from the
        SAME text — enables the last-resort global RTO master lookup described
        below. Optional so the several call sites that have no state context
        keep today's behaviour.
        """
        if not rto_text:
            return None

        code_pattern = r"\b[A-Z]{2}-?\d{1,3}\b"

        literal_codes = re.findall(code_pattern, rto_text.upper())
        discovered = list(literal_codes)

        if master_data:
            normalized_text = self._normalize_zone_label(rto_text)
            for m in master_data:
                if m.entry_type != "rto_mapping" or not m.key or not m.values:
                    continue
                key_norm = self._normalize_zone_label(m.key)
                if not key_norm:
                    continue
                if re.search(rf"\b{re.escape(key_norm)}\b", normalized_text):
                    codes = re.findall(code_pattern, m.values.upper())
                    discovered.extend(codes)

        if self._carrier_code == "Chola" and self._carrier_zone_map:
            normalized_zone_text = self._normalize_zone_label(rto_text)
            matched_zones = self._match_carrier_zones(normalized_zone_text)
            for zone_slug in matched_zones:
                discovered.extend(self._carrier_zone_map[zone_slug].get("rto", []))

            # Last resort: the carrier's zone master had nothing, so ask the
            # GLOBAL RTO master whether the label names a locality inside the
            # state this rule already resolved. Chola's Two Wheeler grid keys a
            # row "KA (Bangalore)" against a differently-priced "TN/ROK" row,
            # but seeds no bangalore zone -- without this the metro row claims
            # no RTO, the export fills it with all 71 Karnataka codes, and the
            # two rows collide on identical geography.
            #
            # Ordering is the guard: this runs only when nothing else matched,
            # so a seeded carrier zone always wins and this can only ever add
            # geography where there was none. See ``match_rto_localities`` for
            # why the state scope and the boundary rule are load-bearing.
            fallback_codes: List[str] = []
            if not discovered and state_slugs and self._rto_locality_map:
                from master_data.services.resolver import match_rto_localities

                fallback_codes = match_rto_localities(
                    self._rto_locality_map, state_slugs, rto_text
                )
                discovered.extend(fallback_codes)

            # Warn only if BOTH the carrier zone master and the global-master
            # fallback came up empty -- a label the fallback resolved is not an
            # unmatched zone label.
            if not matched_zones and not fallback_codes:
                self._warn_unmatched_zone(rto_text, normalized_zone_text)

        if not discovered:
            return None

        return sorted(set(discovered))

    # ==================== OUTCOMES ====================

    def _transform_outcome(
        self, entry: RawCommissionEntry, raw: RawExtraction
    ) -> CommissionOutcome:
        """Transform entry to CommissionOutcome."""
        commission_type, parsed_values = self._detect_commission_type(
            entry.commission_value
        )

        # Build outcome
        outcome_dict = {"commission_type": commission_type, **parsed_values}

        # Add GST information
        gst_applicable = entry.gst_applicable
        gst_percentage = None

        if raw.global_gst_percentage:
            gst_match = re.search(r"(\d+(?:\.\d+)?)", raw.global_gst_percentage)
            if gst_match:
                gst_percentage = float(gst_match.group(1))
                gst_applicable = True

        outcome_dict["gst_applicable"] = gst_applicable
        if gst_percentage:
            outcome_dict["gst_percentage"] = gst_percentage

        # Add applied_on
        if entry.applied_on_text:
            outcome_dict["applied_on"] = entry.applied_on_text
        elif raw.global_applied_on:
            outcome_dict["applied_on"] = raw.global_applied_on

        # Parse bonuses (from global settings only)
        bonuses = self._parse_bonuses(None)
        if bonuses:
            outcome_dict["bonuses"] = bonuses
            if commission_type == "percentage":
                outcome_dict["commission_type"] = "hybrid"

        # Parse deductions (from global settings only)
        deductions = self._parse_deductions(
            None, raw.global_deductions, entry.channel_text
        )
        if deductions:
            outcome_dict["deductions"] = deductions
            if commission_type == "percentage":
                outcome_dict["commission_type"] = "hybrid"

        # Preserve raw free-text conditions/footnotes (e.g. "if CPA is not
        # collected for individual name, 1.5% of OD Pay-out is deducted").
        # These are qualifying rules from notes/master sections that the
        # structured Deduction parser cannot model. Surface them verbatim
        # so downstream consumers (UI, export, manual review) keep the
        # information.
        other_conditions_parts = []
        entry_other = (entry.other_conditions or "").strip()
        if entry_other:
            other_conditions_parts.append(entry_other)
        global_other = (getattr(raw, "global_other_conditions", "") or "").strip()
        if global_other and global_other not in other_conditions_parts:
            other_conditions_parts.append(global_other)
        if other_conditions_parts:
            outcome_dict["other_conditions"] = " | ".join(other_conditions_parts)

        # Structured machine-ingestable representation of qualifying clauses
        # (applied_on overrides, conditional deductions, grid aliases). The LLM
        # emits these in Pass 3 alongside other_conditions; codegen path leaves
        # them empty. Validate each dict against QualifyingClause so malformed
        # entries don't pollute the CICS output.
        raw_clauses = getattr(entry, "qualifying_clauses", None) or []
        validated_clauses: List[QualifyingClause] = []
        for raw_clause in raw_clauses:
            try:
                validated_clauses.append(QualifyingClause.model_validate(raw_clause))
            except Exception as e:
                self.warnings.append(f"Dropping malformed qualifying_clause: {e}")
        if validated_clauses:
            outcome_dict["qualifying_clauses"] = validated_clauses

        return CommissionOutcome(**outcome_dict)

    def _detect_commission_type(self, value_text: str) -> Tuple[str, Dict]:
        """
        Detect commission type from value pattern.

        Patterns:
        - "12%" -> percentage
        - "Rs. 500" or "500 INR" -> flat
        - "0-25K: 10%, 25K-50K: 12%" -> tiered
        - "12% + 2% bonus" -> hybrid
        """
        # Handle empty commission value (placeholder case)
        if not value_text or value_text.strip() == "":
            self.warnings.append("Empty commission value - flagging for manual review")
            return "percentage", {
                "percentage_value": None,
                "calculation_notes": "Commission value not provided in source document - REQUIRES MANUAL REVIEW",
            }

        value = value_text.strip()
        value_lower = value.lower()

        # Check for tiered pattern (multiple rate:value pairs)
        tier_pattern = (
            r"(\d+[\d,kK]*)\s*[-–to]+\s*(\d+[\d,kK]*)\s*:\s*(\d+(?:\.\d+)?)\s*%"
        )
        tier_matches = re.findall(tier_pattern, value, re.IGNORECASE)
        if len(tier_matches) >= 2:
            tiers = self._parse_tiers(value)
            if tiers:
                return "tiered", {"tiers": tiers}

        # Check for hybrid (base + bonus)
        if re.search(r"\+\s*\d+(?:\.\d+)?%", value) or "bonus" in value_lower:
            base_match = re.search(r"^(\d+(?:\.\d+)?)\s*%", value)
            if base_match:
                return "hybrid", {"percentage_value": float(base_match.group(1))}

        # Check for flat amount
        flat_pattern = r"(?:Rs\.?|INR|₹)\s*(\d+(?:,\d{3})*(?:\.\d{2})?)"
        flat_match = re.search(flat_pattern, value)
        if flat_match:
            amount = float(flat_match.group(1).replace(",", ""))
            return "flat", {"flat_value": amount, "flat_currency": "INR"}

        # Default: percentage with % sign
        pct_pattern = r"(\d+(?:\.\d+)?)\s*%"
        pct_match = re.search(pct_pattern, value)
        if pct_match:
            pct_value = float(pct_match.group(1))
            if pct_value > 100:
                # An explicit '%' sign means percentage. Values > 100 are almost
                # always upstream bugs (e.g. fraction multiplied by 100 twice:
                # 0.65 -> 65 -> "6500%"). Flag for manual review instead of
                # silently flipping units to flat INR.
                self.warnings.append(
                    f"Commission value '{value}' exceeds 100% — likely an upstream "
                    f"unit error; flagging for manual review"
                )
                self.errors.append(
                    f"Commission value '{value}' > 100% requires manual review"
                )
                return "percentage", {
                    "percentage_value": None,
                    "calculation_notes": (
                        f"UNPARSED: {value} exceeds 100% - REQUIRES MANUAL REVIEW"
                    ),
                }
            return "percentage", {"percentage_value": pct_value}

        # Plain decimal/number without % sign (e.g., "0.26" means 26%, "26" means 26%)
        plain_number_pattern = r"^(\d*\.?\d+)$"
        plain_match = re.match(plain_number_pattern, value.strip())
        if plain_match:
            num_value = float(plain_match.group(1))
            # If value < 1, assume it's a decimal representation (0.26 = 26%)
            if num_value < 1:
                return "percentage", {"percentage_value": num_value * 100}
            # Plain number > 100 with no currency marker is ambiguous — could be
            # a flat INR amount or a unit error. Flag for manual review rather
            # than guessing a currency.
            elif num_value > 100:
                self.warnings.append(
                    f"Commission value '{value}' exceeds 100 with no currency or "
                    f"% marker — flagging for manual review"
                )
                self.errors.append(
                    f"Ambiguous commission value '{value}' requires manual review"
                )
                return "percentage", {
                    "percentage_value": None,
                    "calculation_notes": (
                        f"UNPARSED: {value} ambiguous (>100, no unit) - REQUIRES MANUAL REVIEW"
                    ),
                }
            else:
                return "percentage", {"percentage_value": num_value}

        # Could not parse - don't default to 0, use None
        self.warnings.append(f"Could not parse commission value: {value}")
        self.errors.append(
            f"Unparsable commission value will require manual review: {value}"
        )
        return "percentage", {
            "percentage_value": None,
            "calculation_notes": f"UNPARSED: {value} - REQUIRES MANUAL REVIEW",
        }

    def _normalize_amount_text(self, text: str) -> str:
        """Strip currency prefixes and normalise amount text for regex parsing."""
        return re.sub(r"(?i)rs\.?\s*|inr\s*|₹\s*", "", text).strip()

    def _parse_tiers(self, value_text: str) -> Optional[List[CommissionTier]]:
        """Parse tiered commission structure."""
        tiers = []
        value_text = self._normalize_amount_text(value_text)

        # Pattern: threshold: rate%
        tier_pattern = (
            r"(\d+(?:,\d{3})*)\s*[-–to]+\s*(\d+(?:,\d{3})*)\s*:\s*(\d+(?:\.\d+)?)\s*%"
        )
        matches = re.findall(tier_pattern, value_text, re.IGNORECASE)

        for match in matches:
            min_val = int(match[0].replace(",", ""))
            max_val = int(match[1].replace(",", ""))
            rate = float(match[2])

            tiers.append(
                CommissionTier(
                    threshold=NumericRange(min=min_val, max=max_val, unit="INR"),
                    value=rate,
                    value_type="percentage",
                )
            )

        # Check for "above X" pattern
        above_pattern = (
            r"(?:above|>)\s*(?:rs\.?\s*)?(\d+(?:,\d{3})*)\s*:\s*(\d+(?:\.\d+)?)\s*%"
        )
        above_match = re.search(above_pattern, value_text, re.IGNORECASE)
        if above_match:
            min_val = int(above_match.group(1).replace(",", "")) + 1
            rate = float(above_match.group(2))

            tiers.append(
                CommissionTier(
                    threshold=NumericRange(min=min_val, unit="INR"),
                    value=rate,
                    value_type="percentage",
                )
            )

        return tiers if tiers else None

    def _parse_bonuses(self, text: Optional[str]) -> Optional[List[Bonus]]:
        """Parse bonuses from text."""
        if not text:
            return None

        bonuses = []
        text_lower = text.lower()

        # Pattern: X% for Y
        bonus_pattern = (
            r"(?:additional\s+)?(\d+(?:\.\d+)?)\s*%\s*(?:for|on|with)?\s*(.+?)(?:,|$)"
        )
        matches = re.findall(bonus_pattern, text_lower)

        for match in matches:
            value = float(match[0])
            condition = match[1].strip()

            # Determine bonus type from condition
            bonus_type = "bonus"
            if "ncb" in condition or "no claim" in condition:
                bonus_type = "high-ncb-bonus"
            elif "volume" in condition or "policies" in condition:
                bonus_type = "volume-bonus"
            elif "target" in condition:
                bonus_type = "target-achievement"
            elif "early" in condition:
                bonus_type = "early-payment"

            bonuses.append(
                Bonus(
                    bonus_type=bonus_type,
                    value=value,
                    value_type="percentage",
                    conditions=condition if condition else None,
                )
            )

        return bonuses if bonuses else None

    def _parse_deductions(
        self,
        text: Optional[str],
        global_deductions: Optional[str],
        channel_text: Optional[str],
    ) -> Optional[List[Deduction]]:
        """Parse deductions from text."""
        deductions = []

        # Check if global deductions apply
        deduction_text = text or ""
        if global_deductions:
            # Check if global deduction applies to this channel
            if channel_text:
                channel_lower = channel_text.lower()
                global_lower = global_deductions.lower()
                if "online" in channel_lower and "online" in global_lower:
                    deduction_text = f"{deduction_text} {global_deductions}"
                elif "all" in global_lower:
                    deduction_text = f"{deduction_text} {global_deductions}"

        if not deduction_text.strip():
            return None

        text_lower = deduction_text.lower()

        # Pattern: X% fee/charge
        deduction_pattern = (
            r"(\d+(?:\.\d+)?)\s*%\s*(?:platform fee|processing|fee|charge)"
        )
        matches = re.findall(deduction_pattern, text_lower)

        for match in matches:
            value = float(match)
            deductions.append(
                Deduction(
                    deduction_type="platform-fee",
                    value=value,
                    value_type="percentage",
                    conditions="Applied to online transactions"
                    if "online" in text_lower
                    else None,
                )
            )

        # Pattern: Rs. X fee
        flat_deduction_pattern = r"(?:Rs\.?|INR|₹)\s*(\d+(?:,\d{3})*)\s*(?:fee|charge)"
        flat_matches = re.findall(flat_deduction_pattern, text_lower)

        for match in flat_matches:
            value = float(match.replace(",", ""))
            deductions.append(
                Deduction(
                    deduction_type="processing-fee",
                    value=value,
                    value_type="flat",
                    conditions=None,
                )
            )

        return deductions if deductions else None

    # ==================== PRIORITY COMPUTATION ====================

    def _compute_priorities(self, entries: List[RawCommissionEntry]) -> Dict[int, int]:
        """
        Compute priorities based on rule specificity.
        More specific rules get lower (higher priority) numbers.
        """
        specificity_scores = []

        for i, entry in enumerate(entries):
            score = 0

            # Count non-null condition fields
            condition_fields = [
                entry.customer_type_text,
                entry.channel_text,
                entry.coverage_type_text,
                entry.state_text,
                entry.other_conditions,
            ]

            score += sum(10 for f in condition_fields if f)

            # Each key in conditions dict adds specificity (same weight as a named field)
            entry_conditions = getattr(entry, "conditions", None) or {}
            score += len(entry_conditions) * 10

            # Custom conditions add specificity
            if entry.other_conditions:
                score += len(entry.other_conditions.split(",")) * 3

            specificity_scores.append((i, score))

        # Sort by score descending (most specific first)
        sorted_entries = sorted(specificity_scores, key=lambda x: -x[1])

        # Assign priorities in increments of 10
        priorities = {}
        for rank, (index, _) in enumerate(sorted_entries):
            priorities[index] = (rank + 1) * 10

        return priorities

    # ==================== SOURCE TRACES ====================

    def _generate_source_traces(
        self, entries: List[RawCommissionEntry], products: List[Product]
    ) -> List[SourceTrace]:
        """Generate source traces linking extracted data to source locations."""
        traces = []

        # Product-level traces
        for product in products:
            trace = SourceTrace(
                trace_id=self._next_trace_id(),
                entity_type="product",
                entity_id=product.product_code,
                source_location=None,
                quoted_text=f"Product: {product.product_name}",
                confidence=0.95,
            )
            traces.append(trace)

        # Rule-level traces
        for product in products:
            for rule in product.commission_rules:
                # Find matching entry
                for entry in entries:
                    if self._entry_matches_rule(entry, rule):
                        source_loc = None
                        if entry.source_section:
                            source_loc = SourceLocation(section=entry.source_section)

                        confidence = 0.9
                        if entry.confidence_note:
                            confidence = 0.7

                        traces.append(
                            SourceTrace(
                                trace_id=self._next_trace_id(),
                                entity_type="rule",
                                entity_id=rule.rule_id,
                                source_location=source_loc,
                                quoted_text=entry.quoted_text or entry.rule_text[:200],
                                confidence=confidence,
                                extraction_notes=entry.confidence_note,
                            )
                        )
                        break

        return traces

    def _entry_matches_rule(
        self, entry: RawCommissionEntry, rule: CommissionRule
    ) -> bool:
        """Check if an entry matches a rule (simple heuristic)."""
        if rule.rule_description and entry.rule_text:
            return (
                entry.rule_text[:50] in rule.rule_description
                or rule.rule_description[:50] in entry.rule_text
            )
        return False

    def _next_trace_id(self) -> str:
        """Generate next trace ID."""
        self._trace_counter += 1
        return f"TRACE_{self._trace_counter:03d}"

    # ==================== CHANGE REFERENCE ====================

    def _transform_change_reference(
        self, raw: RawExtraction
    ) -> Optional[ChangeReference]:
        """Transform change reference if inheritance detected."""
        if not raw.inherits_from_previous:
            return None

        return ChangeReference(
            is_inherited=True, inheritance_notes=raw.inheritance_note
        )

    # ==================== VALIDATION ====================

    def _generate_validation(self, raw: RawExtraction, products: List) -> Validation:
        """Generate validation block."""
        requires_review = raw.requires_review or False

        # Check for low confidence indicators
        if raw.ambiguities and len(raw.ambiguities) > 3:
            requires_review = True

        validation_notes = []

        # Check for null commission values (unparsable or missing)
        null_commission_count = 0
        for product in products:
            for rule in product.commission_rules:
                if (
                    rule.outcome.commission_type == "percentage"
                    and rule.outcome.percentage_value is None
                ):
                    null_commission_count += 1

        if null_commission_count > 0:
            requires_review = True
            validation_notes.append(
                f"Found {null_commission_count} rules with unparsable/missing commission values. "
                "Manual review required to obtain actual rates."
            )

        # Add transformation warnings
        for warning in self.warnings:
            validation_notes.append(f"Transform warning: {warning}")

        # Add ambiguities
        if raw.ambiguities:
            for amb in raw.ambiguities:
                validation_notes.append(f"Ambiguity: {amb}")

        # Add transformation errors
        for error in self.errors:
            validation_notes.append(f"Transform error: {error}")
            requires_review = True

        # Calculate confidence
        confidence = 0.9
        if self.warnings:
            confidence -= 0.05 * min(len(self.warnings), 4)
        if raw.ambiguities:
            confidence -= 0.1 * min(len(raw.ambiguities), 3)
        if self.errors:
            confidence -= 0.2

        confidence = max(confidence, 0.3)

        return Validation(
            overall_confidence=round(confidence, 2),
            requires_manual_review=requires_review,
            validation_notes=validation_notes if validation_notes else None,
        )

    # ==================== UTILITIES ====================

    def _slugify(self, text: str) -> str:
        """Convert text to slug format."""
        # Remove special characters and replace spaces with underscores
        slug = re.sub(r"[^\w\s-]", "", text)
        slug = re.sub(r"[\s-]+", "_", slug)
        return slug.strip("_")

    def _parse_date(self, text: str) -> Optional[str]:
        """
        Parse various date formats to YYYY-MM-DD.

        Handles:
        - "January 1, 2024"
        - "01/01/2024"
        - "1st Jan 2024"
        - "2024-01-01"
        """
        if not text:
            return None

        text = text.strip()

        # Already in ISO format
        if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            return text

        # Try common formats
        formats = [
            "%B %d, %Y",  # January 1, 2024
            "%b %d, %Y",  # Jan 1, 2024
            "%d %B %Y",  # 1 January 2024
            "%d %b %Y",  # 1 Jan 2024
            "%d/%m/%Y",  # 01/01/2024
            "%m/%d/%Y",  # 01/01/2024 (US format)
            "%d-%m-%Y",  # 01-01-2024
            "%Y/%m/%d",  # 2024/01/01
        ]

        # Remove ordinal suffixes (1st, 2nd, 3rd, 4th, etc.)
        cleaned = re.sub(r"(\d+)(?:st|nd|rd|th)", r"\1", text)

        for fmt in formats:
            try:
                parsed = datetime.strptime(cleaned, fmt)
                return parsed.strftime("%Y-%m-%d")
            except ValueError:
                continue

        # Try extracting components
        month_map = {
            "jan": 1,
            "january": 1,
            "feb": 2,
            "february": 2,
            "mar": 3,
            "march": 3,
            "apr": 4,
            "april": 4,
            "may": 5,
            "jun": 6,
            "june": 6,
            "jul": 7,
            "july": 7,
            "aug": 8,
            "august": 8,
            "sep": 9,
            "september": 9,
            "oct": 10,
            "october": 10,
            "nov": 11,
            "november": 11,
            "dec": 12,
            "december": 12,
        }

        text_lower = text.lower()
        for month_name, month_num in month_map.items():
            if month_name in text_lower:
                year_match = re.search(r"20\d{2}", text)
                day_match = re.search(r"\b(\d{1,2})\b", text)

                if year_match:
                    year = int(year_match.group())
                    day = int(day_match.group(1)) if day_match else 1
                    return f"{year}-{month_num:02d}-{day:02d}"

        return None


# Convenience function
def transform_raw_to_cics(
    raw: RawExtraction,
) -> Tuple[CICSDocument, List[str], List[str]]:
    """
    Transform RawExtraction to CICSDocument.

    Args:
        raw: The raw extraction from AI

    Returns:
        Tuple of (CICSDocument, warnings, errors)
    """
    transformer = RawToCICSTransformer()
    return transformer.transform(raw)
