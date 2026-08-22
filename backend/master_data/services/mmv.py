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

# Tokens carrying no identity: **corporate-form boilerplate only**.
#
# This list was once much wider — it also dropped the industry words
# ("auto", "motors", "industries", "enterprises", "india"), which folded
# "TATA MOTORS LTD" onto "Tata". Measured against a dump of 2,237 real Chola
# deals, that was wrong on 93% of them: those words are not boilerplate in the
# Indian commercial-vehicle market, they are the only thing distinguishing
# genuinely unrelated firms. "OM INDUSTRIES" and "OM MOTORS" both collapsed to
# "om", "STAR INDUSTRIES" onto "STAR AUTO", "NIKHIL ENTERPRISES" onto
# "NIKHIL FURNITURE" — each rewriting a correct manufacturer into a different
# real company.
#
# Narrowing to corporate-form words takes the wrong-rewrite count on that same
# dump to ZERO. The cost is that "TATA MOTORS LTD" and "TATA" no longer fold
# together; both are left as the grid spelled them, which is the conservative
# outcome this module is built around. Do not re-add industry words here
# without re-running the dump sweep.
_NOISE_TOKENS = frozenset(
    {
        "and",
        "co",
        "company",
        "corporation",
        "limited",
        "ltd",
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
                   "models_any": {model_slug: canonical_model},
                   "makes_own":  {make_slug: canonical_make}}}

    ``models`` is nested by make so a scoped lookup is one dict access;
    ``models_any`` is the precomputed make-less lookup, holding only model
    slugs with a single spelling across the whole bucket. Both exist so
    ``match_model`` does no per-call rebuilding — treat this shape as internal
    and never hand-assemble it in a test, build it through here.

    ``makes_own`` holds only the makes THIS product's book actually lists, and
    exists for ENUMERATION, not matching. ``makes`` is a carrier-wide alias on
    purpose (see below) — right for resolving a spelling, wrong for answering
    "which makes does this product cover", where it would return all 1,109 the
    carrier has ever written against a Car book that lists 53.

    One scoped query. Returns ``{}`` on any failure (DB unavailable, table
    unmigrated, master unseeded) so every consumer degrades to today's
    behaviour rather than raising.
    """
    try:
        from master_data.services.resolver import carrier_vehicle_mmv

        rows = carrier_vehicle_mmv(carrier)
    except Exception:
        return {}

    # Collect every source spelling per slug FIRST, then reduce (see
    # ``_finalize``). Reducing as we go — the old ``_keep_preferred`` — could
    # not tell "the same name written twice" from "two different companies
    # that happen to share a slug", and silently picked one of the two.
    make_spellings: Dict[str, set] = {}
    product_make_spellings: Dict[str, Dict[str, set]] = {}
    model_spellings: Dict[str, Dict[str, set]] = {}
    products: set = set()

    for row in rows:
        product = row.get("product") or ""
        make_slug = row.get("make_slug") or ""
        if not product or not make_slug:
            continue
        products.add(product)
        make_spellings.setdefault(make_slug, set()).add(row.get("make") or "")
        product_make_spellings.setdefault(product, {}).setdefault(make_slug, set()).add(
            row.get("make") or ""
        )
        model_slug = row.get("model_slug") or ""
        if model_slug:
            # Nested by product then make so a scoped lookup is one dict access
            # rather than a filtering pass over every model (the CV book has
            # 5,215 of them, and this runs per rule per document).
            by_make = model_spellings.setdefault(product, {}).setdefault(make_slug, {})
            by_make.setdefault(model_slug, set()).add(row.get("model") or "")

    index: Dict[str, dict] = {}
    # Makes are matched CARRIER-WIDE, not per product. A make is a brand, and
    # real Chola Car/Taxi deals enumerate commercial brands (Ashok Leyland,
    # Bharat Benz, SML Isuzu) that the 614-row Car bucket does not hold — under
    # product scoping half of the makes in a production dump went unmatched for
    # no better reason than which book listed them. The per-product ``makes``
    # map is kept as an alias so the shape stays stable for callers.
    index["_makes"] = _finalize(make_spellings)

    for product in products:
        by_make = model_spellings.get(product, {})
        bucket = {
            "makes": index["_makes"],
            "makes_own": _finalize(product_make_spellings.get(product, {})),
            "models": {ms: _finalize(models) for ms, models in by_make.items()},
        }
        # Precompute the make-less lookup ONCE per bucket. It depends only on
        # the master, never on the rule being matched, so rebuilding it per
        # call was pure waste — measured at ~1ms a call against ~0.1ms for the
        # scoped path.
        pooled: Dict[str, set] = {}
        for models in by_make.values():
            for model_slug, names in models.items():
                pooled.setdefault(model_slug, set()).update(names)
        bucket["models_any"] = _finalize(pooled)
        index[product] = bucket

    return index


def _finalize(spellings: Dict[str, set]) -> Dict[str, str]:
    """Reduce ``{slug: {source spellings}}`` to ``{slug: canonical}``.

    A slug whose spellings differ by more than case, whitespace and
    punctuation names more than one thing and is DROPPED — it is not
    resolvable, and picking one would rewrite a rule to name a company the grid
    never mentioned. This is the guard that stops "OM INDUSTRIES" becoming
    "OM MOTORS" when the master happens to hold both.

    Punctuation is ignored in that comparison but case and word order are not:
    "B-M-W" and "B M W" are one manufacturer written twice, whereas
    "OM INDUSTRIES" and "OM MOTORS" are two companies that merely share a slug
    once the corporate forms come off.

    Where the spellings agree, the shortest wins (alphabetical tie-break) so
    the choice is deterministic across reseeds.
    """
    out: Dict[str, str] = {}
    for slug, names in spellings.items():
        found = [name for name in names if name]
        if not slug or not found:
            continue
        if len({_ident(name) for name in found}) > 1:
            continue  # ambiguous — say nothing
        out[slug] = min(found, key=lambda name: (len(name), name))
    return out


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


def _ident(text: str) -> str:
    """Case-, punctuation- and whitespace-insensitive form of a spelling.

    Used ONLY to decide whether two source spellings name the same thing (see
    ``_finalize``). Unlike ``mmv_slug`` it keeps every token, so two names that
    differ by a real word stay different.
    """
    return " ".join(_PUNCT_RE.sub(" ", str(text or "").lower()).split())


def _norm(text: str) -> str:
    """Case- and whitespace-insensitive form, for comparing two spellings."""
    return " ".join(str(text or "").split()).casefold()


def _resolve(candidates: Dict[str, str], text: str) -> Optional[str]:
    """Exact-slug lookup for ``text`` over ``{slug: canonical}``.

    **One rung, exact match, nothing else.** Two looser rungs were tried and
    both were removed for the same reason — each invented a vehicle the grid
    never mentioned:

    - a fuzzy/edit-distance rung, at any cutoff loose enough to catch a real
      typo, matched ``SCOOTER`` (a sub-class word the extractor is known to
      mis-land in ``vehicle_make``) onto the manufacturer ``SCOOTERS INDIA``;
    - a unique-leading-prefix rung ("maruti" -> the sole "maruti suzuki")
      looked safe until it was measured against 2,237 real Chola deals, where
      it fired on 93% of them and produced ``EICHER`` -> ``Eicher Polaris``,
      ``HYUNDAI`` -> ``HYUNDAI CONSTRUCTION CO`` and ``SUPREME`` ->
      ``AUTO SUPREME INDUSTRIES``. A complete, correct manufacturer name is
      not a prefix of a longer one just because the master lists only the
      longer one.

    Canonicalization also NEVER lengthens. Because the slug drops corporate
    forms, a master holding only ``SML ISUZU LTD`` would otherwise rewrite the
    grid's correct ``SML ISUZU`` into the longer boilerplate spelling. Dropping
    ``PVT LTD`` is a tidy-up; adding it is noise.
    """
    if not candidates:
        return None
    slug = mmv_slug(text)
    if not slug:
        return None

    canonical = candidates.get(slug)
    if not canonical:
        return None  # absent, or dropped as ambiguous — do not guess
    if len(_norm(canonical)) > len(_norm(text)):
        return None
    return canonical


def match_make(index: Dict[str, dict], product: str, text: str) -> Optional[str]:
    """Canonical make for ``text``, or None.

    ``product`` is accepted for signature stability and to keep the call sites
    symmetrical with ``match_model``, but makes are matched CARRIER-WIDE — see
    ``build_mmv_index`` for why product scoping was wrong here.
    """
    if not index:
        return None
    return _resolve(index.get("_makes", {}), text)


# A grid writes one manufacturer word where the master lists a whole corporate
# family, and only the carrier knows which subsidiaries belong to it. Chola's
# CV grids price "TMM" — Tata / Maruti / Mahindra — as one bucket; the master
# has no bare "Mahindra" or "TATA" row at all, so an exact match returns
# nothing and an exclusion cannot be expanded.
#
# These groupings are READ OFF the client's own production deals, not inferred.
# In Chola_Dump_AMJJ.xlsx the "all makes except TMM" deal carries 1,700 makes
# against the full book's 1,713, and the 13 it drops are exactly the 13 below —
# the same 13 that appear as the TMM *include* list on the sibling deals.
#
# It has to be an explicit list, because no mechanical rule reproduces it:
# TATA MOTORS LTD is in the family but **TATA HITACHI is NOT** (the carrier
# treats the construction JV as a separate brand and leaves it in the
# complement), while SHUBHMARUTI — not a word-boundary match at all — IS. A
# prefix or substring rule would get both wrong, in the direction that silently
# changes who gets paid.
#
# Used ONLY to enumerate a family for export. ``match_make`` stays exact and
# single-valued: it answers "what is this spelling", which has one answer,
# where this answers "what does this word cover", which has thirteen.
CARRIER_MAKE_FAMILIES: Dict[str, Dict[str, tuple]] = {
    "Chola": {
        "mahindra": (
            "MAHINDRA AND MAHINDRA",
            "MAHINDRA CONSTRUCTION EQUIPMENTS",
            "MAHINDRA ELECTRIC MOBILITY LIMITED",
            "MAHINDRA GUJARAT TRACTOR LTD",
            "MAHINDRA INTL",
            "MAHINDRA NAVISTAR",
        ),
        "maruti": (
            "MARUTI",
            "MARUTI BHIWANI E AUTOMOBILES",
            "MARUTI NANDAN BUSINESS PV",
            "MARUTI SUZUKI",
            "SHREE MARUTI NANDAN",
            "SHUBHMARUTI",
        ),
        "tata": ("TATA MOTORS LTD",),
    },
}


def expand_make_family(
    index: Dict[str, dict], carrier: str, product: str, text: str
) -> tuple:
    """Every canonical make in ``product``'s book that ``text``'s family covers.

    Returns ``()`` when the carrier has no family table, the token names no
    family, or the family's members are all absent from this product's book —
    callers treat an empty result as "this token did not resolve" and must not
    proceed as though nothing needed excluding.

    Members absent from the book are simply skipped: the book is what we are
    subtracting from, so a make it never listed cannot be removed from it.
    """
    families = CARRIER_MAKE_FAMILIES.get(carrier or "")
    if not families or not index:
        return ()
    members = families.get(mmv_slug(text))
    if not members:
        return ()
    book = (index.get(product) or {}).get("makes_own") or {}
    return tuple(book[slug] for slug in (mmv_slug(m) for m in members) if slug in book)


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

    if make_canonical:
        return _resolve(bucket["models"].get(mmv_slug(make_canonical), {}), text)

    # No make to scope by: only a model name unambiguous across the whole
    # bucket can be canonicalized. Precomputed in build_mmv_index.
    return _resolve(bucket.get("models_any", {}), text)
