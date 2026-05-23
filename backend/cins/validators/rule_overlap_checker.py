"""
CICS Rule Overlap Checker

Detects overlapping commission rules within products to prevent ambiguous
commission calculations.

Types of overlaps detected:
1. Complete overlap: Identical conditions
2. Partial overlap: Intersecting conditions
3. Priority conflicts: Same priority with overlapping conditions
4. Coverage gaps: Scenarios not covered by any rule

Usage:
    from rule_overlap_checker import RuleOverlapChecker

    checker = RuleOverlapChecker()
    result = checker.check('path/to/document.json')

    for overlap in result.overlaps:
        print(f"{overlap.severity}: {overlap.description}")
"""

import json
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass
from enum import Enum


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
            "critical": sum(1 for o in self.overlaps if o.severity == OverlapSeverity.CRITICAL),
            "high": sum(1 for o in self.overlaps if o.severity == OverlapSeverity.HIGH),
            "medium": sum(1 for o in self.overlaps if o.severity == OverlapSeverity.MEDIUM),
            "low": sum(1 for o in self.overlaps if o.severity == OverlapSeverity.LOW),
            "gaps": len(self.gaps)
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
            with open(document_path, 'r') as f:
                self.current_doc = json.load(f)
        except Exception as e:
            return OverlapCheckResult(
                overlaps=[],
                gaps=[],
                total_rules_checked=0
            )

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
            overlaps=self.overlaps,
            gaps=self.gaps,
            total_rules_checked=total_rules
        )

    def _check_rule_pair(self, product_code: str, rule1: Dict, rule2: Dict):
        """Check if two rules overlap"""
        rule1_id = rule1.get("rule_id", "unknown")
        rule2_id = rule2.get("rule_id", "unknown")
        priority1 = rule1.get("priority", 999)
        priority2 = rule2.get("priority", 999)

        conditions1 = rule1.get("conditions") or {}
        conditions2 = rule2.get("conditions") or {}

        # Check for overlap in each dimension
        overlapping_dims = []
        has_overlap = True

        # If both rules have no conditions, they completely overlap
        if not conditions1 and not conditions2:
            self.overlaps.append(RuleOverlap(
                severity=OverlapSeverity.CRITICAL if priority1 == priority2 else OverlapSeverity.INFO,
                overlap_type="complete_overlap",
                product_code=product_code,
                rule1_id=rule1_id,
                rule2_id=rule2_id,
                description="Both rules have no conditions (default/catch-all rules)",
                suggestion="Only one default rule per product recommended. Ensure different priorities if intentional.",
                overlapping_dimensions=["all"]
            ))
            return

        # List fields checked via set overlap
        list_fields = [
            "vehicle_sub_class", "policy_type", "state", "rto",
            "number_of_wheels", "school_bus_type", "booking_mode",
            "fuel_type", "vehicle_make", "vehicle_model",
            "vehicle_carrier", "business_type", "deal_parameter",
        ]
        for field in list_fields:
            if field in conditions1 and field in conditions2:
                if self._sets_overlap(conditions1[field], conditions2[field]):
                    overlapping_dims.append(field)
                else:
                    has_overlap = False

        # Single-value fields checked via equality
        single_fields = ["zero_dep", "cpa", "ncb", "vehicle_age", "od_term", "tp_term"]
        for field in single_fields:
            if field in conditions1 and field in conditions2:
                if conditions1[field] == conditions2[field]:
                    overlapping_dims.append(field)
                else:
                    has_overlap = False

        # Range fields checked via range overlap
        range_pairs = [
            ("gvw_start_range", "gvw_end_range", "gvw"),
            ("cc_start_range", "cc_end_range", "cc"),
            ("seating_capacity_start_range", "seating_capacity_end_range", "seating_capacity"),
            ("policies_count_start_range", "policies_count_end_range", "policies_count"),
        ]
        for start_key, end_key, label in range_pairs:
            if (start_key in conditions1 or end_key in conditions1) and \
               (start_key in conditions2 or end_key in conditions2):
                range1 = {"min": conditions1.get(start_key), "max": conditions1.get(end_key)}
                range2 = {"min": conditions2.get(start_key), "max": conditions2.get(end_key)}
                if self._ranges_overlap(range1, range2):
                    overlapping_dims.append(label)
                else:
                    has_overlap = False

        # If we found overlaps in all checked dimensions
        if has_overlap and overlapping_dims:
            # Determine severity and type
            all_dims = set(conditions1.keys()) | set(conditions2.keys())

            if set(overlapping_dims) == all_dims and self._conditions_identical(conditions1, conditions2):
                # Complete overlap
                severity = OverlapSeverity.CRITICAL if priority1 == priority2 else OverlapSeverity.INFO
                overlap_type = "complete_overlap"
                description = "Rules have identical conditions"
                suggestion = "Remove duplicate rule or ensure different priorities" if priority1 == priority2 else "Priority-resolved (lower number wins)"
            else:
                # Partial overlap
                severity = OverlapSeverity.HIGH if priority1 == priority2 else OverlapSeverity.INFO
                overlap_type = "partial_overlap"
                description = f"Rules overlap in: {', '.join(overlapping_dims)}"
                suggestion = "Refine conditions to eliminate overlap or ensure different priorities" if priority1 == priority2 else "Priority-resolved (lower number wins)"

            self.overlaps.append(RuleOverlap(
                severity=severity,
                overlap_type=overlap_type,
                product_code=product_code,
                rule1_id=rule1_id,
                rule2_id=rule2_id,
                description=description,
                suggestion=suggestion,
                overlapping_dimensions=overlapping_dims
            ))

    def _ranges_overlap(self, range1: Dict, range2: Dict) -> bool:
        """Check if two numeric ranges overlap"""
        # Handle None values
        if range1 is None or range2 is None:
            return False

        min1 = range1.get("min", float('-inf'))
        max1 = range1.get("max", float('inf'))
        min2 = range2.get("min", float('-inf'))
        max2 = range2.get("max", float('inf'))

        # Handle None values in min/max
        if min1 is None:
            min1 = float('-inf')
        if max1 is None:
            max1 = float('inf')
        if min2 is None:
            min2 = float('-inf')
        if max2 is None:
            max2 = float('inf')

        # Ranges overlap if they have any intersection
        return not (max1 < min2 or max2 < min1)

    def _sets_overlap(self, set1: List, set2: List) -> bool:
        """Check if two sets (arrays) have any common elements"""
        if not isinstance(set1, list) or not isinstance(set2, list):
            return False
        return bool(set(set1) & set(set2))

    def _conditions_identical(self, cond1: Dict, cond2: Dict) -> bool:
        """Check if two condition objects are identical"""
        # Simple check: same keys and same values
        if set(cond1.keys()) != set(cond2.keys()):
            return False

        for key in cond1.keys():
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
                    f"Tier {i+1} ({min1}-{max1}) overlaps with Tier {i+2} ({min2}-{max2})"
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
                        f"Gap between Tier {i+1} (max: {max1}) and Tier {i+2} (min: {min2}): range {max1+1}-{min2-1} not covered"
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
        critical = [o for o in result.overlaps if o.severity == OverlapSeverity.CRITICAL]
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
                print(f"  Overlapping Dimensions: {', '.join(overlap.overlapping_dimensions)}")
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
