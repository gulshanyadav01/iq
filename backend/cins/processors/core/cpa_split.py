"""Materialize CPA-deduction rules into With-CPA / Without-CPA variants.

A commission grid sometimes carries a footnote like "if CPA is not collected
for individual name then 1.5% of OD Pay-out is deducted". Pass 3 stores this as
a structured ``conditional_deduction`` qualifying clause on a rule whose own CPA
condition is unconstrained (``conditions.cpa is None``).

Bifurcation is business logic, not presentation: it belongs in the transform so
the canonical CICS document carries both rows and every consumer (Excel export,
API, downstream booking, validators, rule counts) sees the same data. This
module is applied right after ``RawToCICSTransformer.transform`` for each
pipeline mode.

The function is pure and idempotent: a rule that has already been split has an
explicit ``cpa`` value (and the clause removed), so it is never split again.

The deduction comes off the rule's OWN payout column — od / net / tp — in
percentage POINTS, not off the premium basis the clause happens to name (the
footnote is Private-Car-centric and says "OD Pay-out" even on a Net-paying CV
grid). Third-party rules split and deduct like any other: the footnote reduces
the BROKER's payout, not the statutory TP premium, so there is no pass-through
to protect. What differs is only the negative case, and the two policies must
not be conflated:

* **od / net clamp at 0** (``deduct_pct``) — a 1.5-point deduction on a 0.5%
  Two Wheeler grade would otherwise emit a negative rate that fails schema
  validation CRITICAL and takes the whole document down.
* **tp declines** (``deduct_pct_no_negative``) — the row still bifurcates, but
  both variants carry the undeducted rate, because a 0% ACT payout is a number
  the carrier never published while the grid rate at least appears on the grid.
  The count is logged at WARNING so the frequency stays visible in real data.
"""

import collections
import json
import logging

logger = logging.getLogger("cins.cpa_split")


def _clause_key(clause):
    """Stable hashable key for a clause, order-independent."""
    if isinstance(clause, (dict, list)):
        return json.dumps(clause, sort_keys=True, ensure_ascii=False)
    return str(clause or "")


def applied_on_to_column(applied_on):
    """Map an applied_on phrase to its commission column: od / tp / net / None.

    'OD Cover Premium' / 'OD Pay-out' -> 'od'; 'TP/PA Premium' -> 'tp';
    'Net Premium' -> 'net'. None when the phrase names no known basis.
    """
    if not applied_on:
        return None
    s = str(applied_on).lower()
    if "od" in s:
        return "od"
    if "tp" in s or "pa" in s:
        return "tp"
    if "net" in s:
        return "net"
    return None


def derive_applied_on(conditions, outcome):
    """Resolve the applied_on basis that decides a rule's commission column.

    ``outcome.applied_on`` is often a generic default ('Net Premium'); the real
    basis is carried by coverage-conditional ``applied_on_override`` clauses.
    Match the override whose ``when.coverage_type_text`` aligns with the rule's
    ``conditions.policy_type`` (comprehensive / third-party); fall back to
    ``outcome.applied_on``.
    """
    conditions = conditions or {}
    outcome = outcome or {}
    pt = conditions.get("policy_type")
    if isinstance(pt, list):
        pt = pt[0] if pt else None
    pt = str(pt or "").lower()
    coverage = None
    if "comprehensive" in pt:
        coverage = "comprehensive"
    elif "third" in pt:
        coverage = "third party"
    if coverage:
        for clause in outcome.get("qualifying_clauses") or []:
            if not isinstance(clause, dict):
                continue
            if clause.get("clause_type") != "applied_on_override":
                continue
            when = (clause.get("when") or {}).get("coverage_type_text")
            options = when if isinstance(when, list) else [when]
            if any(coverage in str(o or "").lower() for o in options):
                applied_on = (clause.get("effect") or {}).get("applied_on")
                if applied_on:
                    return applied_on
    return outcome.get("applied_on")


def cpa_deduction_clause(qualifying_clauses):
    """Return the without-cpa conditional_deduction clause for a rule, or None.

    Matches clause_type == 'conditional_deduction' with when.cpa == 'without-cpa'
    and a numeric effect.deduction.value.
    """
    for c in qualifying_clauses or []:
        if not isinstance(c, dict):
            continue
        if c.get("clause_type") != "conditional_deduction":
            continue
        when = c.get("when") or {}
        if when.get("cpa") != "without-cpa":
            continue
        val = ((c.get("effect") or {}).get("deduction") or {}).get("value")
        if isinstance(val, (int, float)):
            return c
    return None


def _cpa_clause_from_notes(other_conditions):
    """Detect a without-cpa conditional_deduction clause from free-text notes.

    Reuses the same footnote patterns as Pass-3 codegen so a rule whose notes
    name the deduction ("...X% of Net/OD Payout will be deducted") gets split
    even when no structured clause was attached upstream. Returns the clause
    dict (same shape as `cpa_deduction_clause`) or None.
    """
    if not other_conditions:
        return None
    # Lazy import: detect_qualifying_clauses is a host-side helper in the
    # codegen module; importing it at module load is unnecessary coupling.
    from cins.processors.core.code_gen_executor import detect_qualifying_clauses

    try:
        clauses = detect_qualifying_clauses(str(other_conditions))
    except Exception:
        return None
    return cpa_deduction_clause(clauses)


def deduct_pct(v, amount):
    """Subtract `amount` percentage points from a numeric % cell; pass others through.

    Clamped at 0: a CPA-not-collected deduction can exceed the base commission
    on low-rate rows (e.g. a 1.5% OD-CPA deduction on a 0.5%-1% Two Wheeler
    grade), which would otherwise emit a negative commission that the schema
    validator flags CRITICAL and fails the whole document on. A carrier never
    pays a negative commission, so the floor is 0.

    This is the od / net path only; the tp path uses
    ``deduct_pct_no_negative``. Do not merge the two — see the module docstring
    for why they differ."""
    if v is None or v == "":
        return v
    try:
        return max(0.0, round(float(v) - amount, 4))
    except (TypeError, ValueError):
        return v


def deduct_pct_no_negative(v, amount):
    """TP path: subtract `amount` points, or DECLINE when it would go negative.

    Returns ``(value, applied)``. Where ``deduct_pct`` floors an over-large
    deduction at 0, this one refuses it and hands back the input unchanged: a
    third-party ACT rate is routinely smaller than the footnote's deduction (a
    1.5-point deduction against a ~0.3% ACT payout), and flooring those to 0
    would export a 0% commission deal — a rate the carrier never published, and
    one a booking system may well read as "no payout at all". Handing back the
    grid rate is the conservative direction: the row still bifurcates into
    With-CPA / Without-CPA, the two variants simply carry the same value.

    ``applied`` lets the caller count declines so ``materialize_cpa_split`` can
    report how often this fires on real documents.
    """
    if v is None or v == "":
        return v, False
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v, False
    new = round(f - amount, 4)
    # Exactly zero is not negative — a rate the deduction consumes precisely is
    # a real 0%, not an over-large deduction.
    if new < 0:
        return v, False
    return new, True


def _split_rule(rule, stats=None):
    """Return [rule] unchanged, or [with_cpa, without_cpa] when it carries a
    without-cpa conditional_deduction and its own CPA is unconstrained.

    ``stats`` is an optional Counter the tp-path deduction reports into; see
    ``materialize_cpa_split``.
    """
    import copy

    conditions = rule.get("conditions") or {}
    outcome = rule.get("outcome") or {}

    # NOTE: third-party rules used to be exempted here, on the reasoning that
    # TP/PA is a statutory pass-through that is never CPA-deducted. That was
    # wrong about what the footnote deducts: it reduces the BROKER's payout,
    # not the statutory premium, so a TP rule carrying the clause splits and
    # deducts like any other. The exemption's visible cost was that such a rule
    # stayed cpa-unconstrained and exported ONE row labelled the merged
    # "With CPA,Without CPA" at the UNDEDUCTED rate — overstating the
    # without-CPA payout by the full deduction on every one of them.
    #
    # Only clause-carrying rules split, which is what keeps this Chola-scoped
    # in practice: no other carrier in the corpus writes the footnote. That is
    # deliberate — there is no carrier-name check here and there should not be.

    dc = cpa_deduction_clause(outcome.get("qualifying_clauses"))
    if dc is None:
        # Fallback: synthesize the clause from the rule's free-text notes.
        # Chola Commercial-Vehicle grids carry the CPA-deduction footnote only
        # in outcome.other_conditions ("...1.5% of Net Payout will be
        # deducted"), never as a structured qualifying_clause, so without this
        # the without-cpa deduction is silently dropped for CV.
        dc = _cpa_clause_from_notes(outcome.get("other_conditions"))
    if dc is None or conditions.get("cpa") is not None:
        return [rule]

    dc_key = _clause_key(dc)
    deduction = ((dc.get("effect") or {}).get("deduction") or {}).get("value")
    rule_col = applied_on_to_column(derive_applied_on(conditions, outcome))

    def variant(cpa_value, apply_deduction):
        v = copy.deepcopy(rule)
        v.setdefault("conditions", {})["cpa"] = cpa_value
        out = v.setdefault("outcome", {})
        out["qualifying_clauses"] = [
            c for c in (out.get("qualifying_clauses") or []) if _clause_key(c) != dc_key
        ]
        # The CPA-not-collected deduction reduces the broker's commission
        # *payout*, which is whatever column the rule pays in — OD (Private Car
        # comprehensive), Net (Commercial Vehicle) or TP/PA (ACT). So apply it
        # to the rule's own payout column, independent of the premium basis
        # named in the clause (often a Private-Car-centric "OD Pay-out").
        #
        # The tp branch is tested FIRST, and on `_is_third_party` as well as on
        # the column: `rule_col` is derived from applied_on, not policy_type, so
        # a third-party rule carrying no third-party applied_on_override falls
        # back to outcome.applied_on ("Net Premium") and would otherwise take
        # the clamp-at-0 path. It is the rule's coverage, not the label on its
        # premium basis, that makes a 0% floor the wrong answer.
        if apply_deduction:
            if _is_third_party(rule) or rule_col == "tp":
                new, applied = deduct_pct_no_negative(
                    out.get("percentage_value"), deduction
                )
                out["percentage_value"] = new
                if stats is not None:
                    stats["tp_deducted" if applied else "tp_skipped_negative"] += 1
            elif rule_col in ("od", "net"):
                out["percentage_value"] = deduct_pct(
                    out.get("percentage_value"), deduction
                )
        return v

    return [variant("with-cpa", False), variant("without-cpa", True)]


# Condition dimensions that identify a rule's coverage-independent grid slot.
# policy_type is deliberately excluded — it is the coverage axis we compare across.
_MIRROR_DIM_KEYS = (
    "state",
    "rto",
    "vehicle_make",
    "vehicle_model",
    "vehicle_sub_class",
    "number_of_wheels",
    "fuel_type",
    "business_type",
    "cc_start_range",
    "cc_end_range",
    "gvw_start_range",
    "gvw_end_range",
    "seating_capacity_start_range",
    "seating_capacity_end_range",
    "policies_count_start_range",
    "policies_count_end_range",
    # The NCB band is a grid slot like any other: a base tier and its ">25%"
    # twin sit in the SAME cell of every other dimension and differ only here.
    # Omitting it makes two genuinely different tiers share one signature, so a
    # third-party rule can mirror a comprehensive rule it does not actually
    # duplicate — and this signature feeds a path that DELETES rules. Including
    # it can only make `drop_mirrored_third_party` fire less often, which is the
    # conservative direction that function documents for itself.
    "ncb_start_range",
    "ncb_end_range",
)


def _mirror_signature(rule):
    """Coverage-independent (dims + payout value) signature of a rule."""
    cond = rule.get("conditions") or {}
    outcome = rule.get("outcome") or {}
    dims = tuple(str(cond.get(k)) for k in _MIRROR_DIM_KEYS)
    return dims + (str(outcome.get("percentage_value")),)


def _is_third_party(rule):
    pt = (rule.get("conditions") or {}).get("policy_type")
    pts = pt if isinstance(pt, (list, tuple)) else [pt]
    return any("third" in str(p).lower() for p in pts if p is not None)


def _is_comprehensive(rule):
    pt = (rule.get("conditions") or {}).get("policy_type")
    pts = pt if isinstance(pt, (list, tuple)) else [pt]
    return any("comprehensive" in str(p).lower() for p in pts if p is not None)


def drop_mirrored_third_party(cics_doc):
    """Drop third-party rules that exactly mirror a comprehensive rule.

    Guards a codegen fabrication: for a flattened single-value grid (e.g. the
    Chola Two Wheeler make x band x state grid), the generated parser can emit
    each cell TWICE — once comprehensive, once third-party — with the identical
    Package value, inflating the rule count with bogus TP deals (a 45% TW
    Liability payout where the real ACT rate is ~0.3%).

    Discriminator: a product with a REAL third-party column (PC / CV ACT) carries
    third-party rows whose rates genuinely differ from the comprehensive sibling,
    so only SOME of its third-party rules mirror a comprehensive (by coincidence).
    A fabricated grid duplicates EVERY cell, so ALL of its third-party rules
    mirror a comprehensive. We only strip a product's third-party rules when
    **every one** of them exactly mirrors a comprehensive rule (dims + value) —
    i.e. the product has no genuine third-party rate at all. This never touches
    PC/CV, whose third-party sets contain non-mirroring rows.

    Mutates and returns ``cics_doc``. Product-scoped; conservative.
    """
    if not isinstance(cics_doc, dict):
        return cics_doc
    for product in cics_doc.get("products") or []:
        rules = product.get("commission_rules")
        if not rules:
            continue
        tp_rules = [r for r in rules if _is_third_party(r)]
        if not tp_rules:
            continue
        comp_sigs = {_mirror_signature(r) for r in rules if _is_comprehensive(r)}
        if not comp_sigs:
            continue
        # Only fabricated when the ENTIRE third-party set is comprehensive mirrors.
        if not all(_mirror_signature(r) in comp_sigs for r in tp_rules):
            continue
        product["commission_rules"] = [r for r in rules if not _is_third_party(r)]
        logger.info(
            "coverage_dedup: dropped %d fabricated third-party rule(s) (all "
            "mirrored a comprehensive rule) in product %s",
            len(tp_rules),
            product.get("product_name"),
        )
    return cics_doc


def materialize_cpa_split(cics_doc):
    """Expand CPA-deduction rules in-place into With/Without-CPA variants.

    Mutates and returns `cics_doc`. Idempotent: already-split rules carry an
    explicit cpa value and no clause, so a second pass is a no-op.

    Runs ``drop_mirrored_third_party`` FIRST so fabricated comprehensive->TP
    duplicates never reach the split (and never get counted/exported). Do not
    reorder these two: since third-party rules now split, a dedup running after
    the split would compare a deducted without-cpa value against its
    comprehensive twin, stop recognising the mirror, and let the fabricated pair
    through as two rows instead of zero.
    """
    if not isinstance(cics_doc, dict):
        return cics_doc
    drop_mirrored_third_party(cics_doc)
    stats = collections.Counter()
    for product in cics_doc.get("products") or []:
        rules = product.get("commission_rules")
        if not rules:
            continue
        expanded = []
        for rule in rules:
            expanded.extend(_split_rule(rule, stats))
        product["commission_rules"] = expanded
    if stats["tp_skipped_negative"]:
        logger.warning(
            "cpa_split: %d third-party without-CPA row(s) kept UNDEDUCTED because the "
            "deduction exceeds the TP payout (%d TP row(s) deducted normally). Both "
            "CPA variants export the same rate for those rows.",
            stats["tp_skipped_negative"],
            stats["tp_deducted"],
        )
    return cics_doc
