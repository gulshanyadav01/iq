#!/usr/bin/env python3
"""
CICS Validation CLI

Command-line tool for validating Commission Intelligence Canonical Schema (CICS)
documents. Combines schema validation and rule overlap checking.

Usage:
    python validate.py <cics_document.json>
    python validate.py <cics_document.json> --verbose
    python validate.py <cics_document.json> --schema path/to/schema.json
    python validate.py <cics_document.json> --output report.json

Exit Codes:
    0 - Valid document (no errors)
    1 - Warnings present (medium severity issues)
    2 - Errors present (high or critical issues)
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from schema_validator import SchemaValidator
    from rule_overlap_checker import RuleOverlapChecker, OverlapSeverity
except ImportError:
    print(
        "Error: Cannot import validators. Make sure schema_validator.py and rule_overlap_checker.py are in the same directory."
    )
    sys.exit(1)


class Colors:
    """ANSI color codes for terminal output"""

    RED = "\033[91m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def colorize(text: str, color: str) -> str:
    """Add color to text if terminal supports it"""
    if sys.stdout.isatty():
        return f"{color}{text}{Colors.RESET}"
    return text


def print_header(text: str):
    """Print a section header"""
    print(f"\n{colorize('=' * 70, Colors.BOLD)}")
    print(colorize(f"  {text}", Colors.BOLD))
    print(colorize("=" * 70, Colors.BOLD))


def print_summary(schema_result, overlap_result) -> int:
    """Print validation summary and return exit code"""
    print_header("VALIDATION SUMMARY")

    schema_summary = schema_result.get_summary()
    overlap_summary = overlap_result.get_summary()

    # Determine overall status
    has_critical = schema_result.has_critical or overlap_result.has_critical
    has_high = schema_result.has_high or overlap_result.has_high
    has_medium = len(schema_result.warnings) > 0 or overlap_summary.get("medium", 0) > 0

    if has_critical:
        status = colorize("FAILED", Colors.RED)
        status_icon = "✗"
    elif has_high:
        status = colorize("FAILED (High Priority Issues)", Colors.RED)
        status_icon = "✗"
    elif has_medium:
        status = colorize("PASSED WITH WARNINGS", Colors.YELLOW)
        status_icon = "⚠"
    else:
        status = colorize("PASSED", Colors.GREEN)
        status_icon = "✓"

    print(
        f"\n{colorize(status_icon, Colors.GREEN if not (has_critical or has_high) else Colors.RED)} Overall Status: {status}\n"
    )

    # Schema validation summary
    print(colorize("Schema Validation:", Colors.BOLD))
    print(f"  {colorize('✗', Colors.RED)} Critical: {schema_summary['critical']}")
    print(f"  {colorize('✗', Colors.RED)} High: {schema_summary['high']}")
    print(f"  {colorize('⚠', Colors.YELLOW)} Medium: {schema_summary['medium']}")
    print(f"  {colorize('ℹ', Colors.BLUE)} Low: {schema_summary['low']}")
    print(f"  {colorize('ℹ', Colors.CYAN)} Info: {schema_summary['info']}")

    # Rule overlap summary
    print(f"\n{colorize('Rule Overlap Check:', Colors.BOLD)}")
    print(f"  Total Rules: {overlap_result.total_rules_checked}")
    print(f"  {colorize('✗', Colors.RED)} Critical: {overlap_summary['critical']}")
    print(f"  {colorize('✗', Colors.RED)} High: {overlap_summary['high']}")
    print(f"  {colorize('⚠', Colors.YELLOW)} Medium: {overlap_summary['medium']}")
    print(
        f"  {colorize('ℹ', Colors.CYAN)} Priority-Resolved: {overlap_summary.get('low', 0)}"
    )
    print(f"  {colorize('⚠', Colors.YELLOW)} Coverage Gaps: {overlap_summary['gaps']}")

    # Determine exit code
    if has_critical or has_high:
        return 2
    elif has_medium:
        return 1
    else:
        return 0


def print_issues(schema_result, overlap_result, verbose: bool):
    """Print detailed issues"""

    # Schema errors
    if schema_result.errors:
        print_header("SCHEMA VALIDATION ERRORS")
        for issue in schema_result.errors:
            color = Colors.RED
            icon = "✗"

            print(
                f"\n{colorize(icon, color)} [{issue.severity.value}] {colorize(issue.category, Colors.BOLD)}"
            )
            print(f"   Field: {colorize(issue.field_path, Colors.CYAN)}")
            print(f"   {issue.message}")
            if issue.suggestion:
                print(f"   {colorize('Suggestion:', Colors.BLUE)} {issue.suggestion}")

    # Schema warnings
    if schema_result.warnings:
        print_header("SCHEMA VALIDATION WARNINGS")
        for issue in schema_result.warnings:
            color = Colors.YELLOW
            icon = "⚠"

            print(
                f"\n{colorize(icon, color)} [{issue.severity.value}] {colorize(issue.category, Colors.BOLD)}"
            )
            print(f"   Field: {colorize(issue.field_path, Colors.CYAN)}")
            print(f"   {issue.message}")
            if issue.suggestion:
                print(f"   {colorize('Suggestion:', Colors.BLUE)} {issue.suggestion}")

    # Schema info (only in verbose mode)
    if verbose and schema_result.info:
        print_header("SCHEMA VALIDATION INFO")
        for issue in schema_result.info:
            color = Colors.BLUE
            icon = "ℹ"

            print(
                f"\n{colorize(icon, color)} [{issue.severity.value}] {colorize(issue.category, Colors.BOLD)}"
            )
            print(f"   Field: {colorize(issue.field_path, Colors.CYAN)}")
            print(f"   {issue.message}")
            if issue.suggestion:
                print(f"   {colorize('Suggestion:', Colors.BLUE)} {issue.suggestion}")

    # Rule overlaps
    if overlap_result.overlaps:
        # Critical and High overlaps
        critical_high = [
            o
            for o in overlap_result.overlaps
            if o.severity in [OverlapSeverity.CRITICAL, OverlapSeverity.HIGH]
        ]

        if critical_high:
            print_header("RULE OVERLAP ISSUES")
            for overlap in critical_high:
                color = Colors.RED
                icon = "✗"

                print(
                    f"\n{colorize(icon, color)} [{overlap.severity.value}] {colorize(overlap.overlap_type.upper(), Colors.BOLD)}"
                )
                print(f"   Product: {colorize(overlap.product_code, Colors.CYAN)}")
                print(f"   Rules: {overlap.rule1_id} ↔ {overlap.rule2_id}")
                print(f"   {overlap.description}")
                print(f"   Dimensions: {', '.join(overlap.overlapping_dimensions)}")
                print(f"   {colorize('Suggestion:', Colors.BLUE)} {overlap.suggestion}")

        # Medium overlaps
        medium = [
            o for o in overlap_result.overlaps if o.severity == OverlapSeverity.MEDIUM
        ]
        if medium:
            print_header("RULE OVERLAP WARNINGS")
            for overlap in medium:
                color = Colors.YELLOW
                icon = "⚠"

                print(f"\n{colorize(icon, color)} [{overlap.severity.value}]")
                print(f"   Product: {overlap.product_code}")
                print(f"   Rules: {overlap.rule1_id} ↔ {overlap.rule2_id}")
                print(f"   {overlap.description}")

        # Info overlaps (priority-resolved, only in verbose)
        if verbose:
            info = [
                o
                for o in overlap_result.overlaps
                if o.severity in [OverlapSeverity.LOW, OverlapSeverity.INFO]
            ]
            if info:
                print_header(f"PRIORITY-RESOLVED OVERLAPS ({len(info)} found)")
                print(
                    f"\n{colorize('ℹ', Colors.CYAN)} These overlaps are resolved by priority (lower number wins):"
                )
                for overlap in info[:5]:  # Show first 5
                    print(
                        f"   • {overlap.product_code}: {overlap.rule1_id} ↔ {overlap.rule2_id}"
                    )
                if len(info) > 5:
                    print(f"   ... and {len(info) - 5} more")


def generate_json_report(schema_result, overlap_result, output_path: str):
    """Generate JSON validation report"""
    report = {
        "validation_timestamp": None,  # Could add timestamp
        "overall_status": "valid"
        if (not schema_result.has_critical and not overlap_result.has_critical)
        else "invalid",
        "schema_validation": {
            "summary": schema_result.get_summary(),
            "errors": [
                {
                    "severity": issue.severity.value,
                    "category": issue.category,
                    "field_path": issue.field_path,
                    "message": issue.message,
                    "suggestion": issue.suggestion,
                }
                for issue in schema_result.errors
            ],
            "warnings": [
                {
                    "severity": issue.severity.value,
                    "category": issue.category,
                    "field_path": issue.field_path,
                    "message": issue.message,
                    "suggestion": issue.suggestion,
                }
                for issue in schema_result.warnings
            ],
        },
        "overlap_check": {
            "summary": overlap_result.get_summary(),
            "total_rules": overlap_result.total_rules_checked,
            "overlaps": [
                {
                    "severity": overlap.severity.value,
                    "type": overlap.overlap_type,
                    "product_code": overlap.product_code,
                    "rule1_id": overlap.rule1_id,
                    "rule2_id": overlap.rule2_id,
                    "description": overlap.description,
                    "suggestion": overlap.suggestion,
                    "overlapping_dimensions": overlap.overlapping_dimensions,
                }
                for overlap in overlap_result.overlaps
            ],
        },
    }

    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n{colorize('✓', Colors.GREEN)} JSON report written to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Validate CICS (Commission Intelligence Canonical Schema) documents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python validate.py document.json
  python validate.py document.json --verbose
  python validate.py document.json --schema schema.json
  python validate.py document.json --output report.json

Exit Codes:
  0 - Valid document (no errors)
  1 - Warnings present (medium severity)
  2 - Errors present (high or critical)
        """,
    )

    parser.add_argument("document", help="Path to CICS JSON document to validate")

    parser.add_argument(
        "--schema", help="Path to CICS JSON schema file (optional)", default=None
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed output including info-level issues",
    )

    parser.add_argument(
        "--output", "-o", help="Write JSON report to specified file", default=None
    )

    parser.add_argument(
        "--no-color", action="store_true", help="Disable colored output"
    )

    parser.add_argument(
        "--grid",
        default=None,
        help=(
            "Optional JSON file mapping {sheet_name: markdown} of the source "
            "grid. When supplied, runs the row-coverage validator (asserts "
            "every rate-bearing grid row traces to >=1 emitted rule)."
        ),
    )

    args = parser.parse_args()

    # Disable colors if requested
    if args.no_color:
        for attr in dir(Colors):
            if not attr.startswith("_"):
                setattr(Colors, attr, "")

    # Validate document exists
    if not Path(args.document).exists():
        print(f"{colorize('Error:', Colors.RED)} Document not found: {args.document}")
        sys.exit(2)

    # Print header
    print(
        colorize(
            "\n╔══════════════════════════════════════════════════════════════════╗",
            Colors.BOLD,
        )
    )
    print(
        colorize(
            "║     CICS DOCUMENT VALIDATOR                                      ║",
            Colors.BOLD,
        )
    )
    print(
        colorize(
            "║     Commission Intelligence Canonical Schema v1.0                ║",
            Colors.BOLD,
        )
    )
    print(
        colorize(
            "╚══════════════════════════════════════════════════════════════════╝",
            Colors.BOLD,
        )
    )

    print(f"\n{colorize('Document:', Colors.BOLD)} {args.document}")

    # Run schema validation
    print(f"\n{colorize('⟳', Colors.CYAN)} Running schema validation...")
    schema_validator = SchemaValidator(args.schema)
    schema_result = schema_validator.validate(args.document)

    # Run overlap checking
    print(f"{colorize('⟳', Colors.CYAN)} Running rule overlap check...")
    overlap_checker = RuleOverlapChecker()
    overlap_result = overlap_checker.check(args.document)

    # Optional row-coverage check (needs the source grid markdown).
    if args.grid:
        print(f"{colorize('⟳', Colors.CYAN)} Running row-coverage check...")
        try:
            from row_coverage_validator import RowCoverageValidator
        except ImportError:
            from cins.validators.row_coverage_validator import RowCoverageValidator
        with open(args.grid, "r", encoding="utf-8") as gf:
            sheet_contents = json.load(gf)
        rc_result = RowCoverageValidator().validate(args.document, sheet_contents)
        schema_result.errors.extend(rc_result.errors)
        schema_result.warnings.extend(rc_result.warnings)
        schema_result.info.extend(rc_result.info)
        try:
            from coverage_integrity_validator import CoverageIntegrityValidator
        except ImportError:
            from cins.validators.coverage_integrity_validator import (
                CoverageIntegrityValidator,
            )
        ci_result = CoverageIntegrityValidator().validate(args.document, sheet_contents)
        schema_result.errors.extend(ci_result.errors)
        schema_result.warnings.extend(ci_result.warnings)
        schema_result.info.extend(ci_result.info)

    # Print results
    exit_code = print_summary(schema_result, overlap_result)
    print_issues(schema_result, overlap_result, args.verbose)

    # Generate JSON report if requested
    if args.output:
        generate_json_report(schema_result, overlap_result, args.output)

    # Print recommendations
    if exit_code == 2:
        print_header("RECOMMENDATION")
        print(colorize("✗ REJECT", Colors.RED))
        print("  Critical or high-priority issues detected.")
        print("  Address all errors before using this document in production.")
    elif exit_code == 1:
        print_header("RECOMMENDATION")
        print(colorize("⚠ APPROVE WITH MONITORING", Colors.YELLOW))
        print("  Medium-priority issues detected.")
        print("  Review warnings and monitor behavior in production.")
    else:
        print_header("RECOMMENDATION")
        print(colorize("✓ APPROVE", Colors.GREEN))
        print("  Document is valid and ready for production use.")

    print()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
