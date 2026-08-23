"""Carrier vehicle sub-class vocabulary, in the rules' own slug space.

``CarrierProductHierarchyMaster`` stores the carrier's body list as the source
spells it (``Agricultural Tractor``, ``Fork Lift Trucks``, ``PrivateCar``);
``conditions.vehicle_sub_class`` carries SLUGS produced by
``VEHICLE_SUB_CLASS_MAPPINGS``. Anything comparing or merging the two has to
cross that gap, and there must be exactly ONE crossing — the same discipline
the MMV matcher follows. Two normalization schemes would let one call site
reject a spelling the other would have folded.

Both callers live on opposite sides of the pipeline:

- ``RawToCICSTransformer._bifurcate_subclass_buckets`` widens the merged MISC-D
  rule to the carrier's full body list (``CINS_MISCD_SUBCLASS_MERGE``);
- ``CholaProductHierarchyValidator`` checks emitted slugs against it.

Deliberately NOT memoized here. The seed changes between tests and between
carrier uploads, and a module-level cache would serve one test's master to the
next. Callers that need it once per document memoize on their own instance
(``self._canonical_subclass_cache``) — which is also where the DB read belongs,
lazily: ``RawToCICSTransformer`` is constructed as a pure object in unit tests
and by the validator itself, so it must never query at ``__init__`` time.

Reads ``master_data.services.resolver.carrier_product_hierarchy`` at
module level, never through ``MasterDataResolver`` — that class reloads every
table it owns eagerly.

Fails open: an unseeded, unreadable or unmigrated master yields ``[]``, and
every caller treats that as "say nothing", never as "the carrier has no
bodies".
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional

# "PrivateCar" -> "Private Car". The master spells some values without the
# mapping dict's spacing; a cheap camelCase split closes that gap.
#
# The source's OWN typos ("Excavaters", "Buldozer", "Tranist Mixer",
# "Refridgerated Van") are handled as aliases in VEHICLE_SUB_CLASS_MAPPINGS, NOT
# left to fail. This comment used to claim leaving them unmapped was "correct
# and self-consistent" because a grid rule spelling the same typo would fail
# through the identical dict — which was wrong in both directions. The rule side
# reaches the correct slug through a DIFFERENT alias (the grid cell says "Earth
# Movers", not "Excavaters"), so the master was the only side failing: it
# narrowed the MISC-D merge's widened body list AND made
# CholaProductHierarchyValidator reject the slug the rule correctly produced.
# Fix a non-normalizing master value with an alias, never by editing the CSV —
# the master is stored carrier-verbatim on purpose.
CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")

# Chola's own "no body class recorded" row. It is a real master value, but it
# names no vehicle, so it can never belong in a widened body list.
_SENTINEL_SLUGS = frozenset({"not-available"})


def carrier_subclass_rows(carrier: str) -> List[dict]:
    """Raw hierarchy rows for a carrier, or ``[]`` if the master is unreadable."""
    if not carrier:
        return []
    try:
        from master_data.services.resolver import carrier_product_hierarchy

        return carrier_product_hierarchy(carrier) or []
    except Exception:
        return []


def canonical_subclass_slugs(
    carrier: str,
    product: Optional[str],
    sub_product: Optional[str],
    normalize: Callable[[str], Optional[List[str]]],
    rows: Optional[List[dict]] = None,
    drop_sentinels: bool = True,
) -> List[str]:
    """Sorted slugs for one ``(carrier, product, sub_product)`` bucket.

    ``normalize`` is injected rather than imported so this module stays free of
    ``RawToCICSTransformer`` (which imports nothing from here, but whose
    validator does — the cycle is real). Callers pass their own
    ``_normalize_list_field`` bound to ``VEHICLE_SUB_CLASS_MAPPINGS``.

    ``rows`` lets a caller that already fetched the carrier's rows reuse them
    across several buckets instead of re-querying per bucket.

    ``drop_sentinels`` is the difference between the two callers: a validator
    asking "is this a value the master knows?" must accept ``not-available``,
    while a transformer widening a rule to every body of a sub-product must
    not — the sentinel names no vehicle.
    """
    if not product or not sub_product:
        return []
    if rows is None:
        rows = carrier_subclass_rows(carrier)

    want_product = str(product).strip().lower()
    want_sub_product = str(sub_product).strip().lower()

    slugs = set()
    for row in rows:
        if str(row.get("product") or "").strip().lower() != want_product:
            continue
        if str(row.get("sub_product") or "").strip().lower() != want_sub_product:
            continue
        raw = row.get("vehicle_subclass") or ""
        if not raw:
            continue
        try:
            produced = normalize(CAMEL_SPLIT_RE.sub(" ", raw))
        except Exception:
            produced = None
        for slug in produced or ():
            if not slug:
                continue
            if drop_sentinels and slug in _SENTINEL_SLUGS:
                continue
            slugs.add(slug)
    return sorted(slugs)
