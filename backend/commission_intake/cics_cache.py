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
CICS_PIPELINE_VERSION = "1.9.0"

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
