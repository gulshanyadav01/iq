"""Mirror CV/PCV taxi rules into a duplicate Private Car / Taxi product.

Chola's own product hierarchy lists Taxi under BOTH buckets — the seed rows
``Chola,Car,,Taxi`` and ``Chola,CV,PCV,Taxi`` in
``master_data/data/chola_product_hierarchy.csv`` — so the same rate card is
bookable on the Private Car side as well as the commercial one. The transformer
only ever emits the commercial placement (``_apply_chola_pcv_subclass`` assigns
the ``taxi`` sub-class under a ``commercial-pcv`` product), so the Private Car
deal was silently missing from every export.

This module materializes it: for each ``commercial-pcv`` product carrying rules
whose ``vehicle_sub_class`` is exactly ``["taxi"]``, it appends a sibling product
with ``product_name="Private Car"`` / ``sub_category="Taxi"`` holding deep copies
of those rules. The commercial rows are left untouched — this is a duplication,
not a re-classification.

Four deliberate boundaries:

* **Only the exact ``taxi`` slug qualifies.** ``big-taxi``,
  ``greater-than-six-seater``, ``maxi-cab``, ``kaali-peeli-taxi``,
  ``auto-rickshaw`` and ``e-rickshaw`` all stay commercial-only. A 9-seat Maxi
  Cab has no Private Car counterpart, and the hierarchy master does not list one.
* **The clone is a NEW product, never merged into an existing Private Car
  product.** ``rule_overlap_checker`` scopes overlap detection by
  ``product_code``, so folding PCV rates into a genuine Private Car product
  manufactures overlap findings against real PC rules that share every dimension
  but the rate. ``normalize_rule_priorities`` also renumbers per product.
* **It runs AFTER ``materialize_cpa_split``.** Cloning post-split means each
  source rule already carries its explicit With/Without-CPA variant and the copy
  is numerically identical to its commercial twin. Cloning first would let
  ``_split_rule`` resolve ``applied_on`` differently for a Private Car product
  (OD vs Net basis) and silently produce a divergent deduction on one side.
* **The clone carries NO seating band.** The client does not bifurcate Taxi by
  seating capacity on the Private Car side — there is exactly one Private Car /
  Taxi deal and it exists only for the up-to-5-seat taxi, so a populated
  ``SeatingCapacityStartRange`` / ``EndRange`` cell asserts a scoping they never
  book. The COMMERCIAL twin keeps its band untouched: there the band is the sole
  discriminator between Normal Taxi (0-5), Maxi Cab (6-12) and Big Taxi (13-16),
  and it has already shipped inverted once (see ``_apply_chola_pcv_subclass``).
  Nulling a dimension is not free, which is why ``_seating_collision`` gates it:
  ``rule_overlap_checker._constraint_state`` returns ``_NEITHER`` for a dimension
  null on BOTH sides, so two rules separated only by seat band would become
  ``_conditions_identical`` — a CRITICAL ``complete_overlap``, and a CRITICAL
  makes ``cics_cache.should_cache`` refuse to pin the whole document. When such a
  pair exists the product's clones keep their seating and a warning names them.

Bucket consequence, and it is intended: the export derives the RTO column from
``master_data.services.resolver.product_bucket(product_name)``, so the mirrored
rows fill from the CAR zone table while their commercial twins fill from CV.

``CINS_PCV_TAXI_MIRROR=false`` disables the whole thing without a redeploy. Note
that flipping it off after a document has been cached makes the
``private car|taxi`` segment key disappear, which trips the cross-run regression
guard (``CINS_REGRESSION_GUARD``) with a warning and skips the cache pin.

The function is idempotent: a product whose ``product_code`` already carries the
mirror suffix is never mirrored again, and a source product whose mirror already
exists is skipped.
"""

import copy
import json
import logging
import os

logger = logging.getLogger("cins.pcv_taxi_mirror")

# Suffix appended to the source product_code / rule_id / trace_id. Kept inside
# the schema's ``^[A-Za-z0-9_-]+$`` pattern for product_code and trace_id.
MIRROR_SUFFIX = "_PCTAXI"

# The product_subproduct pin the transformer sets for a CV/PCV product
# (_SEGMENT_TO_SUBPRODUCT in raw_to_cics_transformer). Matching on the pin rather
# than the display name is deliberate: the pin is what every downstream
# validator keys off.
_SOURCE_SUBPRODUCT = "commercial-pcv"

# The only qualifying sub-class, as the lowercase slug the transformer emits.
_TAXI_SLUG = "taxi"

# Mirror product identity. product_subproduct="car" maps to the master's "Car"
# bucket in CholaProductHierarchyValidator._bucket_for, which is what makes the
# clone validate against the ``Chola,Car,,Taxi`` seed row rather than CV/PCV.
_MIRROR_PRODUCT_NAME = "Private Car"
_MIRROR_SUB_CATEGORY = "Taxi"
_MIRROR_SUBPRODUCT = "car"
_MIRROR_VEHICLE_TYPE = "car-taxi"

# Cleared on every clone: Private Car / Taxi is one deal, not a seat ladder.
# See the fourth boundary in the module docstring — the commercial source rule
# keeps these, only the copy loses them.
_MIRROR_SEATING_KEYS = (
    "seating_capacity_start_range",
    "seating_capacity_end_range",
)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _is_taxi_rule(rule) -> bool:
    """True when the rule's sub-class is exactly ``taxi`` and nothing else.

    ``_bifurcate_subclass_buckets`` has already fanned multi-sub-class rules out
    into singletons by the time this runs, so the set comparison is the normal
    shape rather than a narrowing.
    """
    if not isinstance(rule, dict):
        return False
    conditions = rule.get("conditions") or {}
    sub_classes = conditions.get("vehicle_sub_class")
    if not isinstance(sub_classes, list) or not sub_classes:
        return False
    return {str(s).strip().lower() for s in sub_classes} == {_TAXI_SLUG}


def _is_source_product(product) -> bool:
    if not isinstance(product, dict):
        return False
    if product.get("product_subproduct") != _SOURCE_SUBPRODUCT:
        return False
    return bool(product.get("product_code"))


def _seating_band(rule):
    conditions = rule.get("conditions") or {}
    return tuple(conditions.get(key) for key in _MIRROR_SEATING_KEYS)


def _seating_free_signature(rule):
    """The rule's grid slot with the seat band removed.

    Two rules sharing this signature are, once the band is nulled, identical to
    ``rule_overlap_checker._conditions_identical``. Deliberately CONDITIONS
    ONLY: the checker compares conditions and ignores the payout, so folding the
    outcome in here would make the guard blind to the very case it exists for —
    two bands at DIFFERENT rates, which is the expensive collision, not the
    harmless one.
    """
    conditions = {
        key: value
        for key, value in (rule.get("conditions") or {}).items()
        if key not in _MIRROR_SEATING_KEYS
    }
    return json.dumps(conditions, sort_keys=True, default=str)


def _seating_collision(rules):
    """Rule-id pairs that only the seat band tells apart, or ``None``.

    A collision means blanking would manufacture a CRITICAL ``complete_overlap``
    and wall the document out of the cache, so the caller keeps the band instead.
    """
    by_signature = {}
    for rule in rules:
        by_signature.setdefault(_seating_free_signature(rule), []).append(rule)
    for group in by_signature.values():
        if len({_seating_band(rule) for rule in group}) > 1:
            return [rule.get("rule_id") for rule in group]
    return None


def _mirror_product(product, rules, clear_seating=True):
    """Build the Private Car / Taxi twin of ``product`` for ``rules``."""
    clone = {
        "product_code": f"{product['product_code']}{MIRROR_SUFFIX}",
        "product_name": _MIRROR_PRODUCT_NAME,
        "product_category": product.get("product_category"),
        "sub_category": _MIRROR_SUB_CATEGORY,
        "product_subproduct": _MIRROR_SUBPRODUCT,
        "vehicle_type": _MIRROR_VEHICLE_TYPE,
        "commission_rules": [],
    }
    # Carry the deal-level metadata across verbatim — it describes the deal
    # terms (deal_type, zone, file_type, ...), not the vehicle classification,
    # so the mirrored deal books on the same terms as its commercial twin.
    for field in (
        "deal_type",
        "deal_business_type",
        "portfolio_business",
        "approval_type",
        "file_type",
        "zone",
        "product_mode",
        "bu_parameters",
        "decline_deal",
    ):
        if field in product:
            clone[field] = product.get(field)

    for rule in rules:
        cloned_rule = copy.deepcopy(rule)
        rule_id = cloned_rule.get("rule_id")
        if rule_id:
            cloned_rule["rule_id"] = f"{rule_id}{MIRROR_SUFFIX}"
        if clear_seating and isinstance(cloned_rule.get("conditions"), dict):
            for key in _MIRROR_SEATING_KEYS:
                cloned_rule["conditions"][key] = None
        clone["commission_rules"].append(cloned_rule)
    return clone


def _mirror_product_traces(cics_doc, source_code, mirror_code):
    """Copy the source product's traces onto the mirror's product_code.

    Without this the schema validator raises a LOW "Products without source
    traces" finding for every mirrored product. Cosmetic, but noise.
    """
    traces = cics_doc.get("source_traces")
    if not isinstance(traces, list):
        return
    added = []
    for trace in traces:
        if not isinstance(trace, dict):
            continue
        if (
            trace.get("entity_type") != "product"
            or trace.get("entity_id") != source_code
        ):
            continue
        cloned = copy.deepcopy(trace)
        cloned["entity_id"] = mirror_code
        trace_id = cloned.get("trace_id")
        if trace_id:
            cloned["trace_id"] = f"{trace_id}{MIRROR_SUFFIX}"
        added.append(cloned)
    traces.extend(added)


def materialize_pcv_taxi_mirror(cics_doc):
    """Append a Private Car / Taxi twin for every CV/PCV taxi rule, in place.

    Mutates and returns ``cics_doc``. Idempotent, and a no-op when
    ``CINS_PCV_TAXI_MIRROR`` is disabled or the document holds no qualifying
    rules.
    """
    if not isinstance(cics_doc, dict):
        return cics_doc
    if not _env_flag("CINS_PCV_TAXI_MIRROR", True):
        return cics_doc

    products = cics_doc.get("products")
    if not isinstance(products, list) or not products:
        return cics_doc

    existing_codes = {
        p.get("product_code")
        for p in products
        if isinstance(p, dict) and p.get("product_code")
    }

    mirrors = []
    for product in products:
        if not _is_source_product(product):
            continue
        mirror_code = f"{product['product_code']}{MIRROR_SUFFIX}"
        if mirror_code in existing_codes:
            continue  # already mirrored on an earlier pass
        taxi_rules = [
            rule
            for rule in (product.get("commission_rules") or [])
            if _is_taxi_rule(rule)
        ]
        if not taxi_rules:
            continue
        collision = _seating_collision(taxi_rules)
        if collision:
            logger.warning(
                "pcv_taxi_mirror: %s has taxi rules separated only by seating "
                "capacity (%s) — keeping the seat band on the mirrored rules "
                "rather than manufacturing a complete_overlap",
                product["product_code"],
                ", ".join(str(rule_id) for rule_id in collision),
            )
        mirrors.append(
            (
                product,
                _mirror_product(product, taxi_rules, clear_seating=not collision),
            )
        )
        existing_codes.add(mirror_code)

    for source, mirror in mirrors:
        products.append(mirror)
        _mirror_product_traces(cics_doc, source["product_code"], mirror["product_code"])
        logger.info(
            "pcv_taxi_mirror: mirrored %d taxi rule(s) from %s into %s (%s / %s)",
            len(mirror["commission_rules"]),
            source["product_code"],
            mirror["product_code"],
            _MIRROR_PRODUCT_NAME,
            _MIRROR_SUB_CATEGORY,
        )
    return cics_doc
