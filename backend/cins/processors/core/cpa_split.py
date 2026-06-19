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
"""

import json


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


def deduct_pct(v, amount):
    """Subtract `amount` percentage points from a numeric % cell; pass others through."""
    if v is None or v == "":
        return v
    try:
        return round(float(v) - amount, 4)
    except (TypeError, ValueError):
        return v


def _split_rule(rule):
    """Return [rule] unchanged, or [with_cpa, without_cpa] when it carries a
    without-cpa conditional_deduction and its own CPA is unconstrained."""
    import copy

    conditions = rule.get("conditions") or {}
    outcome = rule.get("outcome") or {}
    dc = cpa_deduction_clause(outcome.get("qualifying_clauses"))
    if dc is None or conditions.get("cpa") is not None:
        return [rule]

    dc_key = _clause_key(dc)
    deduction = ((dc.get("effect") or {}).get("deduction") or {}).get("value")
    ded_col = applied_on_to_column(
        (dc.get("effect") or {}).get("deduction", {}).get("applied_on")
    )
    rule_col = applied_on_to_column(derive_applied_on(conditions, outcome))

    def variant(cpa_value, apply_deduction):
        v = copy.deepcopy(rule)
        v.setdefault("conditions", {})["cpa"] = cpa_value
        out = v.setdefault("outcome", {})
        out["qualifying_clauses"] = [
            c for c in (out.get("qualifying_clauses") or []) if _clause_key(c) != dc_key
        ]
        # Deduction only applies when the rule's commission column is the one the
        # clause names (OD). A third-party (TP) or net rule keeps its payout.
        if apply_deduction and ded_col is not None and ded_col == rule_col:
            out["percentage_value"] = deduct_pct(out.get("percentage_value"), deduction)
        return v

    return [variant("with-cpa", False), variant("without-cpa", True)]


def materialize_cpa_split(cics_doc):
    """Expand CPA-deduction rules in-place into With/Without-CPA variants.

    Mutates and returns `cics_doc`. Idempotent: already-split rules carry an
    explicit cpa value and no clause, so a second pass is a no-op.
    """
    if not isinstance(cics_doc, dict):
        return cics_doc
    for product in cics_doc.get("products") or []:
        rules = product.get("commission_rules")
        if not rules:
            continue
        expanded = []
        for rule in rules:
            expanded.extend(_split_rule(rule))
        product["commission_rules"] = expanded
    return cics_doc
