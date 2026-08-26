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

Gap-fill: the clause does not always reach every rule
-----------------------------------------------------
``_propagate_cpa_clause_within_product`` closes a hole in the above. A rule only
splits if the clause reaches IT, and two shapes of rule never carry the footnote
in their own ``other_conditions``: synthesized 0%-backstop rules (declined
segments, which have no notes at all) and rules extracted from a SEPARATE SHEET
of the same workbook (Chola's ``Act Only Policy-Changes`` publishes a heavy-GVW
ACT ladder while the footnote lives on the main CV sheet). Both stayed
cpa-unconstrained and exported ONE row labelled the merged "With CPA,Without
CPA" at the undeducted rate. Measured on the stored corpus: 265 Chola GCV rules,
21 of them at real rates.

Four things are deliberate and none of them is a tuning knob:

* **Carrier-gated to Chola, and sub-product-gated to commercial-gcv.** This
  contradicts the "only clause-carrying rules split, so no carrier check" note
  above ON PURPOSE, and the distinction is the whole point: that rule governs
  rules whose OWN text states the deduction, where carrier-agnosticism costs
  nothing. This one INFERS a deduction for rules whose text never stated it,
  which is a strictly stronger claim, and Chola GCV is the only population in
  the corpus where it is evidenced. Do not "fix" the inconsistency by widening
  it. Chola's PCV / Buses / MISC-D are already 100% split and need nothing.
* **Product-scoped, not document-scoped.** A product is the narrowest unit that
  provably shares a rate card; document scope would let a Two Wheeler footnote
  reach a CV product.
* **Only rules with EMPTY ``other_conditions`` are filled.** A rule that HAS
  notes has text that was already examined and found not to carry the footnote —
  overriding that is a guess. A rule with NO notes had nothing to examine. This
  is also what excludes a real defect the gap-fill must not paper over: 96 rules
  in ``June PO Grid - Chola MS.eml`` carry a Private-Car summary-sheet blurb and
  are duplicates (68 exact, 24 same-slot-different-rate) of the real grid,
  extracted from stale ``CV AUG-25`` / ``DIFF`` sheets. Deducting 1.5% from
  those would polish rows that should not be in the export at all.
* **Conflicting deduction values disable the fill for that product.** A product
  publishing two amounts is a detection failure, not a gap; guessing which
  applies would misprice silently.
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


# Gap-fill scope. Both gates are load-bearing — see the module docstring for why
# this pass is carrier-scoped while the split itself deliberately is not.
_GAP_FILL_CARRIER = "Chola"
_GAP_FILL_SUBPRODUCT = "commercial-gcv"


def _document_carrier(cics_doc):
    """Canonical carrier short code for a CICS document, or None.

    Reads ``provider.provider_id`` (already a short code) and falls back to the
    free-text ``provider_name``. Fails safe: an absent or unrecognised provider
    yields None, which makes every carrier-scoped pass a no-op.
    """
    from cins.schema.carriers import normalize_carrier

    provider = (cics_doc or {}).get("provider")
    if not isinstance(provider, dict):
        return None
    for key in ("provider_id", "provider_name"):
        carrier = normalize_carrier(provider.get(key))
        if carrier:
            return carrier
    return None


def _resolved_cpa_clause(rule):
    """The without-cpa deduction clause a rule resolves on its own, or None.

    Exactly what ``_split_rule`` consults, in the same order — structured clause
    first, free-text notes second. Shared so the gap-fill can never disagree
    with the split about which rules already have a clause.
    """
    outcome = rule.get("outcome") or {}
    return cpa_deduction_clause(
        outcome.get("qualifying_clauses")
    ) or _cpa_clause_from_notes(outcome.get("other_conditions"))


def _propagate_cpa_clause_within_product(product, carrier):
    """Attach a product's CPA deduction clause to its note-less unconstrained rules.

    Scoped to ``_GAP_FILL_CARRIER`` / ``_GAP_FILL_SUBPRODUCT``. A rule is filled
    only when its ``conditions.cpa`` is unconstrained, it resolves no clause of
    its own, and its ``outcome.other_conditions`` is EMPTY — see the module
    docstring for why the empty-notes test is the discriminator rather than a
    convenience. Returns the number of rules filled.

    Mutates ``product`` in place. Idempotent by construction: ``_split_rule``
    runs straight after and gives every filled rule an explicit ``cpa``, so a
    second pass finds nothing unconstrained left to fill.
    """
    if carrier != _GAP_FILL_CARRIER:
        return 0
    if (product or {}).get("product_subproduct") != _GAP_FILL_SUBPRODUCT:
        return 0
    rules = product.get("commission_rules") or []
    if not rules:
        return 0

    clauses = {}
    for rule in rules:
        clause = _resolved_cpa_clause(rule)
        if clause is not None:
            value = ((clause.get("effect") or {}).get("deduction") or {}).get("value")
            clauses.setdefault(value, clause)
    if not clauses:
        return 0
    if len(clauses) > 1:
        logger.warning(
            "cpa_split: product %s publishes %d different CPA deduction values (%s); "
            "skipping the gap-fill for it rather than guessing which applies",
            product.get("product_code") or product.get("product_name"),
            len(clauses),
            sorted(str(v) for v in clauses),
        )
        return 0

    clause = next(iter(clauses.values()))
    filled = 0
    for rule in rules:
        conditions = rule.get("conditions") or {}
        if conditions.get("cpa") is not None:
            continue
        outcome = rule.get("outcome") or {}
        if str(outcome.get("other_conditions") or "").strip():
            continue
        if _resolved_cpa_clause(rule) is not None:
            continue
        import copy

        out = rule.setdefault("outcome", {})
        # Not setdefault: the key is routinely present and explicitly null, in
        # which case setdefault hands back None and the append blows up.
        existing = out.get("qualifying_clauses") or []
        out["qualifying_clauses"] = existing + [copy.deepcopy(clause)]
        filled += 1
    return filled


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

    ``_propagate_cpa_clause_within_product`` runs per product BETWEEN the two:
    after the dedup, so a fabricated third-party duplicate is deleted rather
    than gap-filled and then split into two exported rows; before the split, so
    a filled rule bifurcates in the same pass as every other.
    """
    if not isinstance(cics_doc, dict):
        return cics_doc
    drop_mirrored_third_party(cics_doc)
    carrier = _document_carrier(cics_doc)
    stats = collections.Counter()
    for product in cics_doc.get("products") or []:
        rules = product.get("commission_rules")
        if not rules:
            continue
        stats["gap_filled"] += _propagate_cpa_clause_within_product(product, carrier)
        rules = product.get("commission_rules")
        expanded = []
        for rule in rules:
            expanded.extend(_split_rule(rule, stats))
        product["commission_rules"] = expanded
    if stats["gap_filled"]:
        logger.info(
            "cpa_split: gap-filled the CPA deduction clause onto %d note-less "
            "%s %s rule(s) that the footnote never reached",
            stats["gap_filled"],
            _GAP_FILL_CARRIER,
            _GAP_FILL_SUBPRODUCT,
        )
    if stats["tp_skipped_negative"]:
        logger.warning(
            "cpa_split: %d third-party without-CPA row(s) kept UNDEDUCTED because the "
            "deduction exceeds the TP payout (%d TP row(s) deducted normally). Both "
            "CPA variants export the same rate for those rows.",
            stats["tp_skipped_negative"],
            stats["tp_deducted"],
        )
    return cics_doc
