"""
CICS Rule Overlap Checker

Detects overlapping commission rules within products to prevent ambiguous
commission calculations.

Detects COMPLETE overlaps only — two rules in the same product whose conditions
are identical, so no input can distinguish them.

Partial (merely intersecting) overlaps are NOT reported. Rules are evaluated
lowest-priority-number-first and priorities are unique per product by
construction (processors/core/rule_priority.py), so an intersection always
resolves deterministically. Reporting them was pure noise: one Chola document
produced 7192 partial overlaps against 20 genuine duplicates.

Also reports coverage gaps: scenarios not covered by any rule.

Usage:
    from rule_overlap_checker import RuleOverlapChecker

    checker = RuleOverlapChecker()
    result = checker.check('path/to/document.json')

    for overlap in result.overlaps:
        print(f"{overlap.severity}: {overlap.description}")
"""

import json
from typing import List, Dict, Any
from dataclasses import dataclass
from enum import Enum


# Sentinels for how many sides of a pair constrain a given dimension. A plain
# enum would be overkill; these are only ever compared with `is`.
_NEITHER = "neither"
_ONE_SIDE = "one-side"
_BOTH = "both"

# Range condition keys folded to the dimension label used in `overlapping_dims`.
_RANGE_KEY_TO_LABEL = {
    "gvw_start_range": "gvw",
    "gvw_end_range": "gvw",
    # The NCB band folds to the SAME label as the ``ncb`` boolean, on purpose.
    # _constrained_dims builds a set and the caller compares
    # ``set(overlapping_dims) == all_dims``; a separate label would let a rule
    # carrying both the bool and the band register two dims while
    # overlapping_dims records one, silently breaking that equality. Sharing the
    # label makes the duplicate append (once from single_fields, once from
    # range_pairs) harmless.
    "ncb_start_range": "ncb",
    "ncb_end_range": "ncb",
    "cc_start_range": "cc",
    "cc_end_range": "cc",
    "seating_capacity_start_range": "seating_capacity",
    "seating_capacity_end_range": "seating_capacity",
    "policies_count_start_range": "policies_count",
    "policies_count_end_range": "policies_count",
}


class OverlapSeverity(Enum):
    """Severity of rule overlap"""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass
class RuleOverlap:
    """Represents an overlap between rules"""

    severity: OverlapSeverity
    overlap_type: str
    product_code: str
    rule1_id: str
    rule2_id: str
    description: str
    suggestion: str
    overlapping_dimensions: List[str]


@dataclass
class OverlapCheckResult:
    """Result of overlap checking"""

    overlaps: List[RuleOverlap]
    gaps: List[Dict[str, Any]]
    total_rules_checked: int

    @property
    def has_critical(self) -> bool:
        return any(o.severity == OverlapSeverity.CRITICAL for o in self.overlaps)

    @property
    def has_high(self) -> bool:
        return any(o.severity == OverlapSeverity.HIGH for o in self.overlaps)

    def get_summary(self) -> Dict[str, int]:
        return {
            "critical": sum(
                1 for o in self.overlaps if o.severity == OverlapSeverity.CRITICAL
            ),
            "high": sum(1 for o in self.overlaps if o.severity == OverlapSeverity.HIGH),
            "medium": sum(
                1 for o in self.overlaps if o.severity == OverlapSeverity.MEDIUM
            ),
            "low": sum(1 for o in self.overlaps if o.severity == OverlapSeverity.LOW),
            "gaps": len(self.gaps),
        }


class RuleOverlapChecker:
    """Checks for overlapping commission rules"""

    def __init__(self):
        self.overlaps: List[RuleOverlap] = []
        self.gaps: List[Dict[str, Any]] = []
        self.current_doc = None

    def check(self, document_path: str) -> OverlapCheckResult:
        """
        Check for rule overlaps in a CICS document

        Args:
            document_path: Path to CICS JSON document

        Returns:
            OverlapCheckResult with detected overlaps and gaps
        """
        self.overlaps = []
        self.gaps = []

        # Load document
        try:
            with open(document_path, "r") as f:
                self.current_doc = json.load(f)
        except Exception:
            return OverlapCheckResult(overlaps=[], gaps=[], total_rules_checked=0)

        total_rules = 0

        # Check each product
        if "products" in self.current_doc:
            for product in self.current_doc["products"]:
                if "commission_rules" not in product:
                    continue

                rules = product["commission_rules"]
                total_rules += len(rules)
                product_code = product.get("product_code", "unknown")

                # Check all rule pairs for overlaps
                for i in range(len(rules)):
                    for j in range(i + 1, len(rules)):
                        self._check_rule_pair(product_code, rules[i], rules[j])

        return OverlapCheckResult(
            overlaps=self.overlaps, gaps=self.gaps, total_rules_checked=total_rules
        )

    def _check_rule_pair(self, product_code: str, rule1: Dict, rule2: Dict):
        """Report a pair only when it is a COMPLETE overlap (identical conditions).

        Priority is deliberately not consulted. Rules resolve
        lowest-priority-number-first and priorities are unique per product by
        construction, so partial overlaps always resolve deterministically and
        are not defects; identical conditions are a duplicate no matter which
        rule wins.
        """
        rule1_id = rule1.get("rule_id", "unknown")
        rule2_id = rule2.get("rule_id", "unknown")

        conditions1 = rule1.get("conditions") or {}
        conditions2 = rule2.get("conditions") or {}

        # Check for overlap in each dimension
        overlapping_dims = []
        has_overlap = True

        # If both rules have no conditions, they completely overlap
        if not conditions1 and not conditions2:
            self.overlaps.append(
                RuleOverlap(
                    # Severity is deliberately NOT priority-gated: priorities are
                    # renumbered to be unique per product
                    # (processors/core/rule_priority.py), so "different priority"
                    # no longer carries any signal about intent. Two condition-less
                    # rules are a genuine duplicate however they are numbered.
                    severity=OverlapSeverity.CRITICAL,
                    overlap_type="complete_overlap",
                    product_code=product_code,
                    rule1_id=rule1_id,
                    rule2_id=rule2_id,
                    description="Both rules have no conditions (default/catch-all rules)",
                    suggestion="Only one default rule per product recommended.",
                    overlapping_dimensions=["all"],
                )
            )
            return

        # List fields checked via set overlap
        list_fields = [
            "vehicle_sub_class",
            "policy_type",
            "state",
            "rto",
            "number_of_wheels",
            "school_bus_type",
            "booking_mode",
            "fuel_type",
            "vehicle_make",
            "vehicle_model",
            "vehicle_carrier",
            "business_type",
            "deal_parameter",
        ]
        for field in list_fields:
            constrained = self._constraint_state(conditions1, conditions2, field)
            if constrained is _NEITHER:
                continue
            if constrained is _ONE_SIDE:
                overlapping_dims.append(field)
                continue
            if self._sets_overlap(conditions1[field], conditions2[field]):
                overlapping_dims.append(field)
            else:
                has_overlap = False

        # Make/model exclusion disjointness. The GCCV ≤3.5T buckets (a)/(b)/(c)
        # differ only by include vs exclude make/model lists, so the include-
        # only check above never sees them as disjoint. When one rule's include
        # list is a SUBSET of the other's exclude list they can never co-match
        # — mark them disjoint so the buckets don't flag a HIGH overlap on
        # every doc. Checked in both directions and for both make and model.
        for inc_field, exc_field in (
            ("vehicle_make", "vehicle_make_exclude"),
            ("vehicle_model", "vehicle_model_exclude"),
        ):
            if self._include_excluded_by(
                conditions1, conditions2, inc_field, exc_field
            ) or self._include_excluded_by(
                conditions2, conditions1, inc_field, exc_field
            ):
                has_overlap = False

        # Single-value fields checked via equality
        single_fields = ["zero_dep", "cpa", "ncb", "vehicle_age", "od_term", "tp_term"]
        for field in single_fields:
            constrained = self._constraint_state(conditions1, conditions2, field)
            if constrained is _NEITHER:
                continue
            if constrained is _ONE_SIDE:
                overlapping_dims.append(field)
                continue
            if conditions1[field] == conditions2[field]:
                overlapping_dims.append(field)
            else:
                has_overlap = False

        # Range fields checked via range overlap
        range_pairs = [
            ("gvw_start_range", "gvw_end_range", "gvw"),
            ("cc_start_range", "cc_end_range", "cc"),
            # Label shared with the ``ncb`` boolean in single_fields above — see
            # _RANGE_KEY_TO_LABEL for why they must not diverge.
            ("ncb_start_range", "ncb_end_range", "ncb"),
            (
                "seating_capacity_start_range",
                "seating_capacity_end_range",
                "seating_capacity",
            ),
            (
                "policies_count_start_range",
                "policies_count_end_range",
                "policies_count",
            ),
        ]
        for start_key, end_key, label in range_pairs:
            bound1 = self._is_range_constrained(conditions1, start_key, end_key)
            bound2 = self._is_range_constrained(conditions2, start_key, end_key)
            if not bound1 and not bound2:
                continue
            if not bound1 or not bound2:
                overlapping_dims.append(label)
                continue
            range1 = {
                "min": conditions1.get(start_key),
                "max": conditions1.get(end_key),
            }
            range2 = {
                "min": conditions2.get(start_key),
                "max": conditions2.get(end_key),
            }
            if self._ranges_overlap(range1, range2):
                overlapping_dims.append(label)
            else:
                has_overlap = False

        # If we found overlaps in all checked dimensions
        if has_overlap and overlapping_dims:
            # Determine severity and type. Only CONSTRAINED dimensions count: the
            # CICS document is dumped with exclude_none=False, so every rule
            # carries all ~25 condition keys with most set to null. Unioning raw
            # .keys() made all_dims the full field list, so
            # `set(overlapping_dims) == all_dims` could never hold and a genuine
            # duplicate could only ever be reported as a partial overlap.
            all_dims = self._constrained_dims(conditions1) | self._constrained_dims(
                conditions2
            )

            # PARTIAL overlaps are not reported at all. Rules are evaluated
            # lowest-priority-number-first and priorities are unique per product
            # by construction (processors/core/rule_priority.py), so a partial
            # overlap is always resolved deterministically — it was already only
            # ever emitted as INFO "priority-resolved (lower number wins)".
            # Reporting them buried the real findings: one Chola document
            # produced 7192 partial overlaps against 20 genuine duplicates.
            if not (
                set(overlapping_dims) == all_dims
                and self._conditions_identical(conditions1, conditions2)
            ):
                return

            # Complete overlap — identical conditions. Not priority-gated: with
            # unique priorities, "different priority" says nothing about intent,
            # and two rules matching exactly the same input are a real duplicate
            # whichever one wins.
            self.overlaps.append(
                RuleOverlap(
                    severity=OverlapSeverity.CRITICAL,
                    overlap_type="complete_overlap",
                    product_code=product_code,
                    rule1_id=rule1_id,
                    rule2_id=rule2_id,
                    description="Rules have identical conditions",
                    suggestion="Remove duplicate rule",
                    overlapping_dimensions=overlapping_dims,
                )
            )

    def _ranges_overlap(self, range1: Dict, range2: Dict) -> bool:
        """Check if two numeric ranges overlap"""
        # Handle None values
        if range1 is None or range2 is None:
            return False

        min1 = range1.get("min", float("-inf"))
        max1 = range1.get("max", float("inf"))
        min2 = range2.get("min", float("-inf"))
        max2 = range2.get("max", float("inf"))

        # Handle None values in min/max
        if min1 is None:
            min1 = float("-inf")
        if max1 is None:
            max1 = float("inf")
        if min2 is None:
            min2 = float("-inf")
        if max2 is None:
            max2 = float("inf")

        # Ranges overlap if they have any intersection
        return not (max1 < min2 or max2 < min1)

    def _sets_overlap(self, set1: List, set2: List) -> bool:
        """Check if two sets (arrays) have any common elements"""
        if not isinstance(set1, list) or not isinstance(set2, list):
            return False
        return bool(set(set1) & set(set2))

    @staticmethod
    def _constraint_state(cond1: Dict, cond2: Dict, field: str):
        """Classify how many sides actually constrain ``field``.

        A null/absent condition means "unconstrained — matches everything", NOT
        "matches nothing". Treating null as a value made ``_sets_overlap(None,
        None)`` return False, which set ``has_overlap = False`` and declared the
        pair disjoint. Combined with the exclude_none=False dump (every rule
        carries all ~25 keys, most null) that silently disabled the checker in
        production: a 3927-rule document reported zero overlaps while holding
        134 pairs of identical-condition rules with different payouts.
        """
        v1 = cond1.get(field)
        v2 = cond2.get(field)
        if v1 is None and v2 is None:
            return _NEITHER
        if v1 is None or v2 is None:
            # The unconstrained rule subsumes the constrained one on this
            # dimension, so they do overlap here.
            return _ONE_SIDE
        return _BOTH

    @staticmethod
    def _is_range_constrained(cond: Dict, start_key: str, end_key: str) -> bool:
        """True when either bound of a range is actually set."""
        return cond.get(start_key) is not None or cond.get(end_key) is not None

    @classmethod
    def _constrained_dims(cls, cond: Dict) -> set:
        """Dimension names the rule actually constrains.

        Range pairs collapse to their label ("gvw_start_range"/"gvw_end_range" ->
        "gvw") so this set is directly comparable with ``overlapping_dims``,
        which records ranges by label.
        """
        dims = set()
        for key, value in cond.items():
            if value is None:
                continue
            dims.add(_RANGE_KEY_TO_LABEL.get(key, key))
        return dims

    def _include_excluded_by(
        self, cond_a: Dict, cond_b: Dict, inc_field: str, exc_field: str
    ) -> bool:
        """True when cond_a's include list is fully excluded by cond_b's exclude
        list (set(a[inc]) ⊆ set(b[exc])) — the two rules can never co-match on
        that dimension. None-safe: returns False when either list is absent."""
        inc = cond_a.get(inc_field)
        exc = cond_b.get(exc_field)
        if not inc or not exc:
            return False
        if not isinstance(inc, list) or not isinstance(exc, list):
            return False
        return set(inc) <= set(exc)

    def _conditions_identical(self, cond1: Dict, cond2: Dict) -> bool:
        """Check if two condition objects are identical.

        Compares CONSTRAINED keys only. Under exclude_none=False both dicts
        carry every field, so a raw key-set comparison would call two rules
        identical purely because they share the schema; and under a sparse dict
        an explicit ``{"state": None}`` would differ from an omitted "state"
        even though both mean "unconstrained".
        """
        keys1 = {k for k, v in cond1.items() if v is not None}
        keys2 = {k for k, v in cond2.items() if v is not None}
        if keys1 != keys2:
            return False

        for key in keys1:
            if isinstance(cond1[key], list) and isinstance(cond2[key], list):
                # Compare arrays
                if set(cond1[key]) != set(cond2[key]):
                    return False
            else:
                # Direct comparison
                if cond1[key] != cond2[key]:
                    return False

        return True

    def check_tier_overlaps(self, tiers: List[Dict]) -> List[str]:
        """
        Check for overlapping tier thresholds

        Returns:
            List of error messages
        """
        errors = []

        if not tiers or len(tiers) < 2:
            return errors

        for i in range(len(tiers) - 1):
            tier1 = tiers[i]
            tier2 = tiers[i + 1]

            threshold1 = tier1.get("threshold", {})
            threshold2 = tier2.get("threshold", {})

            if self._ranges_overlap(threshold1, threshold2):
                min1 = threshold1.get("min", 0)
                max1 = threshold1.get("max", "∞")
                min2 = threshold2.get("min", 0)
                max2 = threshold2.get("max", "∞")

                errors.append(
                    f"Tier {i + 1} ({min1}-{max1}) overlaps with Tier {i + 2} ({min2}-{max2})"
                )

        # Check for gaps
        for i in range(len(tiers) - 1):
            tier1 = tiers[i]
            tier2 = tiers[i + 1]

            threshold1 = tier1.get("threshold", {})
            threshold2 = tier2.get("threshold", {})

            max1 = threshold1.get("max")
            min2 = threshold2.get("min")

            if max1 is not None and min2 is not None:
                if max1 + 1 < min2:
                    errors.append(
                        f"Gap between Tier {i + 1} (max: {max1}) and Tier {i + 2} (min: {min2}): range {max1 + 1}-{min2 - 1} not covered"
                    )

        return errors


def main():
    """Example usage"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python rule_overlap_checker.py <cics_document.json>")
        sys.exit(1)

    checker = RuleOverlapChecker()
    result = checker.check(sys.argv[1])

    print("\n=== RULE OVERLAP CHECK RESULTS ===\n")

    summary = result.get_summary()
    print(f"Total Rules Checked: {result.total_rules_checked}")
    print(f"Critical Overlaps: {summary['critical']}")
    print(f"High Priority Overlaps: {summary['high']}")
    print(f"Medium Priority Overlaps: {summary['medium']}")
    print(f"Info/Priority-Resolved: {summary.get('low', 0) + summary.get('info', 0)}")
    print(f"Coverage Gaps: {summary['gaps']}")

    if result.overlaps:
        print("\n=== DETECTED OVERLAPS ===\n")

        # Group by severity
        critical = [
            o for o in result.overlaps if o.severity == OverlapSeverity.CRITICAL
        ]
        high = [o for o in result.overlaps if o.severity == OverlapSeverity.HIGH]
        medium = [o for o in result.overlaps if o.severity == OverlapSeverity.MEDIUM]
        info = [o for o in result.overlaps if o.severity == OverlapSeverity.INFO]

        if critical:
            print("CRITICAL OVERLAPS:")
            for overlap in critical:
                print(f"\n  Product: {overlap.product_code}")
                print(f"  Rules: {overlap.rule1_id} ↔ {overlap.rule2_id}")
                print(f"  Type: {overlap.overlap_type}")
                print(f"  Description: {overlap.description}")
                print(
                    f"  Overlapping Dimensions: {', '.join(overlap.overlapping_dimensions)}"
                )
                print(f"  Suggestion: {overlap.suggestion}")

        if high:
            print("\nHIGH PRIORITY OVERLAPS:")
            for overlap in high:
                print(f"\n  Product: {overlap.product_code}")
                print(f"  Rules: {overlap.rule1_id} ↔ {overlap.rule2_id}")
                print(f"  Description: {overlap.description}")
                print(f"  Suggestion: {overlap.suggestion}")

        if medium:
            print("\nMEDIUM PRIORITY OVERLAPS:")
            for overlap in medium:
                print(f"\n  Product: {overlap.product_code}")
                print(f"  Rules: {overlap.rule1_id} ↔ {overlap.rule2_id}")
                print(f"  Description: {overlap.description}")

        if info:
            print(f"\nINFO: {len(info)} priority-resolved overlaps (expected behavior)")

    if result.gaps:
        print("\n=== COVERAGE GAPS ===\n")
        for gap in result.gaps:
            print(f"  {gap}")

    exit_code = 0
    if result.has_critical:
        exit_code = 2
    elif result.has_high:
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
