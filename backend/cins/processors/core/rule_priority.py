"""Rule priority normalization.

`RawToCICSTransformer._compute_priorities` assigns unique priorities (10, 20,
30 ...) by specificity, but it runs on the *pre-expansion* raw entry list. Every
downstream fan-out clones a rule and copies the parent's priority verbatim —
`_bifurcate_gcv_wheels`, `_bifurcate_make_buckets`, `_bifurcate_subclass_buckets`,
`cpa_split._split_rule` — and `pipeline._merge_products` concatenates rule sets
from per-sheet transformer runs that each numbered themselves from 10. Nothing
renumbered afterwards, so a single document could carry thousands of
"Duplicate priority N" HIGH findings from `SchemaValidator`.

This module supplies the one renumbering pass, applied at the end of CICS
assembly (after cpa-split and after the cross-sheet merge) so it sees the final
rule list.
"""

from typing import Any

# Same step the transformer uses, so normalized output stays visually familiar
# and leaves room for manual insertion between rules.
PRIORITY_STEP = 10


def normalize_rule_priorities(cics_json: Any) -> Any:
    """Reassign unique, gap-free priorities per product, in place.

    Rules keep their current relative order: the sort key is
    ``(existing priority, original index)``, so the specificity ordering
    established by ``_compute_priorities`` survives and clone siblings stay
    adjacent in their parent's slot. Rules with no priority sort last.

    Idempotent — running it twice is a no-op.
    """
    if not isinstance(cics_json, dict):
        return cics_json

    products = cics_json.get("products")
    if not isinstance(products, list):
        return cics_json

    for product in products:
        if not isinstance(product, dict):
            continue
        rules = product.get("commission_rules")
        if not isinstance(rules, list):
            continue

        indexed = [(i, r) for i, r in enumerate(rules) if isinstance(r, dict)]
        ordered = sorted(indexed, key=lambda pair: (_sort_priority(pair[1]), pair[0]))
        for rank, (_, rule) in enumerate(ordered):
            rule["priority"] = (rank + 1) * PRIORITY_STEP

    return cics_json


def _sort_priority(rule: dict) -> float:
    """Existing priority as a sort key; missing / non-numeric sorts last."""
    priority = rule.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, (int, float)):
        return float("inf")
    return float(priority)
