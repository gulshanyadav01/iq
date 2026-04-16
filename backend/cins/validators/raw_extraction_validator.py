"""
RawExtraction Validator

Validates RawExtraction before transformation to CICS.
This is Stage 1 validation - checks that raw extraction is usable.
"""

import re
from typing import List, Tuple, Optional
import json

from cins.schema.raw_extraction import RawExtraction, RawCommissionEntry


class RawExtractionValidator:
    """Validates RawExtraction before transformation."""

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate(self, raw: RawExtraction) -> Tuple[bool, List[str], List[str]]:
        """
        Validate a RawExtraction object.

        Args:
            raw: The RawExtraction to validate

        Returns:
            Tuple of (is_valid, errors, warnings)
        """
        self.errors = []
        self.warnings = []

        self._validate_provider(raw)
        self._validate_period(raw)
        self._validate_entries(raw)
        self._validate_master_data(raw)
        self._validate_traceability(raw)
        self._validate_field_length(raw)
        self._validate_repetition(raw)

        return len(self.errors) == 0, self.errors, self.warnings

    def validate_json(self, json_data: dict) -> Tuple[bool, List[str], List[str]]:
        """
        Validate raw extraction from JSON dict.

        Args:
            json_data: JSON dict to validate

        Returns:
            Tuple of (is_valid, errors, warnings)
        """
        try:
            raw = RawExtraction.model_validate(json_data)
            return self.validate(raw)
        except Exception as e:
            return False, [f"Invalid RawExtraction structure: {e}"], []

    def validate_file(self, file_path: str) -> Tuple[bool, List[str], List[str]]:
        """
        Validate raw extraction from JSON file.

        Args:
            file_path: Path to JSON file

        Returns:
            Tuple of (is_valid, errors, warnings)
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            return self.validate_json(json_data)
        except FileNotFoundError:
            return False, [f"File not found: {file_path}"], []
        except json.JSONDecodeError as e:
            return False, [f"Invalid JSON: {e}"], []
        except Exception as e:
            return False, [f"Error reading file: {e}"], []

    def _validate_provider(self, raw: RawExtraction) -> None:
        """Validate provider information."""
        if not raw.provider:
            self.errors.append("Missing provider information")
            return

        if not raw.provider.name:
            self.errors.append("Missing provider name")
        elif len(raw.provider.name.strip()) < 2:
            self.warnings.append(f"Provider name is very short: '{raw.provider.name}'")

    def _validate_period(self, raw: RawExtraction) -> None:
        """Validate commission period."""
        if not raw.start_date:
            self.errors.append("Missing commission period start date")
            return

        # Check if start_date is parsable
        if not self._is_parsable_date(raw.start_date):
            self.warnings.append(
                f"Start date may be unparsable: '{raw.start_date}'. "
                "Expected formats: 'January 1, 2024', '01/01/2024', '2024-01-01'"
            )

        # Check end_date if provided
        if raw.end_date and not self._is_parsable_date(raw.end_date):
            self.warnings.append(f"End date may be unparsable: '{raw.end_date}'")

    def _validate_entries(self, raw: RawExtraction) -> None:
        """Validate commission entries."""
        if not raw.commission_entries or len(raw.commission_entries) == 0:
            self.errors.append("No commission entries extracted")
            return

        for i, entry in enumerate(raw.commission_entries):
            self._validate_entry(entry, i)

    def _validate_entry(self, entry: RawCommissionEntry, index: int) -> None:
        """Validate a single commission entry."""
        prefix = f"Entry {index + 1}"

        # Required fields
        if not entry.commission_value:
            self.errors.append(f"{prefix}: Missing commission value")
        elif not self._is_valid_commission_value(entry.commission_value):
            self.warnings.append(
                f"{prefix}: Commission value may be unparsable: '{entry.commission_value}'"
            )

        if not entry.rule_text:
            self.warnings.append(f"{prefix}: Missing rule_text (verbatim quote)")

        # Check for product identification
        if not entry.product_name and not entry.product_type:
            self.warnings.append(f"{prefix}: No product identification (name or type)")

        # Validate premium range if present
        if entry.premium_range_text:
            if not self._is_valid_premium_range(entry.premium_range_text):
                self.warnings.append(
                    f"{prefix}: Premium range may be unparsable: '{entry.premium_range_text}'"
                )


    def _validate_master_data(self, raw: RawExtraction) -> None:
        """Validate master data entries."""
        if not raw.master_data:
            return

        for i, entry in enumerate(raw.master_data):
            prefix = f"Master data {i + 1}"

            if not entry.entry_type:
                self.warnings.append(f"{prefix}: Missing entry_type")
            elif entry.entry_type not in ['rto_mapping', 'declined', 'vehicle_classification', 'state_mapping', 'general_term']:
                self.warnings.append(f"{prefix}: Unknown entry_type: '{entry.entry_type}'")

            if not entry.key:
                self.warnings.append(f"{prefix}: Missing key")

    def _validate_traceability(self, raw: RawExtraction) -> None:
        """Validate source traceability."""
        if not raw.commission_entries:
            return

        # Check if all entries lack source info
        entries_with_source = sum(
            1 for e in raw.commission_entries
            if e.source_section or e.quoted_text
        )

        if entries_with_source == 0:
            self.warnings.append(
                "No source traceability information in any entry. "
                "Consider adding source_section or quoted_text."
            )
        elif entries_with_source < len(raw.commission_entries) * 0.5:
            self.warnings.append(
                f"Only {entries_with_source}/{len(raw.commission_entries)} entries have source traceability"
            )

    def _validate_field_length(self, raw: RawExtraction) -> None:
        """Validate that condition fields don't exceed reasonable lengths (sign of context bloat)."""
        MAX_LENGTHS = {
            'rule_text': 500,
            'state_text': 1000,
            'channel_text': 80,
            'customer_type_text': 60,
            'coverage_type_text': 80,
            'premium_range_text': 100,
            'other_conditions': 1000,
            'applied_on_text': 50,
            'quoted_text': 300,
        }

        for i, entry in enumerate(raw.commission_entries):
            prefix = f"Entry {i + 1}"
            for field_name, max_len in MAX_LENGTHS.items():
                field_value = getattr(entry, field_name, None)
                if field_value and len(field_value) > max_len:
                    self.warnings.append(
                        f"{prefix}: {field_name} is too long ({len(field_value)} chars, max {max_len}). "
                        f"May contain extraneous context. First 50 chars: '{field_value[:50]}...'"
                    )

    def _validate_repetition(self, raw: RawExtraction) -> None:
        """Detect excessive repetition within fields (sign of AI hallucination/loop)."""
        for i, entry in enumerate(raw.commission_entries):
            prefix = f"Entry {i + 1}"

            # Check for repetitive patterns in condition fields
            condition_fields = {
                'state_text': entry.state_text,
                'channel_text': entry.channel_text,
                'coverage_type_text': entry.coverage_type_text,
                'other_conditions': entry.other_conditions,
            }

            for field_name, field_value in condition_fields.items():
                if not field_value or len(field_value) < 100:
                    continue

                # Check for repeated phrases (sign of AI loop)
                words = field_value.split()
                if len(words) > 20:
                    # Look for repeated 5-word sequences
                    sequences = [' '.join(words[j:j+5]) for j in range(len(words) - 4)]
                    unique_sequences = set(sequences)
                    if len(sequences) > 0 and len(unique_sequences) / len(sequences) < 0.5:
                        self.warnings.append(
                            f"{prefix}: {field_name} contains repetitive text patterns (AI loop suspected). "
                            f"First 50 chars: '{field_value[:50]}...'"
                        )

                # Check for dates/temporal info in non-date fields
                date_patterns = ['2024', '2025', '2026', 'effective', 'from', 'until', 'valid']
                date_count = sum(1 for p in date_patterns if p.lower() in field_value.lower())
                if date_count >= 3 and field_name not in ['other_conditions']:
                    self.warnings.append(
                        f"{prefix}: {field_name} contains temporal/date info that may belong in date fields. "
                        f"First 50 chars: '{field_value[:50]}...'"
                    )

    def _is_parsable_date(self, text: str) -> bool:
        """Check if a date string looks parsable."""
        if not text:
            return False

        text = text.strip()

        # Check common patterns
        patterns = [
            r'^\d{4}-\d{2}-\d{2}$',  # 2024-01-01
            r'^\d{1,2}/\d{1,2}/\d{4}$',  # 01/01/2024
            r'^\d{1,2}-\d{1,2}-\d{4}$',  # 01-01-2024
            r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)',  # Month name
            r'\d{1,2}(?:st|nd|rd|th)?\s+\w+\s+\d{4}',  # 1st January 2024
            r'\w+\s+\d{1,2},?\s+\d{4}',  # January 1, 2024
        ]

        text_lower = text.lower()
        for pattern in patterns:
            if re.search(pattern, text_lower):
                return True

        return False

    def _is_valid_commission_value(self, text: str) -> bool:
        """Check if a commission value looks valid."""
        if not text:
            return False

        text_lower = text.lower().strip()

        # Common valid patterns
        patterns = [
            r'\d+(?:\.\d+)?\s*%',  # 12% or 12.5%
            r'^0?\.\d+$',  # Plain decimal like 0.26 or .26 (26%)
            r'^\d+(?:\.\d+)?$',  # Plain number like 26 or 26.5
            r'(?:rs\.?|inr|₹)\s*\d+',  # Rs. 500, INR 500, ₹500
            r'\d+\s*[-–to]+\s*\d+\s*:\s*\d+\s*%',  # 0-25K: 10%
            r'tiered',  # tiered structure mentioned
        ]

        for pattern in patterns:
            if re.search(pattern, text_lower):
                return True

        return False

    def _is_valid_premium_range(self, text: str) -> bool:
        """Check if a premium range looks valid."""
        if not text:
            return False

        text_lower = text.lower().strip()

        # Common valid patterns
        patterns = [
            r'\d+\s*[-–to]+\s*\d+',  # 0-25000 or 0 to 25000
            r'(?:above|>|greater than)\s*\d+',  # above 50000
            r'(?:up to|upto|below|<|under)\s*\d+',  # up to 25000
            r'\d+\s*[kK]',  # 25K
            r'(?:rs\.?|inr|₹)\s*\d+',  # Rs. 50000
        ]

        for pattern in patterns:
            if re.search(pattern, text_lower):
                return True

        return False

    def _is_valid_percentage(self, text: str) -> bool:
        """Check if a percentage value looks valid."""
        if not text:
            return False

        # Should contain a number
        if re.search(r'\d+(?:\.\d+)?', text):
            return True

        return False


def validate_raw_extraction(raw: RawExtraction) -> Tuple[bool, List[str], List[str]]:
    """
    Validate a RawExtraction object.

    Args:
        raw: The RawExtraction to validate

    Returns:
        Tuple of (is_valid, errors, warnings)
    """
    validator = RawExtractionValidator()
    return validator.validate(raw)


def validate_raw_extraction_file(file_path: str) -> Tuple[bool, List[str], List[str]]:
    """
    Validate a raw extraction from JSON file.

    Args:
        file_path: Path to JSON file

    Returns:
        Tuple of (is_valid, errors, warnings)
    """
    validator = RawExtractionValidator()
    return validator.validate_file(file_path)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate RawExtraction JSON files"
    )
    parser.add_argument(
        "file",
        help="Path to RawExtraction JSON file"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output"
    )

    args = parser.parse_args()

    is_valid, errors, warnings = validate_raw_extraction_file(args.file)

    # Print results
    if is_valid:
        print(f"✓ Valid: {args.file}")
    else:
        print(f"✗ Invalid: {args.file}")

    if errors:
        print("\nErrors:")
        for error in errors:
            print(f"  ✗ {error}")

    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"  ⚠ {warning}")

    if args.verbose:
        print(f"\nSummary: {len(errors)} errors, {len(warnings)} warnings")

    # Exit code
    sys.exit(0 if is_valid else 1)


if __name__ == "__main__":
    main()
