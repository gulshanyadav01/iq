"""Make / Model / Variant slugging and matching.

ONE implementation, imported by BOTH the transformer (which rewrites a rule's
``vehicle_make`` / ``vehicle_model`` onto the master's spelling) and
``CholaMMVValidator`` (which flags what the transformer could not match). If
the two sides could drift, the validator would flag text the transformer would
have fixed — the same reason ``CholaProductHierarchyValidator`` reuses the
transformer's ``VEHICLE_SUB_CLASS_MAPPINGS`` rather than building a second
canonicalization scheme.

The matcher is deliberately conservative: **ambiguity resolves to ``None``,
never to a guess.** A wrong canonicalization silently rewrites a rule to name a
vehicle the grid never mentioned, which is strictly worse than leaving the
source text alone and letting the validator say so.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

# Tokens carrying no identity — corporate-form boilerplate and the industry
# words nearly every Indian manufacturer name repeats. Dropping them is what
# folds "MAHINDRA AND MAHINDRA" onto "Mahindra" and "AMO MOBILITY" onto "amo
# mobility" (kept: "mobility" is not in the list, so distinct makes stay
# distinct). Order-independent — applied per token, not as a suffix strip, so
# "TATA MOTORS LTD" and "Tata" land on the same slug.
_NOISE_TOKENS = frozenset(
    {
        "and",
        "auto",
        "automobiles",
        "automotive",
        "automotives",
        "co",
        "company",
        "corporation",
        "enterprises",
        "india",
        "industries",
        "limited",
        "ltd",
        "motor",
        "motors",
        "private",
        "pvt",
    }
)

_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def mmv_slug(text: str) -> str:
    """Fold a make/model/variant string to its match key.

    Casefold, strip punctuation, drop noise tokens, collapse a token repeated
    adjacently or otherwise ("MAHINDRA AND MAHINDRA" -> "mahindra"). Returns
    "" when nothing identifying survives, which every caller treats as "no
    match possible" rather than as a key.
    """
    if not text:
        return ""
    lowered = _PUNCT_RE.sub(" ", str(text).lower())
    seen: list[str] = []
    for token in lowered.split():
        if token in _NOISE_TOKENS:
            continue
        if token not in seen:
            seen.append(token)
    return " ".join(seen)


def build_mmv_index(carrier: str) -> Dict[str, dict]:
    """Build the per-product match index for one carrier.

    Shape::

        {"_makes": {make_slug: canonical_make},          # carrier-wide
         product: {"makes":      {make_slug: canonical_make},
                   "models":     {make_slug: {model_slug: canonical_model}},
                   "models_any": {model_slug: canonical_model}}}

    ``models`` is nested by make so a scoped lookup is one dict access;
    ``models_any`` is the precomputed make-less lookup, holding only model
    slugs with a single spelling across the whole bucket. Both exist so
    ``match_model`` does no per-call rebuilding — treat this shape as internal
    and never hand-assemble it in a test, build it through here.

    One scoped query. Returns ``{}`` on any failure (DB unavailable, table
    unmigrated, master unseeded) so every consumer degrades to today's
    behaviour rather than raising.
    """
    try:
        from master_data.services.resolver import carrier_vehicle_mmv

        rows = carrier_vehicle_mmv(carrier)
    except Exception:
        return {}

    index: Dict[str, dict] = {}
    # Carrier-wide preferred make spelling, keyed by slug. Matching stays
    # product-scoped (a CV-only make must not match a Private Car rule), but
    # the spelling written back is resolved across products, because a make is
    # a BRAND: the commercial book only ever writes "TATA MOTORS LTD" while
    # the car book writes "TATA", and emitting a different string for the same
    # manufacturer depending on the grid it came from is precisely the
    # inconsistency this master exists to remove.
    preferred_makes: Dict[str, str] = {}
    index["_makes"] = preferred_makes

    for row in rows:
        product = row.get("product") or ""
        make_slug = row.get("make_slug") or ""
        if not product or not make_slug:
            continue
        bucket = index.setdefault(product, {"makes": {}, "models": {}})
        _keep_preferred(preferred_makes, make_slug, row.get("make") or "")
        _keep_preferred(bucket["makes"], make_slug, row.get("make") or "")
        model_slug = row.get("model_slug") or ""
        if model_slug:
            # Nested by make so a scoped lookup is one dict access rather than
            # a filtering pass over every model in the bucket (the CV book has
            # 5,215 of them, and this runs per rule per document).
            by_make = bucket["models"].setdefault(make_slug, {})
            _keep_preferred(by_make, model_slug, row.get("model") or "")

    # Precompute the make-less lookup ONCE per bucket. It depends only on the
    # master, never on the rule being matched, so rebuilding it per call was
    # pure waste — measured at ~1ms a call against ~0.1ms for the scoped path.
    # A model slug carrying more than one distinct spelling across makes is
    # dropped: without a make to scope by it is not resolvable, and guessing
    # would name a vehicle the grid never mentioned.
    for key, bucket in index.items():
        if key == "_makes":
            continue
        spellings: Dict[str, set] = {}
        for by_make in bucket["models"].values():
            for model_slug, canonical in by_make.items():
                spellings.setdefault(model_slug, set()).add(canonical)
        bucket["models_any"] = {
            slug: next(iter(names))
            for slug, names in spellings.items()
            if len(names) == 1
        }
    return index


def scope_make(matched_makes) -> Optional[str]:
    """The make a model lookup is scoped by, given a rule's matched makes.

    ONE definition, called by both the transformer and ``CholaMMVValidator``.
    They previously computed this separately and did NOT agree: the
    transformer scoped by ``match_make(...) or item``, so an UNMATCHED make
    still became the scope (matching nothing, leaving the model verbatim),
    while the validator counted only matched makes and fell through to the
    unscoped lookup — where the model could resolve and therefore go
    unflagged. Net effect: a model neither canonicalized nor reported. The
    two must key off the same value, so they share this.

    Exactly one resolved make scopes the lookup; zero or several means the
    model must be unambiguous across the whole product bucket to match.
    """
    found = [make for make in (matched_makes or ()) if make]
    return found[0] if len(found) == 1 else None


def _keep_preferred(target: dict, key, spelling: str) -> None:
    """Keep the SHORTEST source spelling for a slug (ties: alphabetical).

    One slug routinely has several source spellings — the CV book writes
    ``TATA MOTORS LTD`` where the car book writes ``TATA``, and ``MAHINDRA``
    alongside ``MAHINDRA AND MAHINDRA``. Canonicalizing onto whichever row
    happened to load first would make the same brand come out differently per
    product, which is exactly the inconsistency this master exists to remove.
    Shortest wins because the extra tokens are always corporate boilerplate,
    never identity; alphabetical tie-break keeps the choice deterministic
    across reseeds.
    """
    if not spelling:
        return
    current = target.get(key)
    if current is None or (len(spelling), spelling) < (len(current), current):
        target[key] = spelling


def _resolve(candidates: Dict[str, str], slug: str) -> Optional[str]:
    """Run the match ladder for ``slug`` over ``{slug: canonical}``.

    1. exact slug hit;
    2. unique leading-token-prefix hit ("maruti" -> the sole "maruti suzuki") —
       rejected when more than one master entry starts with it, so a partial
       make shared by two manufacturers stays unmatched.

    There is deliberately NO fuzzy/edit-distance rung. One was tried and
    removed: at any cutoff loose enough to catch a real typo, ``SCOOTER`` — a
    sub-class word the extractor is known to mis-land in ``vehicle_make`` —
    matched the manufacturer ``SCOOTERS INDIA``, which would rewrite a bogus
    rule into one naming a real vehicle and hide it from the validator that
    exists to catch exactly that. An unmatched spelling is reported, not
    guessed at.
    """
    if not slug or not candidates:
        return None

    exact = candidates.get(slug)
    if exact:
        return exact

    prefix = f"{slug} "
    hits = {key: value for key, value in candidates.items() if key.startswith(prefix)}
    if len(hits) == 1:
        return next(iter(hits.values()))
    return None  # absent, or genuinely ambiguous — do not guess


def match_make(index: Dict[str, dict], product: str, text: str) -> Optional[str]:
    """Canonical make for ``text`` within ``product``, or None.

    Matched inside the product bucket, then spelled from the carrier-wide
    preferred map (see ``build_mmv_index``).
    """
    bucket = index.get(product) if index else None
    if not bucket:
        return None
    matched = _resolve(bucket["makes"], mmv_slug(text))
    if matched is None:
        return None
    return index.get("_makes", {}).get(mmv_slug(matched), matched)


def match_model(
    index: Dict[str, dict],
    product: str,
    make_canonical: Optional[str],
    text: str,
) -> Optional[str]:
    """Canonical model for ``text`` within ``product``, or None.

    Scoped to ``make_canonical`` when the rule resolved a make — that scoping
    is what keeps a short model name from colliding across the CV book's 839
    manufacturers. With no make, the model must be unique across the whole
    product bucket to match at all.
    """
    bucket = index.get(product) if index else None
    if not bucket:
        return None

    slug = mmv_slug(text)
    if not slug:
        return None

    if make_canonical:
        return _resolve(bucket["models"].get(mmv_slug(make_canonical), {}), slug)

    # No make to scope by: only a model name unambiguous across the whole
    # bucket can be canonicalized. Precomputed in build_mmv_index.
    return _resolve(bucket.get("models_any", {}), slug)
