"""
CICS Schema Validator

Validates Commission Intelligence Canonical Schema (CICS) documents against:
1. JSON Schema structure and types
2. Business rules and logic
3. Data quality and completeness

Usage:
    from schema_validator import SchemaValidator

    validator = SchemaValidator('path/to/schema.json')
    result = validator.validate('path/to/document.json')

    if result.is_valid:
        print("Document is valid!")
    else:
        for error in result.errors:
            print(f"{error.severity}: {error.message}")
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    """Issue severity levels"""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass
class ValidationIssue:
    """Represents a validation issue"""
    severity: Severity
    message: str
    field_path: str
    category: str
    suggestion: Optional[str] = None


@dataclass
class ValidationResult:
    """Result of validation"""
    is_valid: bool
    errors: List[ValidationIssue]
    warnings: List[ValidationIssue]
    info: List[ValidationIssue]

    @property
    def has_critical(self) -> bool:
        return any(e.severity == Severity.CRITICAL for e in self.errors)

    @property
    def has_high(self) -> bool:
        return any(e.severity == Severity.HIGH for e in self.errors)

    def get_summary(self) -> Dict[str, int]:
        """Get count of issues by severity"""
        all_issues = self.errors + self.warnings + self.info
        return {
            "critical": sum(1 for i in all_issues if i.severity == Severity.CRITICAL),
            "high": sum(1 for i in all_issues if i.severity == Severity.HIGH),
            "medium": sum(1 for i in all_issues if i.severity == Severity.MEDIUM),
            "low": sum(1 for i in all_issues if i.severity == Severity.LOW),
            "info": sum(1 for i in all_issues if i.severity == Severity.INFO),
        }


class SchemaValidator:
    """Validates CICS documents against schema and business rules"""

    def __init__(self, schema_path: Optional[str] = None):
        """
        Initialize validator

        Args:
            schema_path: Path to CICS JSON schema file (optional)
        """
        self.schema = None
        if schema_path:
            self.load_schema(schema_path)

        self.issues: List[ValidationIssue] = []
        self.current_doc = None

    def load_schema(self, schema_path: str):
        """Load JSON schema from file"""
        with open(schema_path, 'r') as f:
            self.schema = json.load(f)

    def validate(self, document_path: str) -> ValidationResult:
        """
        Validate a CICS document

        Args:
            document_path: Path to CICS JSON document

        Returns:
            ValidationResult with errors, warnings, and info
        """
        self.issues = []

        # Load document
        try:
            with open(document_path, 'r') as f:
                self.current_doc = json.load(f)
        except json.JSONDecodeError as e:
            self.issues.append(ValidationIssue(
                severity=Severity.CRITICAL,
                message=f"Invalid JSON: {str(e)}",
                field_path="root",
                category="JSON Structure"
            ))
            return self._build_result()
        except Exception as e:
            self.issues.append(ValidationIssue(
                severity=Severity.CRITICAL,
                message=f"Failed to load document: {str(e)}",
                field_path="root",
                category="File Loading"
            ))
            return self._build_result()

        # Run validations
        self._validate_structure()
        self._validate_required_fields()
        self._validate_field_types()
        self._validate_enums()
        self._validate_formats()
        self._validate_business_rules()
        self._validate_completeness()
        self._validate_traceability()

        return self._build_result()

    def _build_result(self) -> ValidationResult:
        """Build validation result from collected issues"""
        errors = [i for i in self.issues if i.severity in [Severity.CRITICAL, Severity.HIGH]]
        warnings = [i for i in self.issues if i.severity == Severity.MEDIUM]
        info = [i for i in self.issues if i.severity in [Severity.LOW, Severity.INFO]]

        is_valid = len([i for i in self.issues if i.severity == Severity.CRITICAL]) == 0

        return ValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            info=info
        )

    def _validate_structure(self):
        """Validate top-level document structure"""
        if not isinstance(self.current_doc, dict):
            self.issues.append(ValidationIssue(
                severity=Severity.CRITICAL,
                message="Document must be a JSON object",
                field_path="root",
                category="Structure"
            ))
            return

    def _validate_required_fields(self):
        """Validate required fields are present"""
        required_top_level = [
            "document_metadata",
            "provider",
            "commission_period",
            "products"
        ]

        for field in required_top_level:
            if field not in self.current_doc:
                self.issues.append(ValidationIssue(
                    severity=Severity.CRITICAL,
                    message=f"Missing required field: {field}",
                    field_path=field,
                    category="Required Fields"
                ))

        # Validate nested required fields
        if "document_metadata" in self.current_doc:
            self._validate_required_in_object(
                self.current_doc["document_metadata"],
                ["document_id", "extracted_at", "extractor"],
                "document_metadata"
            )

        if self.current_doc.get("provider"):
            self._validate_required_in_object(
                self.current_doc["provider"],
                ["provider_id", "provider_name"],
                "provider"
            )

        if "commission_period" in self.current_doc:
            self._validate_required_in_object(
                self.current_doc["commission_period"],
                ["effective_from"],
                "commission_period"
            )

        # Validate products array
        if "products" in self.current_doc:
            if not isinstance(self.current_doc["products"], list):
                self.issues.append(ValidationIssue(
                    severity=Severity.CRITICAL,
                    message="products must be an array",
                    field_path="products",
                    category="Field Types"
                ))
            elif len(self.current_doc["products"]) == 0:
                self.issues.append(ValidationIssue(
                    severity=Severity.CRITICAL,
                    message="products array must have at least one item",
                    field_path="products",
                    category="Completeness"
                ))
            else:
                for i, product in enumerate(self.current_doc["products"]):
                    self._validate_required_in_object(
                        product,
                        ["product_code", "product_name", "commission_rules"],
                        f"products[{i}]"
                    )

                    if "commission_rules" in product and isinstance(product["commission_rules"], list):
                        if len(product["commission_rules"]) == 0:
                            self.issues.append(ValidationIssue(
                                severity=Severity.CRITICAL,
                                message=f"Product {product.get('product_code', i)} has no commission rules",
                                field_path=f"products[{i}].commission_rules",
                                category="Completeness"
                            ))
                        else:
                            for j, rule in enumerate(product["commission_rules"]):
                                self._validate_required_in_object(
                                    rule,
                                    ["rule_id", "priority", "outcome"],
                                    f"products[{i}].commission_rules[{j}]"
                                )

    def _validate_required_in_object(self, obj: Dict, required: List[str], path: str):
        """Helper to validate required fields in nested object"""
        if not isinstance(obj, dict):
            return

        for field in required:
            if field not in obj:
                self.issues.append(ValidationIssue(
                    severity=Severity.CRITICAL,
                    message=f"Missing required field: {field}",
                    field_path=f"{path}.{field}",
                    category="Required Fields"
                ))

    def _validate_field_types(self):
        """Validate field types are correct"""
        if "schema_version" in self.current_doc:
            if not isinstance(self.current_doc["schema_version"], str):
                self.issues.append(ValidationIssue(
                    severity=Severity.CRITICAL,
                    message="schema_version must be a string",
                    field_path="schema_version",
                    category="Field Types"
                ))
            elif self.current_doc["schema_version"] != "1.0":
                self.issues.append(ValidationIssue(
                    severity=Severity.HIGH,
                    message=f"Unsupported schema version: {self.current_doc['schema_version']} (expected '1.0')",
                    field_path="schema_version",
                    category="Version"
                ))

        # Validate products array types
        if "products" in self.current_doc and isinstance(self.current_doc["products"], list):
            for i, product in enumerate(self.current_doc["products"]):
                if "commission_rules" in product and isinstance(product["commission_rules"], list):
                    for j, rule in enumerate(product["commission_rules"]):
                        if "priority" in rule and not isinstance(rule["priority"], int):
                            self.issues.append(ValidationIssue(
                                severity=Severity.CRITICAL,
                                message="priority must be an integer",
                                field_path=f"products[{i}].commission_rules[{j}].priority",
                                category="Field Types"
                            ))

    def _validate_enums(self):
        """Validate enum field values"""
        # Top-level enum validators
        source_type_values = ["email", "pdf", "excel", "api", "manual"]
        product_category_values = ["motor", "health", "life", "travel", "home", "other"]

        # Rule condition enum validators
        condition_enums = {
            "policy_type": ["third-party", "comprehensive", "saod"],
            "booking_mode": ["online", "offline"],
            "fuel_type": ["petrol", "diesel", "cng", "lpg", "electric"],
            "business_type": ["new", "renewal", "rollover", "port"],
            "number_of_wheels": ["3w", "4w-and-above"],
            "vehicle_carrier": ["public", "private", "commercial"],
            "school_bus_type": ["on-individual-name", "on-school-name", "on-company-name", "on-contract-transporter"],
            "deal_parameter": ["od", "net", "regular-tp", "tp-with-vehicle-age", "flat-amount", "ape"],
        }
        condition_single_enums = {
            "zero_dep": ["yes", "no"],
            "cpa": ["with-cpa", "without-cpa"],
        }

        # Product-level metadata enum validators
        product_meta_enums = {
            "deal_type": ["market-deal", "market-bonus-deal", "special-deal", "special-bonus-deal", "declined-deal", "basic-deal"],
            "deal_business_type": ["net", "ape", "tp", "od"],
            "portfolio_business": ["market-deal", "special-deal", "declined-deal", "partner-specific-special-deal", "registration-deal"],
            "approval_type": ["deal", "booking", "manual-override"],
            "file_type": ["irda-statement", "rewards-statement", "enhancement-statement", "orc-statement"],
            "zone": ["north", "south", "east", "west"],
            "product_mode": ["online", "offline"],
            "bu_parameters": ["motor"],
            "decline_deal": ["decline"],
            "product_subproduct": ["car", "two-wheeler", "commercial-gcv", "commercial-pcv", "commercial-buses", "commercial-miscd"],
            "vehicle_type": ["car-taxi", "two-wheeler", "gcv", "pcv", "buses", "misc-d"],
        }

        # Validate source_type
        if "document_metadata" in self.current_doc:
            source_type = self.current_doc["document_metadata"].get("source_type")
            if source_type and source_type not in source_type_values:
                self.issues.append(ValidationIssue(
                    severity=Severity.HIGH,
                    message=f"Invalid source_type: {source_type}",
                    field_path="document_metadata.source_type",
                    category="Enum Values",
                    suggestion=f"Valid values: {', '.join(source_type_values)}"
                ))

        if "products" not in self.current_doc:
            return

        for i, product in enumerate(self.current_doc["products"]):
            # Validate product_category
            if "product_category" in product:
                category = product["product_category"]
                if category not in product_category_values:
                    self.issues.append(ValidationIssue(
                        severity=Severity.MEDIUM,
                        message=f"Invalid product_category: {category}",
                        field_path=f"products[{i}].product_category",
                        category="Enum Values",
                        suggestion=f"Valid values: {', '.join(product_category_values)}"
                    ))

            # Validate product-level metadata enums
            for field_name, valid_values in product_meta_enums.items():
                if field_name in product:
                    value = product[field_name]
                    if value and value not in valid_values:
                        self.issues.append(ValidationIssue(
                            severity=Severity.MEDIUM,
                            message=f"Invalid {field_name}: {value}",
                            field_path=f"products[{i}].{field_name}",
                            category="Enum Values",
                            suggestion=f"Valid values: {', '.join(valid_values)}"
                        ))

            # Validate rule condition enums
            if "commission_rules" not in product:
                continue

            for j, rule in enumerate(product["commission_rules"]):
                conditions = rule.get("conditions") or {}

                # Validate list enum fields
                for field_name, valid_values in condition_enums.items():
                    if field_name in conditions:
                        values = conditions[field_name]
                        if isinstance(values, list):
                            for val in values:
                                if val not in valid_values:
                                    self.issues.append(ValidationIssue(
                                        severity=Severity.MEDIUM,
                                        message=f"Invalid {field_name} value: {val}",
                                        field_path=f"products[{i}].commission_rules[{j}].conditions.{field_name}",
                                        category="Enum Values",
                                        suggestion=f"Valid values: {', '.join(valid_values)}"
                                    ))

                # Validate single enum fields
                for field_name, valid_values in condition_single_enums.items():
                    if field_name in conditions:
                        val = conditions[field_name]
                        if val and val not in valid_values:
                            self.issues.append(ValidationIssue(
                                severity=Severity.MEDIUM,
                                message=f"Invalid {field_name}: {val}",
                                field_path=f"products[{i}].commission_rules[{j}].conditions.{field_name}",
                                category="Enum Values",
                                suggestion=f"Valid values: {', '.join(valid_values)}"
                            ))

    def _validate_formats(self):
        """Validate field formats (dates, IDs, emails, etc.)"""
        # Validate ID patterns (alphanumeric, underscore, hyphen only)
        id_pattern = re.compile(r'^[A-Za-z0-9_-]+$')

        if "document_metadata" in self.current_doc:
            doc_id = self.current_doc["document_metadata"].get("document_id")
            if doc_id and not id_pattern.match(doc_id):
                self.issues.append(ValidationIssue(
                    severity=Severity.MEDIUM,
                    message=f"document_id contains invalid characters: {doc_id}",
                    field_path="document_metadata.document_id",
                    category="Format",
                    suggestion="Use only alphanumeric, underscore, and hyphen"
                ))

        if self.current_doc.get("provider"):
            provider_id = self.current_doc["provider"].get("provider_id")
            if provider_id and not id_pattern.match(provider_id):
                self.issues.append(ValidationIssue(
                    severity=Severity.MEDIUM,
                    message=f"provider_id contains invalid characters: {provider_id}",
                    field_path="provider.provider_id",
                    category="Format",
                    suggestion="Use only alphanumeric, underscore, and hyphen"
                ))

        # Validate dates (YYYY-MM-DD format)
        date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}$')

        if "commission_period" in self.current_doc:
            period = self.current_doc["commission_period"]

            if "effective_from" in period:
                effective_from = period["effective_from"]
                if effective_from is None:
                    self.issues.append(ValidationIssue(
                        severity=Severity.CRITICAL,
                        message="effective_from is null/missing",
                        field_path="commission_period.effective_from",
                        category="Required Data",
                        suggestion="Provide effective_from date in YYYY-MM-DD format"
                    ))
                elif not date_pattern.match(effective_from):
                    self.issues.append(ValidationIssue(
                        severity=Severity.CRITICAL,
                        message=f"effective_from has invalid date format: {effective_from}",
                        field_path="commission_period.effective_from",
                        category="Format",
                        suggestion="Use YYYY-MM-DD format"
                    ))

            if "effective_to" in period:
                effective_to = period["effective_to"]
                if effective_to is None:
                    # effective_to is optional, so this is just a warning
                    self.issues.append(ValidationIssue(
                        severity=Severity.LOW,
                        message="effective_to is null (no end date specified)",
                        field_path="commission_period.effective_to",
                        category="Completeness",
                        suggestion="Consider adding end date if commission period is limited"
                    ))
                elif not date_pattern.match(effective_to):
                    self.issues.append(ValidationIssue(
                        severity=Severity.CRITICAL,
                        message=f"effective_to has invalid date format: {effective_to}",
                        field_path="commission_period.effective_to",
                        category="Format",
                        suggestion="Use YYYY-MM-DD format"
                    ))

        # Validate ISO 8601 timestamp
        if "document_metadata" in self.current_doc:
            extracted_at = self.current_doc["document_metadata"].get("extracted_at")
            if extracted_at:
                try:
                    datetime.fromisoformat(extracted_at.replace('Z', '+00:00'))
                except ValueError:
                    self.issues.append(ValidationIssue(
                        severity=Severity.HIGH,
                        message=f"extracted_at has invalid ISO 8601 format: {extracted_at}",
                        field_path="document_metadata.extracted_at",
                        category="Format",
                        suggestion="Use ISO 8601 format with timezone (e.g., 2024-01-15T10:30:00Z)"
                    ))

        # Validate email format
        email_pattern = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

        if self.current_doc.get("provider"):
            contact_email = self.current_doc["provider"].get("contact_email")
            if contact_email and not email_pattern.match(contact_email):
                self.issues.append(ValidationIssue(
                    severity=Severity.MEDIUM,
                    message=f"Invalid email format: {contact_email}",
                    field_path="provider.contact_email",
                    category="Format"
                ))

    def _validate_business_rules(self):
        """Validate business rules and logic"""
        # Date consistency
        if "commission_period" in self.current_doc:
            period = self.current_doc["commission_period"]

            if "effective_from" in period and "effective_to" in period:
                effective_from = period["effective_from"]
                effective_to = period["effective_to"]

                # Skip date range validation if either date is None (already flagged in format validation)
                if effective_from is None or effective_to is None:
                    pass  # Validation errors already added in _validate_formats
                else:
                    try:
                        from_date = datetime.strptime(effective_from, "%Y-%m-%d")
                        to_date = datetime.strptime(effective_to, "%Y-%m-%d")

                        if to_date < from_date:
                            self.issues.append(ValidationIssue(
                                severity=Severity.CRITICAL,
                                message=f"effective_to ({effective_to}) is before effective_from ({effective_from})",
                                field_path="commission_period",
                                category="Business Logic"
                            ))
                    except ValueError:
                        pass  # Date format already validated

        # Commission rate sanity checks
        if "products" in self.current_doc:
            for i, product in enumerate(self.current_doc["products"]):
                if "commission_rules" not in product:
                    continue

                for j, rule in enumerate(product["commission_rules"]):
                    outcome = rule.get("outcome", {})

                    # Check percentage values
                    if "percentage_value" in outcome:
                        pct = outcome["percentage_value"]
                        if not isinstance(pct, (int, float)):
                            continue

                        if pct < 0:
                            self.issues.append(ValidationIssue(
                                severity=Severity.CRITICAL,
                                message=f"Negative commission percentage: {pct}%",
                                field_path=f"products[{i}].commission_rules[{j}].outcome.percentage_value",
                                category="Business Logic"
                            ))
                        elif pct > 100:
                            self.issues.append(ValidationIssue(
                                severity=Severity.HIGH,
                                message=f"Commission percentage exceeds 100%: {pct}%",
                                field_path=f"products[{i}].commission_rules[{j}].outcome.percentage_value",
                                category="Business Logic"
                            ))
                        elif pct > 50:
                            self.issues.append(ValidationIssue(
                                severity=Severity.MEDIUM,
                                message=f"Unusually high commission percentage: {pct}% (verify with source)",
                                field_path=f"products[{i}].commission_rules[{j}].outcome.percentage_value",
                                category="Business Logic"
                            ))

                    # Check flat values
                    if "flat_value" in outcome:
                        flat = outcome["flat_value"]
                        if isinstance(flat, (int, float)) and flat < 0:
                            self.issues.append(ValidationIssue(
                                severity=Severity.CRITICAL,
                                message=f"Negative flat commission: {flat}",
                                field_path=f"products[{i}].commission_rules[{j}].outcome.flat_value",
                                category="Business Logic"
                            ))

                    # Check priority values
                    if "priority" in rule:
                        priority = rule["priority"]
                        if isinstance(priority, int) and priority < 1:
                            self.issues.append(ValidationIssue(
                                severity=Severity.HIGH,
                                message=f"Priority must be >= 1, got {priority}",
                                field_path=f"products[{i}].commission_rules[{j}].priority",
                                category="Business Logic"
                            ))

                    # Validate condition range fields
                    conditions = rule.get("conditions") or {}
                    range_pairs = [
                        ("gvw_start_range", "gvw_end_range", "GVW"),
                        ("cc_start_range", "cc_end_range", "CC"),
                        ("seating_capacity_start_range", "seating_capacity_end_range", "Seating Capacity"),
                    ]
                    for start_key, end_key, label in range_pairs:
                        start_val = conditions.get(start_key)
                        end_val = conditions.get(end_key)
                        if start_val is not None and end_val is not None:
                            if isinstance(start_val, (int, float)) and isinstance(end_val, (int, float)):
                                if start_val > end_val:
                                    self.issues.append(ValidationIssue(
                                        severity=Severity.HIGH,
                                        message=f"{label} start ({start_val}) > end ({end_val})",
                                        field_path=f"products[{i}].commission_rules[{j}].conditions.{start_key}",
                                        category="Business Logic"
                                    ))

                    # Validate vehicle_age range
                    vehicle_age = conditions.get("vehicle_age")
                    if vehicle_age is not None and isinstance(vehicle_age, int):
                        if vehicle_age < 0 or vehicle_age > 35:
                            self.issues.append(ValidationIssue(
                                severity=Severity.MEDIUM,
                                message=f"Vehicle age {vehicle_age} out of expected range (0-35)",
                                field_path=f"products[{i}].commission_rules[{j}].conditions.vehicle_age",
                                category="Business Logic"
                            ))

                    # Validate od_term / tp_term range
                    for term_key in ["od_term", "tp_term"]:
                        term_val = conditions.get(term_key)
                        if term_val is not None and isinstance(term_val, int):
                            if term_val < 0 or term_val > 10:
                                self.issues.append(ValidationIssue(
                                    severity=Severity.MEDIUM,
                                    message=f"{term_key} value {term_val} out of expected range (0-10)",
                                    field_path=f"products[{i}].commission_rules[{j}].conditions.{term_key}",
                                    category="Business Logic"
                                ))

        # Check for duplicate priorities within products
        if "products" in self.current_doc:
            for i, product in enumerate(self.current_doc["products"]):
                if "commission_rules" not in product:
                    continue

                priorities = {}
                for j, rule in enumerate(product["commission_rules"]):
                    priority = rule.get("priority")
                    rule_id = rule.get("rule_id", f"rule_{j}")

                    if priority is not None:
                        if priority in priorities:
                            self.issues.append(ValidationIssue(
                                severity=Severity.HIGH,
                                message=f"Duplicate priority {priority} in product {product.get('product_code', i)}: rules {priorities[priority]} and {rule_id}",
                                field_path=f"products[{i}].commission_rules",
                                category="Business Logic",
                                suggestion="Each rule must have a unique priority within the product"
                            ))
                        else:
                            priorities[priority] = rule_id

    def _validate_completeness(self):
        """Validate data completeness"""
        # Check for provider contact info
        if self.current_doc.get("provider"):
            provider = self.current_doc["provider"]
            if not provider.get("contact_email") and not provider.get("contact_phone"):
                self.issues.append(ValidationIssue(
                    severity=Severity.MEDIUM,
                    message="Provider has no contact information (email or phone)",
                    field_path="provider",
                    category="Completeness",
                    suggestion="Add contact_email or contact_phone"
                ))

        # Check for at least one source trace per product
        if "products" in self.current_doc and "source_traces" in self.current_doc:
            product_codes = {p.get("product_code") for p in self.current_doc["products"] if p.get("product_code")}
            traced_products = {
                t.get("entity_id")
                for t in self.current_doc["source_traces"]
                if t.get("entity_type") == "product"
            }

            untraced = product_codes - traced_products
            if untraced:
                # Filter out None values and convert to strings
                untraced_str = [str(code) for code in untraced if code is not None]
                self.issues.append(ValidationIssue(
                    severity=Severity.LOW,
                    message=f"Products without source traces: {', '.join(untraced_str)}",
                    field_path="source_traces",
                    category="Completeness",
                    suggestion="Add at least one source trace per product"
                ))

    def _validate_traceability(self):
        """Validate source traces reference valid entities"""
        if "source_traces" not in self.current_doc:
            return

        # Build entity index
        entity_ids = set()

        # Add product codes
        if "products" in self.current_doc:
            for product in self.current_doc["products"]:
                entity_ids.add(product.get("product_code"))

                # Add rule IDs
                if "commission_rules" in product:
                    for rule in product["commission_rules"]:
                        entity_ids.add(rule.get("rule_id"))

        # Validate trace references
        for i, trace in enumerate(self.current_doc["source_traces"]):
            entity_id = trace.get("entity_id")
            entity_type = trace.get("entity_type")

            if entity_type in ["product", "rule", "condition", "outcome"]:
                if entity_id and entity_id not in entity_ids:
                    self.issues.append(ValidationIssue(
                        severity=Severity.MEDIUM,
                        message=f"Source trace references non-existent entity: {entity_id}",
                        field_path=f"source_traces[{i}].entity_id",
                        category="Traceability"
                    ))

            # Check for confidence scores on low-confidence traces
            confidence = trace.get("confidence")
            if confidence is not None and confidence < 0.7:
                if not trace.get("extraction_notes"):
                    self.issues.append(ValidationIssue(
                        severity=Severity.LOW,
                        message=f"Low confidence trace ({confidence}) missing extraction_notes",
                        field_path=f"source_traces[{i}]",
                        category="Traceability",
                        suggestion="Add extraction_notes explaining low confidence"
                    ))


def main():
    """Example usage"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python schema_validator.py <cics_document.json>")
        sys.exit(1)

    validator = SchemaValidator()
    result = validator.validate(sys.argv[1])

    print("\n=== VALIDATION RESULTS ===\n")

    summary = result.get_summary()
    print(f"Status: {'VALID' if result.is_valid else 'INVALID'}")
    print(f"Critical: {summary['critical']}")
    print(f"High: {summary['high']}")
    print(f"Medium: {summary['medium']}")
    print(f"Low: {summary['low']}")
    print(f"Info: {summary['info']}")

    if result.errors:
        print("\n=== ERRORS ===\n")
        for error in result.errors:
            print(f"[{error.severity.value}] {error.field_path}")
            print(f"  {error.message}")
            if error.suggestion:
                print(f"  Suggestion: {error.suggestion}")
            print()

    if result.warnings:
        print("\n=== WARNINGS ===\n")
        for warning in result.warnings:
            print(f"[{warning.severity.value}] {warning.field_path}")
            print(f"  {warning.message}")
            if warning.suggestion:
                print(f"  Suggestion: {warning.suggestion}")
            print()

    sys.exit(0 if result.is_valid else 1)


if __name__ == "__main__":
    main()
