"""Resolve free-text values extracted by the LLM parser to canonical master IDs.

Loads each master table once at construction time (~7 small SELECTs, <300 rows
total). Cheap relative to the seconds-to-minutes the parser spends on LLM
calls, and avoids stale-cache problems if the seeder runs mid-worker-lifetime.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Optional

from master_data.models import (
    CarrierMaster,
    CarrierProductHierarchyMaster,
    CarrierRTOZoneMaster,
    FuelTypeMaster,
    InsurerMaster,
    PlanTypeMaster,
    PolicyTypeMaster,
    RTOMaster,
    VehicleClassMaster,
    VehicleMaster,
    VehicleSubClassMaster,
)
from master_data.services.canonical import normalize_insurer_name

# Export product bucket -> the RTOMaster zone column that marks the RTO active
# for that product. An RTO is listed for a product only when its zone is non-null.
_PRODUCT_ZONE_ATTR = {"CAR": "car_zone", "CV": "cv_zone", "TW": "tw_zone"}

# "PrivateCar" -> "Private Car". Mirrors the split in
# cins.validators.chola_product_hierarchy_validator, which needs it for the
# same reason: the carrier masters spell some values without the spacing.
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")

# A wheel count that rules out the two-wheeler bucket even when the name says
# "wheeler" ("Four Wheeler" is a car, "Three Wheeler" is a commercial carrier).
_NON_TW_WHEELS_RE = re.compile(r"(?<![a-z])(four|4w|4|three|3w|3)(?![a-z])")

# Product-name tokens that identify each bucket.
#
# GROUP order is load-bearing: the first group with any matching token wins, and
# the commercial group runs first because "Commercial Vehicle" carries neither a
# car nor a wheeler token while a bus/goods name might carry both.
#
# Token order WITHIN a group is presentational only — one `any(...)` consumes
# the whole tuple, so nothing shadows anything. The doubled-C spellings are
# listed before the bare "gcv"/"pcv" purely to read as the same
# specific-before-general ladder as RawToCICSTransformer._prod_token_segment,
# which callers compare this table against.
#
# Chola writes its CV tables as GCCV / PCCV / MSV, and a product_name that
# escaped canonicalization keeps that spelling; without these tokens the bucket
# resolved to None and the export's state-derived RTO fill silently emitted an
# empty cell even for rules carrying a perfectly good state.
_BUCKET_TOKENS = (
    (
        "CV",
        (
            "commercial",
            "cv",
            "scv",
            "lcv",
            "hcv",
            "gccv",
            "pccv",
            "gcv",
            "pcv",
            "msv",
            "misc-d",
            "misc d",
            "miscd",
            "goods",
            "goods carrying",
            "passenger carrying",
            "bus",
            "tractor",
            "trailer",
            "rickshaw",
            # Three-wheelers are passenger/goods carriers, NOT two-wheelers.
            # The CV group runs first, so these win before "wheeler" can claim
            # them for TW -- which is what the bare-substring form used to do,
            # filling their RTO cell with two-wheeler-active RTOs.
            "three wheeler",
            "3 wheeler",
            "3w",
        ),
    ),
    ("TW", ("two", "2w", "tw", "wheeler")),
    # "four wheeler" is a Private Car spelling (PRODUCT_NAME_MAPPINGS agrees).
    # Without it the TW four-guard skipped TW and nothing else matched, so a
    # perfectly ordinary product name resolved to None.
    ("CAR", ("car", "private", "taxi", "four wheeler", "4 wheeler", "4w")),
)

# One compiled alternation per bucket, built once at import. product_bucket runs
# per rule on the export path (5k+ rules on a real Chola document), so building
# ~18 rf-strings per call was pure waste.
#
# `(?:es|s)?` allows the plural — "es" first so "Buses" matches on bus+es rather
# than failing on bus+e. The lookarounds keep it from reopening the substring
# hole the word boundary closed.
#
# The boundary is `[a-z]`, NOT `\w`: `\w` counts digits, so "GCV3T" / "PCV3W" /
# "TW1+1" -- ordinary carrier grid spellings the old substring form matched
# fine -- resolved to None. A None bucket blanks the RTO cell, drops the
# product out of the export's claimed-RTO pre-pass, and (since this change)
# raises a validator finding. Digits must read as a boundary, not as a letter.
_BUCKET_PATTERNS = tuple(
    (
        bucket,
        re.compile(
            "|".join(rf"(?<![a-z]){re.escape(t)}(?:es|s)?(?![a-z])" for t in tokens)
        ),
    )
    for bucket, tokens in _BUCKET_TOKENS
)


def product_bucket(name) -> Optional[str]:
    """Normalise an export product name to the {CAR,CV,TW} RTO bucket.

    Returns ``None`` when the name matches nothing — callers treat that as
    "cannot resolve RTOs for this product". Shared by the commission export
    (which uses it to fill the RTO column from the rule's states) and
    StateRtoPresenceValidator (which asserts that fill produces a non-empty
    cell), so the two can never disagree about what a product name means.

    Tokens are matched on WORD BOUNDARIES, not as bare substrings. The bare
    substring form this replaced silently mis-bucketed any product whose name
    merely contained a token — "Marine Cargo" read as CAR ("car" inside
    "cargo"), "Business Package" as CV ("bus" inside "business") — and a wrong
    bucket is worse than no bucket: it fills the export's RTO cell with another
    product's active RTOs instead of leaving it blank.

    Two normalizations keep that boundary from over-tightening, because a name
    the boundary rejects now resolves to None, which both blanks the export's
    RTO cell AND raises a HIGH in StateRtoPresenceValidator that blocks the
    cache pin:

    * **camelCase is split** — the carrier masters really do spell it
      ``PrivateCar`` (chola_product_hierarchy.csv), which has no word boundary
      before "Car" at all. Same cheap split the Chola hierarchy validator runs.
    * **a plural suffix is allowed** — "Buses", "Cars", "PCVs" are the same
      product as "Bus", "Car", "PCV". ``es`` is listed before ``s`` so "Buses"
      matches on ``bus``+``es`` rather than failing.

    Neither reopens the substring hole: "Cargo" still needs "car" to end the
    word (a bare "car", "cars" or "cares" — "cargo" is none of those), and
    "Business" still isn't "bus"/"buses".
    """
    n = _CAMEL_SPLIT_RE.sub(" ", name or "").lower()
    for bucket, pattern in _BUCKET_PATTERNS:
        if not pattern.search(n):
            continue
        # A wheel count other than two disqualifies TW even though the name
        # carries "wheeler". Three-wheelers are already claimed by the CV group
        # above; this is the backstop for spellings that group misses.
        if bucket == "TW" and _NON_TW_WHEELS_RE.search(n):
            continue
        return bucket
    return None


@dataclass
class Resolution:
    id: Optional[int]
    canonical: str
    matched: bool
    # Set when a value resolved but a lower-priority signal disagreed with the
    # winner (see resolve_product_id_multi). Callers should flag do_verification.
    conflict: bool = False

    @property
    def id_str(self) -> str:
        return str(self.id) if self.id is not None else ""


# Product Id (186/187/188) → Subclass-master product code (Car/TW/CV).
PRODUCT_ID_TO_CODE = {186: "Car", 187: "TW", 188: "CV"}
# Inverse: subclass-master product code (Car/TW/CV) → Product Id.
CODE_TO_PRODUCT_ID = {code: pid for pid, code in PRODUCT_ID_TO_CODE.items()}

# Carrier only confirms Commercial Vehicle (188). "PRIVATE" is ambiguous
# (Car vs TW), so it contributes no product signal (decision D3:A).
CARRIER_PRODUCT_FALLBACK = {"public": 188, "commercial": 188}

# Fallback for VehicleClass enum values that the parser produces but that
# aren't present as rows in the Vehicle Master sheet.
VEHICLE_CLASS_FALLBACK = {
    "gcv": (188, "CV"),
    "pcv": (188, "CV"),
    "cv": (188, "CV"),
    "commercial vehicle": (188, "CV"),
    "bus": (188, "CV"),
    "misc d": (188, "CV"),
    "miscd": (188, "CV"),
    "two wheeler": (187, "TW"),
    "tw": (187, "TW"),
    "private car": (186, "Car"),
    "car": (186, "Car"),
}


def _normalize(value: str) -> str:
    return (value or "").strip().casefold()


class MasterDataResolver:
    """In-memory cache of all master tables for fast in-process lookups."""

    def __init__(self) -> None:
        self._reload()

    def _reload(self) -> None:
        # Small enums — keyed by normalized name.
        self._fuel = {
            _normalize(r.name): (r.fuel_id, r.name)
            for r in FuelTypeMaster.objects.all()
        }
        self._policy = {
            _normalize(r.name): (r.policy_id, r.name)
            for r in PolicyTypeMaster.objects.all()
        }
        self._carrier = {
            _normalize(r.name): (r.carrier_id, r.name)
            for r in CarrierMaster.objects.all()
        }
        # Keyed by normalized name, plus a reverse map by numeric class id so the
        # VehicleClass can be derived from a resolved subclass's vehicle_class_id.
        self._vehicle_class = {}
        self._vehicle_class_by_id: dict[int, tuple[int, str]] = {}
        for r in VehicleClassMaster.objects.all():
            self._vehicle_class[_normalize(r.name)] = (r.vehicle_class_id, r.name)
            try:
                cid = int(r.vehicle_class_id)  # skips the string "Blank"
            except (TypeError, ValueError):
                continue
            self._vehicle_class_by_id[cid] = (cid, r.name)
        self._vehicle = {
            _normalize(r.vehicle_type): (r.vehicle_id, r.vehicle_type)
            for r in VehicleMaster.objects.all()
        }
        # Plan: keyed by (normalized plan_type, is_zero_dep).
        self._plan = {
            (_normalize(r.plan_type), r.is_zero_dep): (r.plan_type_id, r.plan_type)
            for r in PlanTypeMaster.objects.all()
        }
        # Insurer: list per product_id, plus a normalized-name → record map per product.
        self._insurer_by_product: dict[int, list[InsurerMaster]] = {}
        self._insurer_norm: dict[int, dict[str, InsurerMaster]] = {}
        for r in InsurerMaster.objects.all():
            self._insurer_by_product.setdefault(r.product_id, []).append(r)
            self._insurer_norm.setdefault(r.product_id, {})[r.name_normalized] = r
        # Subclass: list per product code (CV/TW/Car).
        # Also build an inverted index (normalized subclass text -> {product_id})
        # so a subclass name can *infer* the product. Keyed off the reliable
        # `product` string column, NOT `product_id`: the Master Mapper sheet
        # stamps the two Car subclass rows with ProductId 188 (CV) by mistake, so
        # the integer column would misclassify every private car as commercial.
        self._subclass_by_product: dict[str, list[VehicleSubClassMaster]] = {}
        self._subclass_product_index: dict[str, set[int]] = {}
        # (product, pb_vehicle_subclass_id) -> vehicle_class_id, so a resolved
        # subclass can supply the authoritative VehicleClass (e.g. E Rickshaw -> PCV).
        self._subclass_class_id: dict[tuple[str, int], int] = {}
        for r in VehicleSubClassMaster.objects.all():
            self._subclass_by_product.setdefault(r.product, []).append(r)
            if r.vehicle_class_id is not None:
                self._subclass_class_id[(r.product, int(r.pb_vehicle_subclass_id))] = (
                    int(r.vehicle_class_id)
                )
            pid = CODE_TO_PRODUCT_ID.get(r.product)
            if pid is None:
                continue
            for key in (_normalize(r.subclass_name), _normalize(r.canonical_name)):
                if key:
                    self._subclass_product_index.setdefault(key, set()).add(pid)
        # RTO: per state slug, the display value + per-product active flags.
        # Built once so rtos_for_states is a pure in-memory filter.
        self._rto_by_state: dict[str, list[dict]] = {}
        for r in RTOMaster.objects.all():
            self._rto_by_state.setdefault(r.state_slug, []).append(
                {
                    "value": r.value,
                    "code": r.code,
                    "car_zone": r.car_zone,
                    "cv_zone": r.cv_zone,
                    "tw_zone": r.tw_zone,
                }
            )

    # --------------------------- small enums ---------------------------

    def resolve_fuel_type(self, value: str) -> Resolution:
        return self._exact_lookup(value, self._fuel)

    def resolve_policy_type(self, value: str) -> Resolution:
        return self._exact_lookup(value, self._policy)

    def resolve_carrier(self, value: str) -> Resolution:
        return self._exact_lookup(value, self._carrier)

    def resolve_vehicle_class(self, value: str) -> Resolution:
        return self._exact_lookup(value, self._vehicle_class)

    def resolve_vehicle_class_by_id(self, class_id: Optional[int]) -> Resolution:
        """Resolve a VehicleClass from its numeric master id (e.g. 2 -> PCV)."""
        if class_id is None:
            return Resolution(id=None, canonical="", matched=False)
        hit = self._vehicle_class_by_id.get(int(class_id))
        if hit is None:
            return Resolution(id=None, canonical="", matched=False)
        cid, name = hit
        return Resolution(id=cid, canonical=name, matched=True)

    def vehicle_class_id_for_subclass(
        self, product_code: str, subclass_id: int
    ) -> Optional[int]:
        """The master vehicle_class_id for a resolved subclass, or None."""
        return self._subclass_class_id.get((product_code, int(subclass_id)))

    def product_code_for_id(self, product_id: Optional[int]) -> Optional[str]:
        """Product Id (186/187/188) -> subclass-master product code (Car/TW/CV)."""
        return PRODUCT_ID_TO_CODE.get(product_id) if product_id is not None else None

    def resolve_product_id(self, vehicle_class_or_product: str) -> Resolution:
        """Map an extracted product/vehicle-class string to a Product Id (186/187/188)."""
        hit = self._exact_lookup(vehicle_class_or_product, self._vehicle)
        if hit.matched:
            return hit
        # Fallback for parser-only enum values (GCV/BUS/MISC D).
        norm = _normalize(vehicle_class_or_product)
        fallback = VEHICLE_CLASS_FALLBACK.get(norm)
        if fallback:
            pid, _code = fallback
            return Resolution(id=pid, canonical=vehicle_class_or_product, matched=True)
        return Resolution(id=None, canonical="", matched=False)

    def _product_from_subclass(self, text: str) -> Optional[int]:
        """Infer a Product Id from a subclass name, or None if unknown/ambiguous."""
        pids = self._subclass_product_index.get(_normalize(text))
        # Only trust a subclass that maps to exactly one product. Names shared
        # across products (e.g. "Taxi" -> Car+CV) give no confident signal.
        return next(iter(pids)) if pids and len(pids) == 1 else None

    def _product_from_carrier(self, text: str) -> Optional[int]:
        """Carrier confirms CV only; PRIVATE (Car vs TW) yields no signal."""
        return CARRIER_PRODUCT_FALLBACK.get(_normalize(text))

    def resolve_product_id_multi(
        self,
        *,
        product_text: str = "",
        vehicle_class_text: str = "",
        vehicle_subclass_text: str = "",
        carrier_text: str = "",
    ) -> Resolution:
        """Resolve Product Id from four signals in priority order.

        Fall-through: product -> vehicle class -> vehicle subclass -> carrier.
        The first signal that yields a product wins. If a lower-priority signal
        disagrees with the winner, the returned Resolution has ``conflict=True``
        so the caller can flag the row for verification. When no signal resolves,
        returns an unmatched Resolution (never guesses).
        """
        ordered = [
            self.resolve_product_id(product_text).id,  # 1. product
            self.resolve_product_id(vehicle_class_text).id,  # 2. vehicle class
            self._product_from_subclass(vehicle_subclass_text),  # 3. vehicle subclass
            self._product_from_carrier(carrier_text),  # 4. carrier (CV-only)
        ]
        present = [pid for pid in ordered if pid is not None]
        if not present:
            return Resolution(id=None, canonical="", matched=False)
        winner = present[0]
        conflict = any(pid != winner for pid in present[1:])
        return Resolution(
            id=winner,
            canonical=PRODUCT_ID_TO_CODE.get(winner, ""),
            matched=True,
            conflict=conflict,
        )

    def resolve_product_code(self, vehicle_class_or_product: str) -> Optional[str]:
        """Return the Subclass-master product code (Car/TW/CV) for an extracted value."""
        norm = _normalize(vehicle_class_or_product)
        if norm in VEHICLE_CLASS_FALLBACK:
            return VEHICLE_CLASS_FALLBACK[norm][1]
        # Try via Vehicle Master → Product Id → code map.
        hit = self._vehicle.get(norm)
        if hit:
            try:
                return PRODUCT_ID_TO_CODE.get(int(hit[0]))
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _exact_lookup(value: str, table: dict) -> Resolution:
        norm = _normalize(value)
        if not norm:
            return Resolution(id=None, canonical="", matched=False)
        hit = table.get(norm)
        if hit is None:
            return Resolution(id=None, canonical="", matched=False)
        rid, canonical = hit
        try:
            return Resolution(id=int(rid), canonical=canonical, matched=True)
        except (TypeError, ValueError):
            # vehicle_class_id can legitimately be the string "Blank".
            return Resolution(id=None, canonical=canonical, matched=True)

    # ------------------------------ rto ------------------------------

    def rtos_for_states(self, slugs, product, exclude_codes=None) -> list:
        """Return the product-active RTO display values for the given states.

        ``slugs`` is an iterable of canonical state slugs (e.g.
        ``["andhra-pradesh"]``); ``product`` is the export bucket CAR/CV/TW. An
        RTO is included only when its per-product zone is non-null (the master
        marks it active for that product). Returns a sorted, de-duplicated list.
        Returns ``[]`` for an unknown product, empty slugs, or states with no
        product-active RTOs — callers treat ``[]`` as "leave the cell empty".

        ``exclude_codes`` (optional) drops RTOs whose ``code`` is in the set —
        used by the export's state-derived fill to subtract RTOs a sibling
        rule already claims explicitly (e.g. a Chola "UP" fill excluding the
        codes an "UP- East" zone rule claims), matched by ``code`` rather than
        the display ``value`` string since callers hold bare codes.
        """
        zone_attr = _PRODUCT_ZONE_ATTR.get(str(product or "").strip().upper())
        if zone_attr is None or not slugs:
            return []
        excl = set(exclude_codes or ())
        out: set[str] = set()
        for slug in slugs:
            for rto in self._rto_by_state.get(slug, []):
                if rto.get(zone_attr) is not None and rto["code"] not in excl:
                    out.add(rto["value"])
        return sorted(out)

    # ------------------------------ plan ------------------------------

    def resolve_plan_type(self, value: str, is_zero_dep: bool) -> Resolution:
        norm_value = _normalize(value)
        if not norm_value:
            return Resolution(id=None, canonical="", matched=False)

        # Map common parser outputs to the canonical plan_type stored in DB.
        synonym_map = {
            "comprehensive": "comprehensive package",
            "comp": "comprehensive package",
            "third party": "third party",
            "tp only": "third party",
            "tp": "third party",
            "saod": "saod",
        }
        canonical_key = synonym_map.get(norm_value, norm_value)

        hit = self._plan.get((canonical_key, is_zero_dep))
        if hit is None and is_zero_dep:
            # Fallback: requested ZD variant doesn't exist — try non-ZD row.
            hit = self._plan.get((canonical_key, False))
        if hit is None:
            return Resolution(id=None, canonical="", matched=False)

        plan_id, name = hit
        canonical_label = (
            f"{name} + ZD"
            if is_zero_dep and (canonical_key, True) in self._plan
            else name
        )
        return Resolution(id=int(plan_id), canonical=canonical_label, matched=True)

    # ----------------------------- insurer -----------------------------

    def resolve_insurer(self, name: str, product_id: Optional[int]) -> Resolution:
        if not name:
            return Resolution(id=None, canonical="", matched=False)

        # If product_id is unknown we can't safely pick between rows that share
        # a name across products. Caller should mark do_verification=TRUE.
        if product_id is None:
            return Resolution(id=None, canonical="", matched=False)

        candidates_norm = self._insurer_norm.get(product_id, {})
        candidates = self._insurer_by_product.get(product_id, [])
        if not candidates:
            return Resolution(id=None, canonical="", matched=False)

        normalized = normalize_insurer_name(name)

        # Step 1: exact normalized match.
        hit = candidates_norm.get(normalized)
        if hit is not None:
            return Resolution(id=int(hit.insurer_id), canonical=hit.name, matched=True)

        # Step 2: fuzzy match on normalized names.
        keys = list(candidates_norm.keys())
        if not keys or not normalized:
            return Resolution(id=None, canonical="", matched=False)
        matches = difflib.get_close_matches(normalized, keys, n=1, cutoff=0.7)
        if matches:
            hit = candidates_norm[matches[0]]
            return Resolution(id=int(hit.insurer_id), canonical=hit.name, matched=True)

        return Resolution(id=None, canonical="", matched=False)

    # ---------------------------- subclass ----------------------------

    def resolve_vehicle_subclass(self, text: str, product: str) -> Resolution:
        """Map extracted subclass text to a canonical subclass within a product (CV/TW/Car)."""
        if not text or not product:
            return Resolution(id=None, canonical="", matched=False)

        candidates = self._subclass_by_product.get(product, [])
        if not candidates:
            return Resolution(id=None, canonical="", matched=False)

        text_upper = text.strip().upper()

        # 1. Exact (case-insensitive on canonical_name).
        for c in candidates:
            if c.canonical_name.upper() == text_upper:
                return Resolution(
                    id=int(c.pb_vehicle_subclass_id),
                    canonical=c.canonical_name,
                    matched=True,
                )

        # 2. Exact on the raw subclass_name from the Excel.
        for c in candidates:
            if c.subclass_name.upper() == text_upper:
                return Resolution(
                    id=int(c.pb_vehicle_subclass_id),
                    canonical=c.canonical_name,
                    matched=True,
                )

        # 3. Substring match either way.
        for c in candidates:
            cn = c.canonical_name.upper()
            if cn in text_upper or text_upper in cn:
                return Resolution(
                    id=int(c.pb_vehicle_subclass_id),
                    canonical=c.canonical_name,
                    matched=True,
                )

        # 4. difflib fuzzy on canonical names.
        names = [c.canonical_name for c in candidates]
        matches = difflib.get_close_matches(text, names, n=1, cutoff=0.5)
        if matches:
            for c in candidates:
                if c.canonical_name == matches[0]:
                    return Resolution(
                        id=int(c.pb_vehicle_subclass_id),
                        canonical=c.canonical_name,
                        matched=True,
                    )

        # 5. Product-specific safety net for Private Car (body styles like SEDAN/SUV).
        if product == "Car":
            if "TAXI" in text_upper or "CAB" in text_upper:
                return Resolution(id=2, canonical="Taxi", matched=True)
            return Resolution(id=1, canonical="Private Car", matched=True)

        return Resolution(id=None, canonical="", matched=False)


# --------------------------- carrier-scoped masters ---------------------------
#
# Deliberately module-level functions, NOT methods on MasterDataResolver: they
# run a scoped query (filter(carrier=...)) rather than the eager `_reload()`
# that loads every table for every one of the ~20 carriers. Callers (the
# Chola-gated transformer path, the product-hierarchy validator) only ever
# need one carrier's rows, so a broadened `_reload` would be wasted work on
# every non-Chola document.


def carrier_zone_expansions(carrier: str) -> dict[str, dict[str, list[str]]]:
    """UW-zone slug -> {"rto": [...], "state": [...]} for a carrier.

    Sorted/deduped. A zone spanning multiple states (e.g. Chola's "ncr",
    which mixes Haryana and Uttar Pradesh RTO codes) naturally aggregates
    every state its RTO codes belong to.
    """
    if not carrier:
        return {}
    zones: dict[str, dict[str, set]] = {}
    rows = CarrierRTOZoneMaster.objects.filter(carrier=carrier).values(
        "zone", "rto_code", "state_slug"
    )
    for row in rows:
        bucket = zones.setdefault(row["zone"], {"rto": set(), "state": set()})
        bucket["rto"].add(row["rto_code"])
        bucket["state"].add(row["state_slug"])
    return {
        zone: {"rto": sorted(v["rto"]), "state": sorted(v["state"])}
        for zone, v in zones.items()
    }


def carrier_product_hierarchy(carrier: str) -> list[dict]:
    """(product, sub_product, vehicle_subclass, canonical_subclass) rows for
    a carrier, via a scoped query."""
    if not carrier:
        return []
    return list(
        CarrierProductHierarchyMaster.objects.filter(carrier=carrier).values(
            "product", "sub_product", "vehicle_subclass", "canonical_subclass"
        )
    )
