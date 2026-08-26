"""Deterministic output-cache layer for commission extraction.

Reuse a fully-extracted, gate-passing result when the same document is re-run,
so a re-extraction does not re-roll the codegen dice (and cannot regress a good
result into a segment-dropping one). See ``models.cics_output_cache`` for the why.

The cache key binds every input that can legitimately change the output:

    content_hash          — SHA256 of the source content (all attachments + body)
    indexer_version       — INDEXER_VERSION (markdown shape can change between versions)
    prompt_version_hash   — SHA256 of the whole prompt corpus (base + overlays)
    llm_provider / model  — a different model produces different code
    CICS_PIPELINE_VERSION  — bump when the deterministic transformer / schema /
                            validator / executor changes, so a bugfix is never
                            served stale from cache (Eng D1, the T1 ship-blocker)

Bump ``CICS_PIPELINE_VERSION`` whenever you change:
  - raw_to_cics_transformer.py (rates/NCB/GVW/excavator/electric fixes, overlays)
  - schema/cics_model.py or the JSON schema
  - validators that gate output
  - code_gen_executor.py sandbox/validation semantics
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("cics_cache")


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# ── Deterministic pipeline version ───────────────────────────────────────────
# Bump on ANY change to the deterministic post-codegen pipeline (transformer,
# schema, gating validators, sandbox executor). This invalidates every cached
# output so a fixed transformer is never served a stale pre-fix result.
# 1.0.1 — Chola vehicle_sub_class made mandatory (transformer backfill +
#         HIGH validator finding), and CRITICAL/HIGH validator issues now
#         block the cache write.
# 1.1.0 — NCB became a numeric percentage band (ncb_start_range /
#         ncb_end_range) instead of a boolean hardcoded to the 25% split.
#         New schema fields, a new label parser, band-aware tier pairing, and
#         new overlap/validator wiring — every one of those changes the output
#         for an NCB-bearing document.
#         NOTE: that release ALSO edited four files under cins/prompts/, which
#         changes prompt_version_hash and therefore invalidates the cache for
#         EVERY carrier on its own, independently of this constant. See the
#         cache-key list in the module docstring above.
#         1.2.0 — state expansion learned Chola's "rest of <state>" zone tokens
#         (ROM / ROTN) and the NE group; product_bucket extracted + word-bounded;
#         StateRtoPresenceValidator added. Without this bump a cached Chola
#         document keeps serving the blank State/RTO columns the fix removes.
# 1.3.0 — two extraction-correctness fixes. (a) Per-sheet filename keys are now
#         collision-free: four Chola June sheets shared a 60-char prefix, so all
#         four truncated to one 50-char key, shared one pass-content file, and —
#         being processed concurrently — the last writer won. Three sheets were
#         extracted from the Two Wheeler grid and the document's entire CV book
#         (1,552 rules across GCV/PCV/Buses/MISC-D) became 2,633 phantom TW
#         rules. No extraction content round-trips through the filesystem any
#         more — neither the per-sheet path nor the single-file email path, whose
#         temp file also sat in a shared PROVIDER directory. Remaining writes are
#         debug artifacts with no reader. (b) An empty codegen result is a
#         legitimate outcome, rather than a 3-retry failure that sets
#         requires_review and blocks the cache pin forever, when BOTH the pass-3
#         markdown and the index-time rate_cell_count agree the sheet holds no
#         rate rows.
#         Cached documents must re-extract or they keep serving the swapped
#         segments and stay unpinnable.
#   1.4.0 — A two-wheeler product exports SubProduct "Two Wheeler" instead of
#         the raw grid token ("2w"). Cached documents must re-extract or they
#         keep serving the old value.
#   1.5.0 — CV/PCV rules whose vehicle_sub_class is exactly "taxi" are mirrored
#         into a duplicate Private Car / Taxi product (pcv_taxi_mirror). Cached
#         documents must re-extract or they keep serving exports with the
#         Private Car deal missing.
#   1.6.0 — Chola metro/remainder geography. "ROK" now maps to karnataka (the
#         "TN/ROK" row previously dropped its Karnataka half entirely), the
#         misspelling "Karnatka" resolves, and a metro label the carrier's own
#         zone master does not cover ("KA (Bangalore)") falls back to the
#         global RTO master's locality index for its explicit RTO set. A
#         two-token zone label also matches with its tokens reversed, so the
#         Two-Wheeler sheet's "East UP" resolves the same zone the Private Car
#         / CV headers spell "UP- East" instead of falling back to a full
#         Uttar Pradesh fill. Cached documents must re-extract or they keep
#         serving the missing state, the over-broad all-of-Karnataka RTO fill
#         and the un-complemented UP rows.
#         NOTE: a 1.7.0 was briefly prepared for Chola make/model
#         canonicalization and then withdrawn before release. The fold now
#         ships behind CINS_CHOLA_MMV, which defaults to FALSE, so the
#         transformer's output is byte-identical to 1.6.0 and no cache entry
#         needs to be invalidated. **Enabling that flag in any environment is a
#         pipeline-version change**: the fold rewrites vehicle_make /
#         vehicle_model and their _exclude twins, and the _exclude fields are
#         part of _DEDUPE_SCALAR_FIELDS, so two groups that differed only by
#         spelling would merge. Bump to 1.7.0 at that point, not before.
# 1.7.0 - A body word is re-filed when it names a different vehicle in its
#         sub-product. Chola writes the MISC-D agricultural tractor as the
#         segment slug "1_TRAC"; the shared VEHICLE_SUB_CLASS_MAPPINGS table
#         maps the bare token "trac" to "tractor", which the carrier's own
#         hierarchy master files under GCV as a GOODS body — a different
#         vehicle at a different payout. Reconciled against prod deal 799822
#         (MISC-D / Third Party / Bihar), which reads "Agricultural Tractor".
#         Cached documents must re-extract or they keep serving the GCV body
#         on MISC-D rules. See _SUBPRODUCT_SUBCLASS_OVERRIDES.
#         NOTE: this is the same 1.7.0 the CINS_CHOLA_MMV note above reserves.
#         That flag still defaults to FALSE and is unaffected — enabling it
#         needs no further bump now that this one has shipped.
# 1.8.0 - Third-party rules are now CPA-split. A TP/PA rule carrying the Chola
#         "CPA not collected" footnote used to be exempted from cpa_split
#         entirely and exported ONE row labelled the merged "With CPA,Without
#         CPA" at the undeducted rate — overstating the without-CPA payout by
#         the full deduction on every one of them. It now emits a With-CPA row
#         at the grid rate and a Without-CPA row at the grid rate minus the
#         footnote's deduction, taken off the TP column in percentage POINTS.
#         The negative case differs from od/net on purpose: where those clamp at
#         0, a TP deduction larger than the payout (a 1.5-point deduction on a
#         ~0.3% ACT rate) is DECLINED — the row still bifurcates but both
#         variants carry the undeducted rate, because a 0% ACT payout is a
#         number the carrier never published. The count is logged at WARNING.
#         CoverageIntegrityValidator's check B ("third-party rule wrongly
#         CPA-split", HIGH) is removed: it now describes correct output, and a
#         HIGH blocks should_cache, which would make every affected Chola
#         document permanently un-cacheable. Third-party rule counts roughly
#         DOUBLE on affected products (this is the incident doc dbe5b4ea's
#         24 -> 48, now the intended count). Cached documents must re-extract or
#         they keep serving the single merged-label row.
# 1.9.0 - GCV rules no longer carry a vehicle_sub_class at all. A goods-carrying
#         grid prices by weight band and publishes no body type — its SUBCLASS
#         column holds the weight ladder ("2_UPTO_3.5T") — so the transformer
#         used to stamp the "not-available" sentinel, which the export then
#         WIDENED into all 15 seeded GCV bodies (Truck, Tipper, Tanker, …). The
#         cell is now blank. The strip is absolute (a body resolved from a slug,
#         e.g. auto-rickshaw off "1_GCCV_3W", goes too) and carrier-agnostic, and
#         runs LAST in _transform_product — after _bifurcate_gcv_wheels, whose
#         4W-only routing for wheel-less special-purpose bodies reads the field.
#         CholaProductHierarchyValidator's mandatory-presence HIGH is carved out
#         for GCV: it would otherwise fire on every GCV rule and, being a HIGH,
#         wall the carrier's whole CV book out of this cache. Rule counts may
#         drop slightly where subclass-only clones now collapse. Rollback is
#         CINS_GCV_NO_SUBCLASS=false, which is itself a version-relevant change.
#         Cached documents must re-extract or they keep serving the 15-body cell.
# 1.10.0 - Two client corrections to the commercial-vehicle export, both behind
#         default-ON flags.
#         (a) CINS_RENEWAL_IMPLIES_ROLLOVER: a CV rule whose business_type
#         contains "renewal" now also carries "rollover", so BusinessType exports
#         "renewal,rollover". A rollover IS a renewal that changes carrier, and
#         no CV grid in the corpus prices the two apart — a rollover quote used
#         to find no matching deal at all. Scoped to the four commercial
#         sub-products and carrier-agnostic; Private Car / Two Wheeler are
#         excluded because their grids DO sometimes price rollover separately.
#         (b) CINS_MISCD_SUBCLASS_MERGE: MISC-D keeps Agricultural Tractor and
#         Harvester as their own rules and collapses every OTHER body of a source
#         rule into ONE rule carrying a multi-value vehicle_sub_class, widened to
#         the carrier's full MISC-D body list from CarrierProductHierarchyMaster.
#         The merge is per SOURCE rule, never across rates. MISC-D rule counts
#         drop by design (a six-body grid cell goes from six rules to one).
#         Rollback is either flag set to false, itself version-relevant.
#         NOTE for the rollout: because (b) drops rule counts in the
#         "commercial vehicle|misc-d" segment, the first re-extraction of an
#         already-cached Chola document may trip CINS_REGRESSION_GUARD and refuse
#         to pin. No segment disappears, so this is a count regression only —
#         expected and one-time. Relax CINS_REGRESSION_GUARD_RATIO (or disable
#         the guard) for that first pass if it fires.
#         (c) The stale-GCCV merged-header guard now matches the GCCV token
#         ANYWHERE in a leaked row, not only as a rule_text prefix. The May'26
#         grid spells the leak "GCV 1_GCCV_3W AP ACT" — the shorter "GCV" opens
#         the row while the GCCV token sits in the segment slug — so the old
#         startswith() test dropped none of the 222 phantom rows (74 into each of
#         PCV / Buses / MISC-D). 148 of them exported as MISC-D auto-rickshaws: a
#         three-wheeled goods carrier filed as a miscellaneous-D deal, which is
#         not a vehicle the carrier sells under that sub-product. Not flagged:
#         the rows are well-formed, they are simply in the wrong table. No flag —
#         this is a bug fix to an existing deterministic guard, and it can only
#         drop rows the guard was already written to drop.
#         Cached documents must re-extract or they keep serving the per-body
#         MISC-D rows, the bare "renewal" cell and the phantom auto-rickshaws.
#   1.11.0 — CC/GVW range boundaries are inclusive on both ends again.
#         Two regressions and one long-standing gap, all producing overlapping
#         or degenerate bands ("150cc to 150cc") in the export:
#         (a) An EXCLUSIVE lower bound is now made inclusive at parse time —
#         _parse_range_field(strict_bounds=True) turns "> 150" / "ABOVE 1500 CC"
#         into 151 / 1501 (">=" is untouched), and _recover_cc_range's GT slug
#         arm returns (N+1, 99999). Previously that +1 happened ONLY in the
#         de-overlap passes, which need a sibling rung in a compatible group as
#         evidence — so an isolated ">150cc" rule shipped overlapping the
#         "UPTO 150 CC" rule by one point. Applied to cc and gvw only: seating
#         deliberately reads "ABOVE 6" as (6, 99), and NCB owns its own GT/GTE
#         parser.
#         (b) A THIRD _normalize_boundary_overlaps pass runs after
#         _strip_gcv_subclass and the final collapse. Both earlier passes group
#         by all non-range conditions, vehicle_sub_class included, so a GCV
#         weight ladder whose rungs differ ONLY by body word was one singleton
#         group per body and was never walked — it exported its shared
#         boundaries verbatim. The strip (1.9.0) is what makes those rungs
#         siblings, and nothing ran after it.
#         (c) _dedupe_range_overlaps walks DISTINCT BANDS, not rules. A rung of
#         a ladder is routinely several rules — a grid column is published once
#         per make and per rate, and vehicle_make/vehicle_model are excluded
#         from the group key — and walking rules normalised only the FIRST rule
#         of each rung: the second (150,150) hit start <= prev_end with prev_end
#         already 150, the bump to 151 would have inverted the range, and the
#         invert-guard let it ship as the point band. This was the bulk of the
#         reported defect: on Chola May'26 doc b6b085b3, 538 Two Wheeler rules
#         stuck at (150,150) and 650 at (350,350), with exactly one rule per
#         rung coming out right.
#         (d) A cc/GVW band still collapsed to a point after all three passes is
#         now reported as a MEDIUM (never HIGH — a HIGH would block the cache
#         pin over one unresolved grid header) rather than silently exported.
#         Cached documents must re-extract or they keep serving the overlapping
#         bands. Expect boundary shifts of ±1 on affected deals; no rule is
#         added or dropped, so CINS_REGRESSION_GUARD should not fire.
#   1.12.0 — parse_cc_range no longer eats the lower bound of an underscore-
#         separated CC BAND. The Chola TW grid spells its middle column
#         "150_350CC" (the sheet uses "_" where other grids write "-"), and the
#         leading-ordinal strip added for the PCCV slugs ("2_4W_LT_1500CC")
#         treated that "150" as an enumeration index — collapsing the column to
#         the point "350", which then collided with the "350cc" column. That
#         left the ladder as TWO NON-ADJACENT SINGLETONS, (150,150) and
#         (350,350), with the middle rung gone: no adjacent boundary anywhere,
#         so the de-overlap passes correctly declined to touch them and the
#         grid exported "150cc to 150cc" / "350cc to 350cc". This was the
#         remaining half of the defect after 1.11.0 — the walk was right, but
#         the ladder handed to it had a hole in the middle. The ordinal is now
#         stripped only when the slug carries a non-numeric token (4W/LT/GT) or
#         when three or more numbers make a band impossible ("1_150_350CC").
#         Cached documents must re-extract; TW cc bands move from two point
#         bands to the full 0-150 / 151-350 / 351-99999 ladder.
#   1.13.0 — _normalize_boundary_overlaps compares the OTHER range dimensions
#         by OVERLAP, not equality, and no longer skips single-rule groups.
#         _DEDUPE_RANGES is walked one dimension at a time and each dimension's
#         group key holds the others' CURRENT values, so a rule un-bumped on TWO
#         dimensions at once was invisible on both: on the cc walk its NOP
#         (100,500) matched no sibling's (101,500), and on the NOP walk its cc
#         (150,350) matched no sibling's (151,350). Each waited for the other
#         and neither moved. Two overlapping bands describe the same region of
#         the same ladder; non-overlapping ones (0-150 vs 151-350) stay separate
#         contexts, and the bump is still gated on a real shared tier edge. The
#         size guard went with it — evidence comes from every compatible key, so
#         a singleton group is a legitimate candidate, and a stuck rule's unique
#         un-normalized band is exactly what makes its group a singleton.
#         Last 18 Two Wheeler rules on Chola May'26 doc b6b085b3; replaying the
#         pass over that document moves those 18 on both dimensions and nothing
#         else, in any product.
# 1.14.0 - The two upper Chola PCV seating bands were attached to the OPPOSITE
#         bodies. The client's spec is: 0-5 seats is a Normal Taxi (split further
#         by CC), 6-12 a Maxi Cab, 13-16 a Big Taxi. _PCV_MAXI_CAB_SEATING held
#         (13, 16) and _PCV_BIG_TAXI_SEATING held (6, 12), so every Maxi Cab deal
#         exported SeatingCapacityStartRange/EndRange of 13/16 and every Big Taxi
#         deal 6/12 — a downstream system matching a 9-seater to a rate card
#         found the wrong body class. The 0-5 half was always correct.
#         VehicleSubClass is UNCHANGED by this fix: the arms' conditions are
#         untouched, so the same rules resolve to the same bodies (maxicab, and
#         greater-than-six-seater for Big Taxi, which is Chola's own hierarchy
#         master spelling and stays literally true of a 13-16 seater). Only the
#         two seating integers move, which is exactly how the inversion survived
#         review. Rule counts hold, so CINS_REGRESSION_GUARD will not trip.
#         Direction of inference is unchanged and load-bearing: the segment label
#         picks the body and the constants supply the band, never the reverse —
#         Chola's grids only write "UPTO 5" / "ABOVE 6", and "ABOVE 6" parses to
#         (6, 99), which spans BOTH upper bands and cannot tell them apart.
#         No prompt file was touched (an edit under cins/prompts/ would
#         invalidate the whole cross-carrier cache via prompt_version_hash), and
#         no flag was added — this is a bug fix to an existing unflagged
#         transform, and a flag would only let someone re-enable wrong output.
#         SECOND defect, found on doc 5f29236b: the bands were unreachable. Both
#         body-keyed arms sat behind a `"4w-and-above" not in wheels` early
#         return, but the overlay sets number_of_wheels only on the "PCCV < 6"
#         table — every "PCCV > 6" row (Big Taxi, Maxi Cab) carries NO wheels, so
#         the arms were dead code on the only rows that can reach them. Live
#         output showed 24 maxicab and 18 taxi rules all still on the raw
#         "ABOVE 6" (6, 99) sentinel. The two arms now run BEFORE the wheel gate:
#         they only REFINE a body the source already named, and both bodies are
#         4-wheelers by definition. The <=5 arm, which INFERS a body the source
#         did not name, stays behind the gate.
#         Consequence worth noting: because the Big Taxis never became
#         greater-than-six-seater, they kept the exact "taxi" slug and
#         pcv_taxi_mirror cloned all 18 into the Private Car / Taxi product — a
#         13-16 seat commercial vehicle exported as a Private Car deal. That
#         product drops from 70 rules to 52 (the 52 genuine 0-5 taxis). This is
#         a real rule-count DROP, so the first re-extraction of an already-cached
#         Chola document MAY trip CINS_REGRESSION_GUARD and refuse to pin. No
#         segment disappears — expected and one-time, same shape as 1.10.0(b).
#         THIRD change: the 13-16 band now exports the new slug "big-taxi"
#         instead of "greater-than-six-seater", and "big taxi"/"big taxis" in a
#         grid body cell normalize to it rather than folding onto plain "taxi".
#         "Greater Than Six Seater" is Chola's ONLY >6 PCV body — its MMV book
#         maps both a 12-seater and a 16-seater onto it — so it cannot express
#         the client's 6-12 / 13-16 split and read as Maxi Cab territory anyway.
#         The hierarchy master carries neither "Big Taxi" nor "Maxi Cab", so both
#         raise a MEDIUM from CholaProductHierarchyValidator; that check is
#         MEDIUM by design and does not block the cache pin. The real fix is for
#         the carrier to add both rows to their product hierarchy master.
#         Folding "big taxis" onto plain "taxi" was also load-bearing in the
#         wrong direction: "taxi" is the exact slug pcv_taxi_mirror clones into
#         Private Car.
#         FOURTH change, master-data side: Chola's product hierarchy gains
#         "Maxi Cab" and "Big Taxi" rows for CV/PCV (their source workbook has
#         neither), and VEHICLE_SUB_CLASS_MAPPINGS learns four of the carrier's
#         OWN misspellings as they appear in that master — "Excavaters",
#         "Buldozer", "Tranist Mixer", "Refridgerated Van". A master value that
#         does not normalize is silently absent from canonical_subclass_slugs,
#         which was doing two things at once: making
#         CholaProductHierarchyValidator reject correctly-spelled slugs the grid
#         legitimately produced (excavators/bulldozer flagged invalid for
#         CV/MISC-D), and narrowing the MISC-D merge's widened body list.
#         So this DOES change export output: the MISC-D rest-bucket
#         VehicleSubClass cell goes from 22 to 25 bodies (gaining bulldozer,
#         excavators, transit-mixer) and GCV's widen-list from 13 to 14. Both
#         are corrections — the carrier does book those bodies — but a cached
#         document keeps serving the short list until it re-extracts.
#         Cached documents must re-extract or they keep serving the swapped
#         seating columns, the (6, 99) sentinel and the mirrored Big Taxis.
# 1.15.0 - The NCB base twin splits in two. _pair_ncb_tiers used to hand the
#         unbanded base twin of a marked "NCB GT 25%" rule the whole complement
#         (0, 24) as ncb=False, which conflated two commercially different
#         deals: a policy that earned NO bonus, and one that earned a bonus
#         below the marked threshold. The base twin now narrows to the no-bonus
#         point band (0, 0) / ncb=False, and a MIRROR of it is emitted at
#         (0, base_end) / ncb=True carrying the same rate — the grid publishes
#         one base rate for everything under the threshold, so both are priced
#         off it. Rule counts RISE on any NCB-bearing product (one extra rule
#         per base twin), so the first re-extraction of an affected document is
#         a count increase, not the drop CINS_REGRESSION_GUARD watches for.
#         The ncb boolean is no longer strictly derivable from the band — the
#         mirror is ncb=True on a band _ncb_bool_from_range reads as False — so
#         it is now authoritative and must not be re-derived post-transform.
#         Degenerate case: a marked tier starting at 1 leaves no room below it,
#         so the mirror is skipped and a warning names the rule. No env flag,
#         by decision. Cached documents must re-extract or they keep serving
#         the single 0-24 / No row.
# 1.16.0 - Range-boundary normalization now runs once more on the MERGED
#         document. Every boundary pass added in 1.11.0 lives inside
#         _transform_product, which for a sheet_by_sheet workbook runs once per
#         SHEET — so a ladder fragmented across sheets was never normalized at
#         all. The sheet holding the upper rungs has no lower rung to use as
#         evidence, and _merge_sheet_jsons concatenates the sheets after every
#         pass has finished. Chola "PO Grid-Apr 26.eml" (doc 1457d5e8): the
#         "Act Only Policy-Changes" sheet publishes a partial GCV weight table
#         starting at 40T_TO_43T, so its 7 third-party rules (one per state)
#         shipped GVW (40000, 43000) — overlapping by one point the
#         20T_TO_40T rung that lives in the " CVApr26" sheet and ends at 40000,
#         while that sheet's own 40001-43000 / 43001-47500 / 47501-999999
#         ladder came out correct. Only the evidence-gated
#         _normalize_boundary_overlaps is re-run (see
#         processors/core/boundary_normalize.py); _dedupe_range_overlaps is an
#         unconditional ladder walk and is deliberately NOT re-run, since
#         merging two sheets' tables into one group could shift bands sharing
#         no real tier edge. A start moves only where it already sat on a
#         compatible band's end, so the blast radius on the reported document
#         is exactly those 7 rules and nothing else. No rule is added or
#         dropped and no product or segment disappears, so
#         CINS_REGRESSION_GUARD should not fire. Cached documents must
#         re-extract or they keep serving the overlapping band.
#
# 1.17.0 — a Two-Wheeler rule whose vehicle_make names an electric bucket
#         ("All Electric Make") is tagged fuel_type=["electric"] host-side.
#         The Chola electric grid prices every electric two-wheeler under that
#         one bucket column and `chola_pass3.md` tells codegen to set the fuel
#         from it, but the prose does not hold: on doc 1457d5e8 all 84 raw
#         rules of the block came back with the bucket in vehicle_make and
#         fuel_type UNSET, so an electric-only segment exported as
#         fuel-agnostic (all five fuels) and overlapped the petrol make grid
#         beside it. Runs only when no fuel was parsed, so an explicit fuel
#         always wins. Cached documents must re-extract or they keep serving
#         the untagged rows. Pairs with INDEXER_VERSION 1.10, which is what
#         made the electric block extract at all.
# 1.18.0 — a mirrored Private Car / Taxi rule carries NO seating band. The
#         client does not bifurcate Taxi by seating capacity on the Private Car
#         side: there is one Private Car / Taxi deal and it exists only for the
#         up-to-5-seat taxi (which is already all pcv_taxi_mirror clones, since
#         it keys on the exact {"taxi"} slug). The mirror was a verbatim
#         deepcopy, so every _PCTAXI row exported
#         SeatingCapacityStartRange/EndRange = 0/5 — asserting a seat-based
#         scoping the client never books. The COMMERCIAL twin is untouched;
#         there the band is the only thing separating Normal Taxi (0-5) from
#         Maxi Cab (6-12) and Big Taxi (13-16), and it shipped inverted once
#         already (1.14.0). Guarded: nulling a dimension makes two rules
#         separated only by that dimension _conditions_identical, i.e. a
#         CRITICAL complete_overlap that would stop should_cache pinning the
#         document, so a product whose taxi rules collide that way keeps its
#         seat band and logs a warning. No rule is added or dropped and no
#         product or segment disappears, so CINS_REGRESSION_GUARD should not
#         fire. Cached documents must re-extract or they keep serving the
#         populated seating cells.
CICS_PIPELINE_VERSION = "1.18.0"

# Directory whose contents define the prompt corpus (base prompts + overlays).
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "cins" / "prompts"

_prompt_hash_cache: Optional[str] = None


def compute_prompt_version_hash() -> str:
    """SHA256 over every prompt/overlay file, so a prompt edit invalidates the cache.

    Hashes the sorted (relative-path, bytes) of all ``*.md`` files under
    ``cins/prompts`` (base prompts + ``carrier_overlays``). Cached in-process; the
    corpus is static per deploy.
    """
    global _prompt_hash_cache
    if _prompt_hash_cache is not None:
        return _prompt_hash_cache
    h = hashlib.sha256()
    try:
        files = sorted(_PROMPTS_DIR.rglob("*.md"), key=lambda p: str(p))
        for p in files:
            rel = p.relative_to(_PROMPTS_DIR).as_posix()
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"prompt_version_hash: could not hash prompts dir: {e}")
        # Fall back to a stable sentinel so caching still works within a deploy.
        h.update(b"prompt-hash-unavailable")
    _prompt_hash_cache = h.hexdigest()
    return _prompt_hash_cache


def compute_cache_key(
    *,
    content_hash: str,
    indexer_version: str,
    prompt_version_hash: str,
    llm_provider: str,
    llm_model: str,
    pipeline_version: str = CICS_PIPELINE_VERSION,
) -> str:
    """SHA256 of the six components (in order) that can change extraction output.

    Provider and model are lowercased; components are `\\x1f`-joined so a free-text
    model name can't merge into an adjacent field. Any component change → new key →
    cache miss → fresh extraction.
    """
    parts = [
        content_hash or "",
        indexer_version or "",
        prompt_version_hash or "",
        (llm_provider or "").lower(),
        (llm_model or "").lower(),
        pipeline_version or "",
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _pass3_requires_review(sheet_pass_results) -> bool:
    """True when any Pass-3 result flagged overall_requires_review.

    That is the completeness-gate / validation signal — we must never cache a
    result the gate flagged, or a segment-dropping run gets frozen and every rerun
    is deterministically wrong-but-'stable' (CEO#2 / Eng D2).
    """
    for entry in sheet_pass_results or []:
        try:
            if int(entry.get("pass_number", 0)) != 3:
                continue
        except (TypeError, ValueError):
            continue
        result = entry.get("result") or {}
        if isinstance(result, dict) and result.get("overall_requires_review"):
            return True
        if entry.get("status") == "failed":
            return True
    return False


def _has_blocking_validation_issue(validation_issues) -> bool:
    """True when any validator issue is CRITICAL or HIGH.

    ``PipelineResult.errors`` holds pipeline *exceptions*, not validator
    findings — validator output lands in ``validation_issues``. Without this
    check a HIGH finding (missing mandatory Chola vehicle_sub_class, a dropped
    coverage row, a state-column misalignment) would still be pinned into the
    cache and every rerun would replay the same wrong answer.
    """
    for issue in validation_issues or []:
        severity = (
            issue.get("severity")
            if isinstance(issue, dict)
            else getattr(issue, "severity", None)
        )
        name = getattr(severity, "value", severity)
        if isinstance(name, str) and name.upper() in ("CRITICAL", "HIGH"):
            return True
    return False


def should_cache(result) -> bool:
    """Only cache a clean, gate-passing result.

    Blocks the write when the pipeline reported errors, a validator raised a
    CRITICAL/HIGH issue, or any Pass-3 flagged requires_review (completeness
    gate shortfall / validation). A conservative write policy is the whole
    point: a false miss just re-runs the LLM; a false write pins a wrong answer.
    """
    if getattr(result, "errors", None):
        return False
    if not getattr(result, "cics_doc", None):
        return False
    if _has_blocking_validation_issue(getattr(result, "validation_issues", None)):
        return False
    if _pass3_requires_review(getattr(result, "sheet_pass_results", []) or []):
        return False
    return True


def lookup(cache_key: str):
    """Return the CicsOutputCache row for ``cache_key`` or None."""
    from commission_intake.models import CicsOutputCache

    return CicsOutputCache.objects.filter(cache_key=cache_key).first()


# ── Cross-run regression guard (Option C) ────────────────────────────────────
# A cache MISS re-extraction (prompt / pipeline / model / indexer version changed)
# can silently regress a document — a whole sub-product/segment the last good run
# emitted goes missing (the June thin-CV / May1 GCV-3W symptom). The per-sheet gate
# is blind to this (fan-out), so instead we diff the new output against the most
# recent gate-passing extraction of the SAME source content and flag a regression.
#
# Conservative by construction: the strong, bump-robust signal is a whole product
# DISAPPEARING (a transformer/prompt fix rarely deletes an entire product); a raw
# total-count drop is only used with a floor + minimum, since an intended fix can
# legitimately shrink counts.


def _regression_enabled() -> bool:
    return _env_flag("CINS_REGRESSION_GUARD", True)


def _regression_ratio() -> float:
    return _env_float("CINS_REGRESSION_GUARD_RATIO", 0.75)


def _regression_min_total() -> int:
    return _env_int("CINS_REGRESSION_GUARD_MIN_TOTAL", 20)


@dataclass
class RegressionReport:
    is_regression: bool = False
    disappeared: List[str] = field(default_factory=list)
    baseline_total: int = 0
    new_total: int = 0
    reason: str = ""
    warning: str = ""


def product_rule_counts(cics_doc: Optional[dict]) -> Dict[str, int]:
    """Map ``(product_name, sub_category)`` → emitted rule count for a CICS doc.

    Aggregates duplicates (a doc can carry several ``Commercial Vehicle`` products
    distinguished only by sub_category) so the diff is order-independent.
    """
    counts: Dict[str, int] = {}
    for p in (cics_doc or {}).get("products", []) or []:
        name = str(p.get("product_name") or "").strip().lower()
        sub = str(p.get("sub_category") or "").strip().lower()
        key = f"{name}|{sub}"
        counts[key] = counts.get(key, 0) + len(p.get("commission_rules", []) or [])
    return counts


def detect_regression(
    baseline_cics_doc: Optional[dict], new_cics_doc: Optional[dict]
) -> RegressionReport:
    """Flag when a new extraction lost a product/segment vs the prior good baseline."""
    if not _regression_enabled() or not baseline_cics_doc or not new_cics_doc:
        return RegressionReport()

    base = product_rule_counts(baseline_cics_doc)
    new = product_rule_counts(new_cics_doc)
    base_total = sum(base.values())
    new_total = sum(new.values())

    # A product/segment present before but now empty. Only count this as a
    # regression when the sheet's TOTAL rule count also dropped — a legitimate
    # sub_category rename (e.g. "gcv-3w" -> "goods carrying 3w") moves the same
    # rules to a new bucket, so the old key "disappears" while the total is
    # preserved. Flagging that would refuse to cache forever and re-warn on every
    # rerun (the taxonomy-churn false-positive), so require a real net loss.
    disappeared = (
        [k for k, v in base.items() if v > 0 and new.get(k, 0) == 0]
        if new_total < base_total
        else []
    )

    ratio = (new_total / base_total) if base_total else 1.0
    big_shortfall = (
        base_total >= _regression_min_total() and ratio < _regression_ratio()
    )

    if not disappeared and not big_shortfall:
        return RegressionReport(baseline_total=base_total, new_total=new_total)

    bits = []
    if disappeared:
        pretty = ", ".join(
            d.replace("|", "/").strip("/") or "(unnamed)" for d in disappeared
        )
        bits.append(f"products present before but now empty/missing: {pretty}")
    if big_shortfall:
        bits.append(
            f"total rules dropped {base_total} → {new_total} "
            f"({ratio:.0%} of the prior good extraction)"
        )
    warning = (
        "REGRESSION vs last good extraction of this document — "
        + "; ".join(bits)
        + ". Not cached; needs manual review before trusting."
    )
    reason = "disappeared products" if disappeared else "total shortfall"
    return RegressionReport(
        is_regression=True,
        disappeared=disappeared,
        baseline_total=base_total,
        new_total=new_total,
        reason=reason,
        warning=warning,
    )


def find_prior_baseline(content_hash: str, exclude_cache_key: str = ""):
    """Most recent gate-passing cached extraction of the SAME source content.

    Keyed by ``content_hash`` (identical source bytes) but ignoring the rest of the
    cache key, so it finds the prior good result even after a prompt / pipeline /
    model bump changed the active cache key. Returns None on the first-ever run.
    """
    from commission_intake.models import CicsOutputCache

    if not content_hash:
        return None
    qs = CicsOutputCache.objects.filter(content_hash=content_hash)
    if exclude_cache_key:
        qs = qs.exclude(cache_key=exclude_cache_key)
    return qs.order_by("-created_at").first()


def store(
    *,
    cache_key: str,
    content_hash: str,
    indexer_version: str,
    prompt_version_hash: str,
    llm_provider: str,
    llm_model: str,
    result,
    provider_id: str = "",
) -> None:
    """Persist a gate-passing result under ``cache_key`` (idempotent upsert)."""
    from commission_intake.models import CicsOutputCache

    CicsOutputCache.objects.update_or_create(
        cache_key=cache_key,
        defaults={
            "content_hash": content_hash or "",
            "indexer_version": indexer_version or "",
            "prompt_version_hash": prompt_version_hash or "",
            "pipeline_version": CICS_PIPELINE_VERSION,
            "llm_provider": llm_provider or "",
            "llm_model": llm_model or "",
            "cics_doc": result.cics_doc,
            "warnings": getattr(result, "warnings", []) or [],
            "notices": getattr(result, "notices", []) or [],
            "errors": getattr(result, "errors", []) or [],
            "duration_seconds": getattr(result, "duration", 0.0) or 0.0,
            "sheet_results": getattr(result, "sheet_results", []) or [],
            "sheet_pass_results": getattr(result, "sheet_pass_results", []) or [],
            "provider_id": provider_id or "",
        },
    )
