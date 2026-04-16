"""
RawToCICSTransformer - Converts RawExtraction to full CICSDocument

This transformer handles all deterministic transformations:
- ID generation (document_id, rule_id, trace_id)
- Priority computation from rule specificity
- Commission type detection from value patterns
- Condition normalization (text to enums)
- Premium range parsing
- State/RTO expansion from master data
- Source trace generation
"""

import re
import hashlib
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any
from pathlib import Path

from cins.schema.raw_extraction import RawExtraction, RawCommissionEntry, RawMasterDataEntry
from cins.schema.cics_model import (
    CICSDocument,
    DocumentMetadata,
    Provider,
    CommissionPeriod,
    Product,
    CommissionRule,
    RuleConditions,
    CommissionOutcome,
    NumericRange,
    CommissionTier,
    Bonus,
    Deduction,
    SourceTrace,
    SourceLocation,
    ChangeReference,
    Validation,
)


# Mapping tables for normalizing free-text to enum values
CHANNEL_MAPPINGS = {
    "online": "online",
    "digital": "online",
    "web": "online",
    "app": "online",
    "offline": "offline",
    "branch": "offline",
    "agent": "offline",
    "partner": "partner",
    "misp": "partner",  # Motor Insurance Service Provider
    "broker": "broker",
    "direct": "direct",
}

CUSTOMER_TYPE_MAPPINGS = {
    "new": "new",
    "new customer": "new",
    "new customers": "new",
    "first time": "new",
    "fresh": "new",
    "renewal": "renewal",
    "renewals": "renewal",
    "renew": "renewal",
    "existing": "renewal",
    "portability": "portability",
    "port": "portability",
    "rollover": "rollover",
}

VEHICLE_TYPE_MAPPINGS = {
    "two-wheeler": "two-wheeler",
    "two wheeler": "two-wheeler",
    "2w": "two-wheeler",
    "2-wheeler": "two-wheeler",
    "bike": "two-wheeler",
    "scooter": "two-wheeler",
    "motorcycle": "two-wheeler",
    "four-wheeler": "four-wheeler",
    "four wheeler": "four-wheeler",
    "4w": "four-wheeler",
    "4-wheeler": "four-wheeler",
    "car": "four-wheeler",
    "pvt car": "four-wheeler",
    "private car": "four-wheeler",
    "gcv": "gcv",
    "goods carrying": "gcv",
    "goods vehicle": "gcv",
    "pcv": "pcv",
    "passenger carrying": "pcv",
    "passenger vehicle": "pcv",
    "bus": "bus",
    "staff bus": "staff-bus",
    "commercial": "commercial",
    "cv": "commercial",
}

COVERAGE_TYPE_MAPPINGS = {
    "comprehensive": "comprehensive",
    "comp": "comprehensive",
    "full": "comprehensive",
    "package": "package",
    "third-party": "third-party",
    "third party": "third-party",
    "tp": "third-party",
    "tp only": "third-party",
    "od-only": "od-only",
    "od only": "od-only",
    "own damage": "od-only",
}

PRODUCT_CATEGORY_MAPPINGS = {
    "motor": "motor",
    "4w": "motor",
    "2w": "motor",
    "two wheeler": "motor",
    "four wheeler": "motor",
    "gcv": "motor",
    "pcv": "motor",
    "car": "motor",
    "bike": "motor",
    "health": "health",
    "mediclaim": "health",
    "life": "life",
    "term": "life",
    "travel": "travel",
    "home": "home",
    "fire": "other",
    "marine": "other",
}

# Valid Indian state codes
VALID_STATE_CODES = {
    "AN",
    "AP",
    "AR",
    "AS",
    "BR",
    "CH",
    "CG",
    "DD",
    "DL",
    "GA",
    "GJ",
    "HR",
    "HP",
    "JH",
    "JK",
    "KA",
    "KL",
    "LA",
    "LD",
    "MP",
    "MH",
    "MN",
    "ML",
    "MZ",
    "NL",
    "OD",
    "PB",
    "PY",
    "RJ",
    "SK",
    "TN",
    "TS",
    "TR",
    "UK",
    "UP",
    "WB",
}

STATE_NAME_MAPPINGS = {
    "andaman and nicobar": "AN",
    "andaman & nicobar": "AN",
    "andhra pradesh": "AP",
    "arunachal pradesh": "AR",
    "assam": "AS",
    "bihar": "BR",
    "chandigarh": "CH",
    "chhattisgarh": "CG",
    "dadra and nagar haveli": "DN",
    "daman and diu": "DD",
    "delhi": "DL",
    "goa": "GA",
    "gujarat": "GJ",
    "haryana": "HR",
    "himachal pradesh": "HP",
    "jammu and kashmir": "JK",
    "jammu & kashmir": "JK",
    "jharkhand": "JH",
    "karnataka": "KA",
    "kerala": "KL",
    "ladakh": "LA",
    "lakshadweep": "LD",
    "madhya pradesh": "MP",
    "maharashtra": "MH",
    "manipur": "MN",
    "meghalaya": "ML",
    "mizoram": "MZ",
    "nagaland": "NL",
    "odisha": "OD",
    "orissa": "OD",
    "puducherry": "PY",
    "pondicherry": "PY",
    "punjab": "PB",
    "rajasthan": "RJ",
    "sikkim": "SK",
    "tamil nadu": "TN",
    "telangana": "TS",
    "tripura": "TR",
    "uttar pradesh": "UP",
    "uttarakhand": "UK",
    "west bengal": "WB",
    "up": "UP",
    "hp": "HP",
    "wb": "WB",
    "tn": "TN",
    "mp": "MP",
    "ap": "AP",
    "ts": "TS",
    "mh": "MH",
    "gj": "GJ",
    "rj": "RJ",
    "ka": "KA",
    "kl": "KL",
    "hr": "HR",
    "br": "BR",
    "jk": "JK",
    "dl": "DL",
}


class RawToCICSTransformer:
    """
    Transforms RawExtraction to full CICS schema.
    All transformations are deterministic and rule-based.
    """

    def __init__(self):
        """Initialize transformer with empty warnings/errors lists."""
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self._trace_counter = 0

    def transform(
        self, raw: RawExtraction
    ) -> Tuple[CICSDocument, List[str], List[str]]:
        """
        Transform RawExtraction to CICSDocument.

        Args:
            raw: The raw extraction from AI

        Returns:
            Tuple of (CICSDocument, warnings, errors)
        """
        self.warnings = []
        self.errors = []
        self._trace_counter = 0

        # Generate document metadata
        doc_metadata = self._transform_metadata(raw)

        # Transform provider
        provider = self._transform_provider(raw.provider)

        # Transform commission period
        period = self._transform_period(raw)

        # Group entries by product and transform
        products = self._transform_products(
            raw.commission_entries, raw.master_data, raw
        )

        # Generate source traces
        source_traces = self._generate_source_traces(raw.commission_entries, products)

        # Build change reference if inheritance detected
        change_ref = self._transform_change_reference(raw)

        # Generate validation block
        validation = self._generate_validation(raw, products)

        doc = CICSDocument(
            schema_version="1.0",
            document_metadata=doc_metadata,
            provider=provider,
            commission_period=period,
            products=products,
            change_reference=change_ref,
            source_traces=source_traces,
            validation=validation,
        )

        return doc, self.warnings, self.errors

    # ==================== METADATA ====================

    def _transform_metadata(self, raw: RawExtraction) -> DocumentMetadata:
        """Transform metadata from raw extraction."""
        document_id = self._generate_document_id(raw)

        source_type = None
        if raw.source_type:
            st = raw.source_type.lower()
            if st in ["email", "pdf", "excel", "api", "manual"]:
                source_type = st

        return DocumentMetadata(
            document_id=document_id,
            source_type=source_type,
            source_file=raw.source_file,
            extracted_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            extractor="raw_to_cics_transformer",
            notes=raw.extraction_notes,
            additional_info={
                "master_data_count": len(raw.master_data) if raw.master_data else 0
            },
        )

    def _generate_document_id(self, raw: RawExtraction) -> str:
        """
        Generate document_id from provider + period + hash.
        Pattern: {PROVIDER}_{PERIOD}_{HASH}
        """
        provider_slug = self._slugify(raw.provider.name)

        # Parse period
        period_slug = self._extract_period_slug(raw)

        # Short hash for uniqueness
        content_hash = hashlib.md5(
            f"{raw.provider.name}{raw.start_date}".encode()
        ).hexdigest()[:6]

        return f"{provider_slug}_{period_slug}_{content_hash}"

    def _extract_period_slug(self, raw: RawExtraction) -> str:
        """Extract a period slug from raw extraction."""
        if raw.period_label:
            return self._slugify(raw.period_label)

        # Try to parse from start_date
        parsed = self._parse_date(raw.start_date)
        if parsed:
            return parsed[:7].replace("-", "")  # YYYYMM

        return "UNKNOWN"

    # ==================== PROVIDER ====================

    def _transform_provider(self, raw_provider) -> Provider:
        """Transform provider from raw extraction."""
        provider_id = self._slugify(raw_provider.name).upper()
        if not provider_id:
            provider_id = "UNKNOWN_PROVIDER"

        return Provider(
            provider_id=provider_id,
            provider_name=raw_provider.name or "Unknown Provider",
        )

    # ==================== PERIOD ====================

    def _transform_period(self, raw: RawExtraction) -> CommissionPeriod:
        """Transform commission period from raw extraction."""
        effective_from = self._parse_date(raw.start_date)
        if not effective_from:
            self.warnings.append(f"Could not parse start_date: {raw.start_date}")
            effective_from = datetime.utcnow().strftime("%Y-%m-%d")

        effective_to = None
        if raw.end_date:
            effective_to = self._parse_date(raw.end_date)
            if not effective_to:
                self.warnings.append(f"Could not parse end_date: {raw.end_date}")

        return CommissionPeriod(
            effective_from=effective_from,
            effective_to=effective_to,
            period_label=raw.period_label,
        )

    # ==================== PRODUCTS & RULES ====================

    def _transform_products(
        self,
        entries: List[RawCommissionEntry],
        master_data: Optional[List[RawMasterDataEntry]],
        raw: RawExtraction,
    ) -> List[Product]:
        """Group entries by product and transform to Product objects."""
        # Group entries by product
        product_groups: Dict[str, List[RawCommissionEntry]] = {}

        for entry in entries:
            product_key = self._get_product_key(entry)
            if product_key not in product_groups:
                product_groups[product_key] = []
            product_groups[product_key].append(entry)

        # Transform each product group
        products = []
        for product_key, group_entries in product_groups.items():
            product = self._transform_product(
                product_key, group_entries, master_data, raw
            )
            products.append(product)

        return products

    def _get_product_key(self, entry: RawCommissionEntry) -> str:
        """Get a product key from an entry."""
        if entry.product_name:
            return entry.product_name
        if entry.product_type:
            return entry.product_type
        return "DEFAULT_PRODUCT"

    def _transform_product(
        self,
        product_key: str,
        entries: List[RawCommissionEntry],
        master_data: Optional[List[RawMasterDataEntry]],
        raw: RawExtraction,
    ) -> Product:
        """Transform a group of entries into a Product."""
        # Determine product code and category
        product_code = self._slugify(product_key).upper()
        product_category = self._detect_product_category(product_key, entries)

        # Compute priorities based on specificity
        priorities = self._compute_priorities(entries)

        # Transform each entry to a rule
        rules = []
        for i, entry in enumerate(entries):
            priority = priorities.get(i, (i + 1) * 10)
            rule = self._transform_rule(
                entry, product_code, i, priority, master_data, raw
            )
            rules.append(rule)

        # Determine sub-category
        sub_category = None
        if entries:
            first_entry = entries[0]
            if first_entry.product_type:
                sub_category = first_entry.product_type.lower()

        return Product(
            product_code=product_code,
            product_name=product_key,
            product_category=product_category,
            sub_category=sub_category,
            commission_rules=rules,
        )

    def _detect_product_category(
        self, product_key: str, entries: List[RawCommissionEntry]
    ) -> Optional[str]:
        """Detect product category from product key and entries."""
        key_lower = product_key.lower()

        for pattern, category in PRODUCT_CATEGORY_MAPPINGS.items():
            if pattern in key_lower:
                return category

        # Check entries
        for entry in entries:
            if entry.product_type:
                type_lower = entry.product_type.lower()
                for pattern, category in PRODUCT_CATEGORY_MAPPINGS.items():
                    if pattern in type_lower:
                        return category

        return None

    def _transform_rule(
        self,
        entry: RawCommissionEntry,
        product_code: str,
        index: int,
        priority: int,
        master_data: Optional[List[RawMasterDataEntry]],
        raw: RawExtraction,
    ) -> CommissionRule:
        """Transform a raw entry into a CommissionRule."""
        rule_id = self._generate_rule_id(product_code, entry, index)

        # Transform conditions
        conditions = self._transform_conditions(entry, master_data, raw)

        # Transform outcome
        outcome = self._transform_outcome(entry, raw)

        # Parse rule-specific validity dates (derived from global period only)
        valid_from = None
        valid_to = None

        return CommissionRule(
            rule_id=rule_id,
            priority=priority,
            conditions=conditions if self._has_conditions(conditions) else None,
            outcome=outcome,
            valid_from=valid_from,
            valid_to=valid_to,
            rule_description=entry.rule_text[:200] if entry.rule_text else None,
        )

    def _generate_rule_id(
        self, product_code: str, entry: RawCommissionEntry, index: int
    ) -> str:
        """Generate rule_id from product + conditions + index."""
        parts = [product_code]

        # Add condition identifiers
        if entry.customer_type_text:
            parts.append(self._slugify(entry.customer_type_text)[:3].upper())
        if entry.channel_text:
            parts.append(self._slugify(entry.channel_text)[:3].upper())

        # Ensure uniqueness with index
        parts.append(f"R{index + 1}")

        return "_".join(parts)

    def _has_conditions(self, conditions: RuleConditions) -> bool:
        """Check if conditions object has any non-null values."""
        return any(
            [
                conditions.channel,
                conditions.customer_type,
                conditions.vehicle_type,
                conditions.coverage_type,
                conditions.state,
                conditions.state_exclude,
                conditions.custom_conditions,
            ]
        )

    # ==================== CONDITIONS ====================

    def _transform_conditions(
        self,
        entry: RawCommissionEntry,
        master_data: Optional[List[RawMasterDataEntry]],
        raw: RawExtraction,
    ) -> RuleConditions:
        """Transform entry conditions to RuleConditions."""
        channel = self._normalize_channel(entry.channel_text)
        customer_type = self._normalize_customer_type(entry.customer_type_text)
        vehicle_type = self._normalize_vehicle_type(
            getattr(raw, "global_vehicle_type_text", None)
        )
        coverage_type = self._normalize_coverage_type(entry.coverage_type_text)
        location_text = entry.state_text or getattr(raw, "global_state_text", None)
        state, state_exclude = self._expand_state_conditions(location_text, master_data)

        custom_conditions = self._parse_custom_conditions(
            entry.other_conditions or getattr(raw, "global_other_conditions", None),
            getattr(raw, "global_seating_capacity_text", None),
        )

        # Extract RTOs from location_text if present (e.g. "UP-16")
        if location_text:
            rto_codes = re.findall(r"\b[A-Z]{2}-\d{2}\b", location_text.upper())
            if rto_codes:
                if custom_conditions is None:
                    custom_conditions = {}
                existing_rtos = custom_conditions.get("rto", [])
                if isinstance(existing_rtos, list):
                    custom_conditions["rto"] = list(set(existing_rtos + rto_codes))
                else:
                    custom_conditions["rto"] = rto_codes

        # If location_text was provided but no states were extracted, preserve it in custom_conditions
        if location_text and not state and not state_exclude:
            if custom_conditions is None:
                custom_conditions = {}
            if "location" not in custom_conditions:
                custom_conditions["location"] = location_text

        # Merge structured conditions dict from extraction (fuel_type, ncb, preferred, etc.)
        entry_conditions = getattr(entry, "conditions", None) or {}
        if entry_conditions:
            if custom_conditions is None:
                custom_conditions = {}

            # PROACTIVE MAPPING: Check if any condition value exists in master data
            # e.g. if preferred="Preferred", check master data for "Preferred" mapping
            if master_data:
                for cond_key, cond_val in list(entry_conditions.items()):
                    if not cond_val or not isinstance(cond_val, str):
                        continue

                    for m in master_data:
                        if m.key and m.key.lower() == cond_val.lower():
                            # Found a mapping for this condition value!
                            if m.values:
                                # Expand values into custom_conditions for more context
                                mapping_key = f"{cond_key}_expanded"
                                if mapping_key not in custom_conditions:
                                    custom_conditions[mapping_key] = m.values

                                # If the mapping contains state codes, add them to rule state
                                codes, _ = self._expand_state_conditions(m.values, None)
                                if codes:
                                    if state is None:
                                        state = []
                                    state = list(set(state + codes))

            # entry.conditions values win over raw_condition fallback
            custom_conditions.update(entry_conditions)

        return RuleConditions(
            channel=channel,
            customer_type=customer_type,
            vehicle_type=vehicle_type,
            coverage_type=coverage_type,
            state=state,
            state_exclude=state_exclude,
            custom_conditions=custom_conditions,
        )

    def _normalize_channel(self, text: Optional[str]) -> Optional[List[str]]:
        """Normalize channel text to enum values."""
        if not text:
            return None

        normalized = []
        text_lower = text.lower()

        for key, value in CHANNEL_MAPPINGS.items():
            if key in text_lower and value not in normalized:
                normalized.append(value)

        if not normalized:
            self.warnings.append(f"Unknown channel: {text}")
            return None

        return normalized

    def _normalize_customer_type(self, text: Optional[str]) -> Optional[List[str]]:
        """Normalize customer type text."""
        if not text:
            return None

        normalized = []
        text_lower = text.lower()

        for key, value in CUSTOMER_TYPE_MAPPINGS.items():
            if key in text_lower and value not in normalized:
                normalized.append(value)

        if not normalized:
            # Keep as-is if no mapping found
            return [text]

        return normalized

    def _normalize_vehicle_type(self, text: Optional[str]) -> Optional[List[str]]:
        """Normalize vehicle type text."""
        if not text:
            return None

        normalized = []
        text_lower = text.lower()

        for key, value in VEHICLE_TYPE_MAPPINGS.items():
            if key in text_lower and value not in normalized:
                normalized.append(value)

        if not normalized:
            return [text.lower()]

        return normalized

    def _normalize_coverage_type(self, text: Optional[str]) -> Optional[List[str]]:
        """Normalize coverage type text."""
        if not text:
            return None

        text_lower = text.lower()

        for key, value in COVERAGE_TYPE_MAPPINGS.items():
            if key in text_lower:
                return [value]

        self.warnings.append(f"Unknown coverage type: {text}")
        return None

    def _expand_state_conditions(
        self, state_text: Optional[str], master_data: Optional[List[RawMasterDataEntry]]
    ) -> Tuple[Optional[List[str]], Optional[List[str]]]:
        """
        Expand state text to state codes and handle exclusions.
        Proactively looks up master data for category mappings (e.g., "NCR", "Preferred").
        """
        if not state_text:
            # Fallback: Check for global state mappings in master data
            if master_data:
                for m in master_data:
                    if m.entry_type == "state_mapping" and m.values:
                        return self._expand_state_conditions(m.values, None)
            return None, None

        text = state_text.lower()
        discovered_codes = []
        state_exclude = None

        # 1. Handle Exclusions ("All states except...")
        if "except" in text or "excluding" in text or "other than" in text:
            # Try to capture everything after exclusion keywords
            exclude_pattern = (
                r"(?:except|excluding|other than)\s+([^,;]+(?:[,;][^,;]+)*)"
            )
            match = re.search(exclude_pattern, state_text, re.IGNORECASE)
            if match:
                exclude_text = match.group(1)
                codes, _ = self._expand_state_conditions(exclude_text, master_data)
                if codes:
                    return None, codes

        # 2. Proactive Master Data Expansion
        if master_data:
            # Check for tokens that might be categories (e.g., "NCR", "Rest of Gujarat")
            upper_text = state_text.upper()

            for m in master_data:
                if not m.key:
                    continue

                key_upper = m.key.upper()
                # Check if key is in state_text as a whole word
                if re.search(rf"\b{re.escape(key_upper)}\b", upper_text):
                    if m.values:
                        codes, _ = self._expand_state_conditions(m.values, master_data)
                        if codes:
                            discovered_codes.extend(codes)

                # Reverse check: if state_text matches a value in master data
                if m.values and upper_text == m.values.upper():
                    if key_upper in VALID_STATE_CODES:
                        discovered_codes.append(key_upper)

        # 3. Standard State Name/Code Mapping
        # Use more flexible word boundaries to handle common separators like _ or -
        for name, code in STATE_NAME_MAPPINGS.items():
            if len(name) <= 3:
                # For short names/abbrs, use flexible word boundaries that include underscore
                pattern = rf"(?:^|[\s,_\-\/]){re.escape(name)}(?=$|[\s,_\-\/])"
            else:
                pattern = rf"{re.escape(name)}"

            if re.search(pattern, text, re.IGNORECASE):
                discovered_codes.append(code)

        # 4. Direct State Code Extraction (2-letter uppercase)
        raw_codes = re.findall(r"\b[A-Z]{2}\b", state_text.upper())
        for code in raw_codes:
            if code in VALID_STATE_CODES:
                discovered_codes.append(code)

        if discovered_codes:
            return list(set(discovered_codes)), state_exclude

        return None, state_exclude

    def _parse_custom_conditions(
        self, other_text: Optional[str], seating_text: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Parse other conditions into custom_conditions dict."""
        if not other_text and not seating_text:
            return None

        conditions = {}

        # 1. Process explicit seating_capacity_text if present
        if seating_text:
            text_lower = seating_text.lower()
            # Handle "capacity > 12", "carrying capacity of 12", "seating 12+1", "12+1"
            capacity_match = re.search(
                r"(?:capacity|seating|carrying)?\s*(?:of|is)?\s*(?:>|>=|above|greater than|more than|at least)?\s*(\d+)",
                text_lower,
            )
            if capacity_match:
                conditions["seating_capacity"] = {"min": int(capacity_match.group(1))}
            else:
                conditions["seating_capacity"] = seating_text

        # 2. Process other_conditions text
        if other_text:
            text_lower = other_text.lower()

            # Parse seating/carrying capacity if not already found
            if "seating_capacity" not in conditions:
                # More robust pattern for natural language
                # "carrying capacity of the buses is more than 12"
                # "seating capacity > 12"
                capacity_pattern = r"(?:seating|carrying|seating)\s*(?:capacity)?\s*(?:of the buses)?\s*(?:is)?\s*(?:>|>=|above|greater than|more than)?\s*(\d+)"
                match = re.search(capacity_pattern, text_lower)
                if match:
                    conditions["seating_capacity"] = {"min": int(match.group(1))}

            # Parse vehicle weight
            weight_pattern = r"(?:weight|gvw)\s*(?:>|>=|above|greater than)?\s*(\d+(?:\.\d+)?)\s*(?:t|ton)"
            match = re.search(weight_pattern, text_lower)
            if match:
                conditions["vehicle_weight"] = f"> {match.group(1)}T"

            # Parse registration type
            if "corporate" in text_lower or "company" in text_lower:
                conditions["registration"] = "corporate"
            elif "individual" in text_lower or "personal" in text_lower:
                conditions["registration"] = "individual"

            # Parse permit type
            permit_types = ["staff bus", "tourist", "contract", "stage carriage"]
            for permit in permit_types:
                if permit in text_lower:
                    conditions["permit_type"] = permit.replace(" ", "-")
                    break

        # If we couldn't parse structured conditions, store the raw text
        if not conditions:
            parts = []
            if seating_text:
                parts.append(f"Seating: {seating_text}")
            if other_text:
                parts.append(other_text)
            conditions["raw_condition"] = "; ".join(parts)

        return conditions if conditions else None

    # ==================== OUTCOMES ====================

    def _transform_outcome(
        self, entry: RawCommissionEntry, raw: RawExtraction
    ) -> CommissionOutcome:
        """Transform entry to CommissionOutcome."""
        commission_type, parsed_values = self._detect_commission_type(
            entry.commission_value
        )

        # Build outcome
        outcome_dict = {"commission_type": commission_type, **parsed_values}

        # Add GST information
        gst_applicable = entry.gst_applicable
        gst_percentage = None

        if raw.global_gst_percentage:
            gst_match = re.search(r"(\d+(?:\.\d+)?)", raw.global_gst_percentage)
            if gst_match:
                gst_percentage = float(gst_match.group(1))
                gst_applicable = True

        outcome_dict["gst_applicable"] = gst_applicable
        if gst_percentage:
            outcome_dict["gst_percentage"] = gst_percentage

        # Add applied_on
        if entry.applied_on_text:
            outcome_dict["applied_on"] = entry.applied_on_text
        elif raw.global_applied_on:
            outcome_dict["applied_on"] = raw.global_applied_on

        # Parse bonuses (from global settings only)
        bonuses = self._parse_bonuses(None)
        if bonuses:
            outcome_dict["bonuses"] = bonuses
            if commission_type == "percentage":
                outcome_dict["commission_type"] = "hybrid"

        # Parse deductions (from global settings only)
        deductions = self._parse_deductions(
            None, raw.global_deductions, entry.channel_text
        )
        if deductions:
            outcome_dict["deductions"] = deductions
            if commission_type == "percentage":
                outcome_dict["commission_type"] = "hybrid"

        return CommissionOutcome(**outcome_dict)

    def _detect_commission_type(self, value_text: str) -> Tuple[str, Dict]:
        """
        Detect commission type from value pattern.

        Patterns:
        - "12%" -> percentage
        - "Rs. 500" or "500 INR" -> flat
        - "0-25K: 10%, 25K-50K: 12%" -> tiered
        - "12% + 2% bonus" -> hybrid
        """
        # Handle empty commission value (placeholder case)
        if not value_text or value_text.strip() == "":
            self.warnings.append("Empty commission value - flagging for manual review")
            return "percentage", {
                "percentage_value": None,
                "calculation_notes": "Commission value not provided in source document - REQUIRES MANUAL REVIEW",
            }

        value = value_text.strip()
        value_lower = value.lower()

        # Check for tiered pattern (multiple rate:value pairs)
        tier_pattern = (
            r"(\d+[\d,kK]*)\s*[-–to]+\s*(\d+[\d,kK]*)\s*:\s*(\d+(?:\.\d+)?)\s*%"
        )
        tier_matches = re.findall(tier_pattern, value, re.IGNORECASE)
        if len(tier_matches) >= 2:
            tiers = self._parse_tiers(value)
            if tiers:
                return "tiered", {"tiers": tiers}

        # Check for hybrid (base + bonus)
        if re.search(r"\+\s*\d+(?:\.\d+)?%", value) or "bonus" in value_lower:
            base_match = re.search(r"^(\d+(?:\.\d+)?)\s*%", value)
            if base_match:
                return "hybrid", {"percentage_value": float(base_match.group(1))}

        # Check for flat amount
        flat_pattern = r"(?:Rs\.?|INR|₹)\s*(\d+(?:,\d{3})*(?:\.\d{2})?)"
        flat_match = re.search(flat_pattern, value)
        if flat_match:
            amount = float(flat_match.group(1).replace(",", ""))
            return "flat", {"flat_value": amount, "flat_currency": "INR"}

        # Default: percentage with % sign
        pct_pattern = r"(\d+(?:\.\d+)?)\s*%"
        pct_match = re.search(pct_pattern, value)
        if pct_match:
            pct_value = float(pct_match.group(1))
            if pct_value > 100:
                self.warnings.append(
                    f"Commission value '{value}' exceeds 100% — interpreting as flat INR amount"
                )
                return "flat", {"flat_value": pct_value, "flat_currency": "INR"}
            return "percentage", {"percentage_value": pct_value}

        # Plain decimal/number without % sign (e.g., "0.26" means 26%, "26" means 26%)
        plain_number_pattern = r"^(\d*\.?\d+)$"
        plain_match = re.match(plain_number_pattern, value.strip())
        if plain_match:
            num_value = float(plain_match.group(1))
            # If value < 1, assume it's a decimal representation (0.26 = 26%)
            if num_value < 1:
                return "percentage", {"percentage_value": num_value * 100}
            # If value > 100, it can't be a percentage — treat as flat amount (INR)
            elif num_value > 100:
                self.warnings.append(
                    f"Commission value '{value}' exceeds 100 — interpreting as flat INR amount"
                )
                return "flat", {"flat_value": num_value, "flat_currency": "INR"}
            else:
                return "percentage", {"percentage_value": num_value}

        # Could not parse - don't default to 0, use None
        self.warnings.append(f"Could not parse commission value: {value}")
        self.errors.append(
            f"Unparsable commission value will require manual review: {value}"
        )
        return "percentage", {
            "percentage_value": None,
            "calculation_notes": f"UNPARSED: {value} - REQUIRES MANUAL REVIEW",
        }

    def _parse_tiers(self, value_text: str) -> Optional[List[CommissionTier]]:
        """Parse tiered commission structure."""
        tiers = []
        value_text = self._normalize_amount_text(value_text)

        # Pattern: threshold: rate%
        tier_pattern = (
            r"(\d+(?:,\d{3})*)\s*[-–to]+\s*(\d+(?:,\d{3})*)\s*:\s*(\d+(?:\.\d+)?)\s*%"
        )
        matches = re.findall(tier_pattern, value_text, re.IGNORECASE)

        for match in matches:
            min_val = int(match[0].replace(",", ""))
            max_val = int(match[1].replace(",", ""))
            rate = float(match[2])

            tiers.append(
                CommissionTier(
                    threshold=NumericRange(min=min_val, max=max_val, unit="INR"),
                    value=rate,
                    value_type="percentage",
                )
            )

        # Check for "above X" pattern
        above_pattern = (
            r"(?:above|>)\s*(?:rs\.?\s*)?(\d+(?:,\d{3})*)\s*:\s*(\d+(?:\.\d+)?)\s*%"
        )
        above_match = re.search(above_pattern, value_text, re.IGNORECASE)
        if above_match:
            min_val = int(above_match.group(1).replace(",", "")) + 1
            rate = float(above_match.group(2))

            tiers.append(
                CommissionTier(
                    threshold=NumericRange(min=min_val, unit="INR"),
                    value=rate,
                    value_type="percentage",
                )
            )

        return tiers if tiers else None

    def _parse_bonuses(self, text: Optional[str]) -> Optional[List[Bonus]]:
        """Parse bonuses from text."""
        if not text:
            return None

        bonuses = []
        text_lower = text.lower()

        # Pattern: X% for Y
        bonus_pattern = (
            r"(?:additional\s+)?(\d+(?:\.\d+)?)\s*%\s*(?:for|on|with)?\s*(.+?)(?:,|$)"
        )
        matches = re.findall(bonus_pattern, text_lower)

        for match in matches:
            value = float(match[0])
            condition = match[1].strip()

            # Determine bonus type from condition
            bonus_type = "bonus"
            if "ncb" in condition or "no claim" in condition:
                bonus_type = "high-ncb-bonus"
            elif "volume" in condition or "policies" in condition:
                bonus_type = "volume-bonus"
            elif "target" in condition:
                bonus_type = "target-achievement"
            elif "early" in condition:
                bonus_type = "early-payment"

            bonuses.append(
                Bonus(
                    bonus_type=bonus_type,
                    value=value,
                    value_type="percentage",
                    conditions=condition if condition else None,
                )
            )

        return bonuses if bonuses else None

    def _parse_deductions(
        self,
        text: Optional[str],
        global_deductions: Optional[str],
        channel_text: Optional[str],
    ) -> Optional[List[Deduction]]:
        """Parse deductions from text."""
        deductions = []

        # Check if global deductions apply
        deduction_text = text or ""
        if global_deductions:
            # Check if global deduction applies to this channel
            if channel_text:
                channel_lower = channel_text.lower()
                global_lower = global_deductions.lower()
                if "online" in channel_lower and "online" in global_lower:
                    deduction_text = f"{deduction_text} {global_deductions}"
                elif "all" in global_lower:
                    deduction_text = f"{deduction_text} {global_deductions}"

        if not deduction_text.strip():
            return None

        text_lower = deduction_text.lower()

        # Pattern: X% fee/charge
        deduction_pattern = (
            r"(\d+(?:\.\d+)?)\s*%\s*(?:platform fee|processing|fee|charge)"
        )
        matches = re.findall(deduction_pattern, text_lower)

        for match in matches:
            value = float(match)
            deductions.append(
                Deduction(
                    deduction_type="platform-fee",
                    value=value,
                    value_type="percentage",
                    conditions="Applied to online transactions"
                    if "online" in text_lower
                    else None,
                )
            )

        # Pattern: Rs. X fee
        flat_deduction_pattern = r"(?:Rs\.?|INR|₹)\s*(\d+(?:,\d{3})*)\s*(?:fee|charge)"
        flat_matches = re.findall(flat_deduction_pattern, text_lower)

        for match in flat_matches:
            value = float(match.replace(",", ""))
            deductions.append(
                Deduction(
                    deduction_type="processing-fee",
                    value=value,
                    value_type="flat",
                    conditions=None,
                )
            )

        return deductions if deductions else None

    # ==================== PRIORITY COMPUTATION ====================

    def _compute_priorities(self, entries: List[RawCommissionEntry]) -> Dict[int, int]:
        """
        Compute priorities based on rule specificity.
        More specific rules get lower (higher priority) numbers.
        """
        specificity_scores = []

        for i, entry in enumerate(entries):
            score = 0

            # Count non-null condition fields
            condition_fields = [
                entry.customer_type_text,
                entry.channel_text,
                entry.coverage_type_text,
                entry.state_text,
                entry.other_conditions,
            ]

            score += sum(10 for f in condition_fields if f)

            # Each key in conditions dict adds specificity (same weight as a named field)
            entry_conditions = getattr(entry, "conditions", None) or {}
            score += len(entry_conditions) * 10

            # Custom conditions add specificity
            if entry.other_conditions:
                score += len(entry.other_conditions.split(",")) * 3

            specificity_scores.append((i, score))

        # Sort by score descending (most specific first)
        sorted_entries = sorted(specificity_scores, key=lambda x: -x[1])

        # Assign priorities in increments of 10
        priorities = {}
        for rank, (index, _) in enumerate(sorted_entries):
            priorities[index] = (rank + 1) * 10

        return priorities

    # ==================== SOURCE TRACES ====================

    def _generate_source_traces(
        self, entries: List[RawCommissionEntry], products: List[Product]
    ) -> List[SourceTrace]:
        """Generate source traces linking extracted data to source locations."""
        traces = []

        # Product-level traces
        for product in products:
            trace = SourceTrace(
                trace_id=self._next_trace_id(),
                entity_type="product",
                entity_id=product.product_code,
                source_location=None,
                quoted_text=f"Product: {product.product_name}",
                confidence=0.95,
            )
            traces.append(trace)

        # Rule-level traces
        for product in products:
            for rule in product.commission_rules:
                # Find matching entry
                for entry in entries:
                    if self._entry_matches_rule(entry, rule):
                        source_loc = None
                        if entry.source_section:
                            source_loc = SourceLocation(section=entry.source_section)

                        confidence = 0.9
                        if entry.confidence_note:
                            confidence = 0.7

                        traces.append(
                            SourceTrace(
                                trace_id=self._next_trace_id(),
                                entity_type="rule",
                                entity_id=rule.rule_id,
                                source_location=source_loc,
                                quoted_text=entry.quoted_text or entry.rule_text[:200],
                                confidence=confidence,
                                extraction_notes=entry.confidence_note,
                            )
                        )
                        break

        return traces

    def _entry_matches_rule(
        self, entry: RawCommissionEntry, rule: CommissionRule
    ) -> bool:
        """Check if an entry matches a rule (simple heuristic)."""
        if rule.rule_description and entry.rule_text:
            return (
                entry.rule_text[:50] in rule.rule_description
                or rule.rule_description[:50] in entry.rule_text
            )
        return False

    def _next_trace_id(self) -> str:
        """Generate next trace ID."""
        self._trace_counter += 1
        return f"TRACE_{self._trace_counter:03d}"

    # ==================== CHANGE REFERENCE ====================

    def _transform_change_reference(
        self, raw: RawExtraction
    ) -> Optional[ChangeReference]:
        """Transform change reference if inheritance detected."""
        if not raw.inherits_from_previous:
            return None

        return ChangeReference(
            is_inherited=True, inheritance_notes=raw.inheritance_note
        )

    # ==================== VALIDATION ====================

    def _generate_validation(self, raw: RawExtraction, products: List) -> Validation:
        """Generate validation block."""
        requires_review = raw.requires_review or False

        # Check for low confidence indicators
        if raw.ambiguities and len(raw.ambiguities) > 3:
            requires_review = True

        validation_notes = []

        # Check for null commission values (unparsable or missing)
        null_commission_count = 0
        for product in products:
            for rule in product.commission_rules:
                if (
                    rule.outcome.commission_type == "percentage"
                    and rule.outcome.percentage_value is None
                ):
                    null_commission_count += 1

        if null_commission_count > 0:
            requires_review = True
            validation_notes.append(
                f"Found {null_commission_count} rules with unparsable/missing commission values. "
                "Manual review required to obtain actual rates."
            )

        # Add transformation warnings
        for warning in self.warnings:
            validation_notes.append(f"Transform warning: {warning}")

        # Add ambiguities
        if raw.ambiguities:
            for amb in raw.ambiguities:
                validation_notes.append(f"Ambiguity: {amb}")

        # Add transformation errors
        for error in self.errors:
            validation_notes.append(f"Transform error: {error}")
            requires_review = True

        # Calculate confidence
        confidence = 0.9
        if self.warnings:
            confidence -= 0.05 * min(len(self.warnings), 4)
        if raw.ambiguities:
            confidence -= 0.1 * min(len(raw.ambiguities), 3)
        if self.errors:
            confidence -= 0.2

        confidence = max(confidence, 0.3)

        return Validation(
            overall_confidence=round(confidence, 2),
            requires_manual_review=requires_review,
            validation_notes=validation_notes if validation_notes else None,
        )

    # ==================== UTILITIES ====================

    def _slugify(self, text: str) -> str:
        """Convert text to slug format."""
        # Remove special characters and replace spaces with underscores
        slug = re.sub(r"[^\w\s-]", "", text)
        slug = re.sub(r"[\s-]+", "_", slug)
        return slug.strip("_")

    def _parse_date(self, text: str) -> Optional[str]:
        """
        Parse various date formats to YYYY-MM-DD.

        Handles:
        - "January 1, 2024"
        - "01/01/2024"
        - "1st Jan 2024"
        - "2024-01-01"
        """
        if not text:
            return None

        text = text.strip()

        # Already in ISO format
        if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            return text

        # Try common formats
        formats = [
            "%B %d, %Y",  # January 1, 2024
            "%b %d, %Y",  # Jan 1, 2024
            "%d %B %Y",  # 1 January 2024
            "%d %b %Y",  # 1 Jan 2024
            "%d/%m/%Y",  # 01/01/2024
            "%m/%d/%Y",  # 01/01/2024 (US format)
            "%d-%m-%Y",  # 01-01-2024
            "%Y/%m/%d",  # 2024/01/01
        ]

        # Remove ordinal suffixes (1st, 2nd, 3rd, 4th, etc.)
        cleaned = re.sub(r"(\d+)(?:st|nd|rd|th)", r"\1", text)

        for fmt in formats:
            try:
                parsed = datetime.strptime(cleaned, fmt)
                return parsed.strftime("%Y-%m-%d")
            except ValueError:
                continue

        # Try extracting components
        month_map = {
            "jan": 1,
            "january": 1,
            "feb": 2,
            "february": 2,
            "mar": 3,
            "march": 3,
            "apr": 4,
            "april": 4,
            "may": 5,
            "jun": 6,
            "june": 6,
            "jul": 7,
            "july": 7,
            "aug": 8,
            "august": 8,
            "sep": 9,
            "september": 9,
            "oct": 10,
            "october": 10,
            "nov": 11,
            "november": 11,
            "dec": 12,
            "december": 12,
        }

        text_lower = text.lower()
        for month_name, month_num in month_map.items():
            if month_name in text_lower:
                year_match = re.search(r"20\d{2}", text)
                day_match = re.search(r"\b(\d{1,2})\b", text)

                if year_match:
                    year = int(year_match.group())
                    day = int(day_match.group(1)) if day_match else 1
                    return f"{year}-{month_num:02d}-{day:02d}"

        return None


# Convenience function
def transform_raw_to_cics(
    raw: RawExtraction,
) -> Tuple[CICSDocument, List[str], List[str]]:
    """
    Transform RawExtraction to CICSDocument.

    Args:
        raw: The raw extraction from AI

    Returns:
        Tuple of (CICSDocument, warnings, errors)
    """
    transformer = RawToCICSTransformer()
    return transformer.transform(raw)
