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

BOOKING_MODE_MAPPINGS = {
    "online": "online",
    "digital": "online",
    "web": "online",
    "app": "online",
    "offline": "offline",
    "branch": "offline",
    "agent": "offline",
    "partner": "offline",
    "misp": "offline",
    "broker": "offline",
    "direct": "offline",
}

BUSINESS_TYPE_MAPPINGS = {
    "new": "new",
    "new customer": "new",
    "new customers": "new",
    "first time": "new",
    "fresh": "new",
    "renewal": "renewal",
    "renewals": "renewal",
    "renew": "renewal",
    "existing": "renewal",
    "rollover": "rollover",
    "portability": "port",
    "port": "port",
}

POLICY_TYPE_MAPPINGS = {
    "comprehensive": "comprehensive",
    "comp": "comprehensive",
    "full": "comprehensive",
    "package": "comprehensive",
    "third-party": "third-party",
    "third party": "third-party",
    "tp": "third-party",
    "tp only": "third-party",
    "saod": "saod",
    "sa od": "saod",
    "stand alone od": "saod",
    "standalone od": "saod",
    "od-only": "saod",
    "od only": "saod",
    "own damage": "saod",
}

VEHICLE_SUB_CLASS_MAPPINGS = {
    "private car": "private-car",
    "private-car": "private-car",
    "pvt car": "private-car",
    "car": "private-car",
    "taxi": "taxi",
    "kaali peeli taxi": "kaali-peeli-taxi",
    "kaali-peeli-taxi": "kaali-peeli-taxi",
    "scooter": "scooter",
    "moped": "moped",
    "bike": "bike",
    "motorcycle": "bike",
    "e-bike": "e-bike",
    "e bike": "e-bike",
    "e rickshaw": "e-rickshaw",
    "e-rickshaw": "e-rickshaw",
    "auto rickshaw": "auto-rickshaw",
    "auto-rickshaw": "auto-rickshaw",
    "pick up/delivery van": "pick-up-delivery-van",
    "pick-up-delivery-van": "pick-up-delivery-van",
    "pickup van": "pick-up-delivery-van",
    "delivery van": "pick-up-delivery-van",
    "truck": "truck",
    "tractor": "tractor",
    "tipper": "tipper",
    "heavy truck/semi trailer": "heavy-truck-semi-trailer",
    "heavy-truck-semi-trailer": "heavy-truck-semi-trailer",
    "heavy truck": "heavy-truck-semi-trailer",
    "semi trailer": "heavy-truck-semi-trailer",
    "dumper": "dumper",
    "tanker": "tanker",
    "gas tanker": "gas-tanker",
    "gas-tanker": "gas-tanker",
    "trailer": "trailer",
    "flat bed trailer": "flat-bed-trailer",
    "flat-bed-trailer": "flat-bed-trailer",
    "refrigerated van": "refrigerated-van",
    "refrigerated-van": "refrigerated-van",
    "bulker": "bulker",
    "school bus": "school-bus",
    "school-bus": "school-bus",
    "passenger bus": "passenger-bus",
    "passenger-bus": "passenger-bus",
    "staff bus": "staff-bus",
    "staff-bus": "staff-bus",
    "agricultural tractor": "agricultural-tractor",
    "agricultural-tractor": "agricultural-tractor",
    "backhoe loader": "backhoe-loader",
    "backhoe-loader": "backhoe-loader",
    "crane": "crane",
    "ambulance": "ambulance",
    "loader": "loader",
    "tractor loader": "tractor-loader",
    "tractor-loader": "tractor-loader",
    "harvester": "harvester",
    "excavators": "excavators",
    "excavator": "excavators",
    "garbage van": "garbage-van",
    "garbage-van": "garbage-van",
    "motor grader": "motor-grader",
    "motor-grader": "motor-grader",
    "transit mixer": "transit-mixer",
    "transit-mixer": "transit-mixer",
    "fork lift trucks": "fork-lift-trucks",
    "fork-lift-trucks": "fork-lift-trucks",
    "forklift": "fork-lift-trucks",
    "generator van": "generator-van",
    "generator-van": "generator-van",
    "drilling rigs": "drilling-rigs",
    "drilling-rigs": "drilling-rigs",
    "compressors": "compressors",
    "compressor": "compressors",
    "other carts": "other-carts",
    "other-carts": "other-carts",
    "plane loaders and other": "plane-loaders-and-other",
    "plane-loaders-and-other": "plane-loaders-and-other",
    "road rollers": "road-rollers",
    "road-rollers": "road-rollers",
    "road roller": "road-rollers",
    "road sweeper": "road-sweeper",
    "road-sweeper": "road-sweeper",
    "car carrier": "car-carrier",
    "car-carrier": "car-carrier",
    "greater than six seater": "greater-than-six-seater",
    "greater-than-six-seater": "greater-than-six-seater",
    "not available": "not-available",
    "not-available": "not-available",
    "kisan suvidha": "kisan-suvidha",
    "kisan-suvidha": "kisan-suvidha",
    "container": "container",
    "towing van": "towing-van",
    "towing-van": "towing-van",
    "bulldozer": "bulldozer",
    "compactor": "compactor",
    "jetting machine": "jetting-machine",
    "jetting-machine": "jetting-machine",
    "fire vehicles": "fire-vehicles",
    "fire-vehicles": "fire-vehicles",
}

FUEL_TYPE_MAPPINGS = {
    "petrol": "petrol",
    "diesel": "diesel",
    "cng": "cng",
    "lpg": "lpg",
    "electric": "electric",
    "ev": "electric",
    # "Non Petrol" is a group label meaning Diesel + CNG + LPG — expanded at normalisation time
    "non petrol": "non-petrol",
    "non-petrol": "non-petrol",
}

NUMBER_OF_WHEELS_MAPPINGS = {
    "3w": "3w",
    "three wheeler": "3w",
    "three-wheeler": "3w",
    "3 wheeler": "3w",
    "4w": "4w-and-above",
    "4w & above": "4w-and-above",
    "4w and above": "4w-and-above",
    "4w-and-above": "4w-and-above",
    "four wheeler": "4w-and-above",
    "four-wheeler": "4w-and-above",
}

VEHICLE_CARRIER_MAPPINGS = {
    "public": "public",
    "private": "private",
    "commercial": "commercial",
}

SCHOOL_BUS_TYPE_MAPPINGS = {
    "on individual name": "on-individual-name",
    "on-individual-name": "on-individual-name",
    "individual": "on-individual-name",
    "on school name": "on-school-name",
    "on-school-name": "on-school-name",
    "school": "on-school-name",
    "on company name": "on-company-name",
    "on-company-name": "on-company-name",
    "company": "on-company-name",
    "on contract (transporter)": "on-contract-transporter",
    "on contract transporter": "on-contract-transporter",
    "on-contract-transporter": "on-contract-transporter",
    "contract": "on-contract-transporter",
    "transporter": "on-contract-transporter",
}

DEAL_PARAMETER_MAPPINGS = {
    "od": "od",
    "net": "net",
    "regular tp": "regular-tp",
    "regular-tp": "regular-tp",
    "tp with vehicle age": "tp-with-vehicle-age",
    "tp-with-vehicle-age": "tp-with-vehicle-age",
    "flat amount": "flat-amount",
    "flat-amount": "flat-amount",
    "ape": "ape",
}

# Maps raw product key substrings → canonical product name shown in output
# Longer/more-specific patterns first so they take priority
PRODUCT_NAME_MAPPINGS = [
    # Commercial Vehicle variants
    ("staff bus", "Commercial Vehicle"),
    ("school bus", "Commercial Vehicle"),
    ("passenger bus", "Commercial Vehicle"),
    ("mini bus", "Commercial Vehicle"),
    ("bus", "Commercial Vehicle"),
    ("gcv", "Commercial Vehicle"),
    ("goods carrying", "Commercial Vehicle"),
    ("pcv", "Commercial Vehicle"),
    ("passenger carrying", "Commercial Vehicle"),
    ("commercial vehicle", "Commercial Vehicle"),
    ("misc d", "Commercial Vehicle"),
    ("miscd", "Commercial Vehicle"),
    # Private Car variants
    ("four wheeler", "Private Car"),
    ("private car", "Private Car"),
    ("4w", "Private Car"),
    ("car", "Private Car"),
    ("taxi", "Private Car"),
    # Two Wheeler variants
    ("two wheeler", "Two Wheeler"),
    ("2w", "Two Wheeler"),
    ("bike", "Two Wheeler"),
    ("scooter", "Two Wheeler"),
    ("motorcycle", "Two Wheeler"),
    ("moped", "Two Wheeler"),
]

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
    "commercial vehicle": "motor",
    "health": "health",
    "mediclaim": "health",
    "life": "life",
    "term": "life",
    "travel": "travel",
    "home": "home",
    "fire": "other",
    "marine": "other",
}

# Valid Indian state names (canonical lowercase-hyphenated)
VALID_STATE_NAMES = {
    "andaman-and-nicobar",
    "andhra-pradesh",
    "arunachal-pradesh",
    "assam",
    "bihar",
    "chandigarh",
    "chattisgarh",
    "delhi-ncr",
    "goa",
    "gujarat",
    "haryana",
    "jammu-and-kashmir",
    "himachal-pradesh",
    "karnataka",
    "jharkhand",
    "kerala",
    "lakshadweep",
    "madhya-pradesh",
    "mizoram",
    "maharashtra",
    "meghalaya",
    "manipur",
    "nagaland",
    "orissa",
    "pondicherry",
    "rajasthan",
    "punjab",
    "sikkim",
    "tamil-nadu",
    "telangana",
    "tripura",
    "west-bengal",
    "uttar-pradesh",
    "ladakh",
    "uttarakhand",
    "dadra-and-nagar-haveli-and-daman-and-diu",
}

# Maps various state name/abbreviation forms to canonical names
STATE_NAME_MAPPINGS = {
    "andaman and nicobar": "andaman-and-nicobar",
    "andaman & nicobar": "andaman-and-nicobar",
    "a&n": "andaman-and-nicobar",
    "andhra pradesh": "andhra-pradesh",
    "arunachal pradesh": "arunachal-pradesh",
    "assam": "assam",
    "bihar": "bihar",
    "chandigarh": "chandigarh",
    "chhattisgarh": "chattisgarh",
    "chattisgarh": "chattisgarh",
    "delhi": "delhi-ncr",
    "delhi ncr": "delhi-ncr",
    "goa": "goa",
    "gujarat": "gujarat",
    "haryana": "haryana",
    "himachal pradesh": "himachal-pradesh",
    "jammu and kashmir": "jammu-and-kashmir",
    "jammu & kashmir": "jammu-and-kashmir",
    "jharkhand": "jharkhand",
    "karnataka": "karnataka",
    "kerala": "kerala",
    "ladakh": "ladakh",
    "lakshadweep": "lakshadweep",
    "madhya pradesh": "madhya-pradesh",
    "maharashtra": "maharashtra",
    "manipur": "manipur",
    "meghalaya": "meghalaya",
    "mizoram": "mizoram",
    "nagaland": "nagaland",
    "odisha": "orissa",
    "orissa": "orissa",
    "puducherry": "pondicherry",
    "pondicherry": "pondicherry",
    "punjab": "punjab",
    "rajasthan": "rajasthan",
    "sikkim": "sikkim",
    "tamil nadu": "tamil-nadu",
    "telangana": "telangana",
    "tripura": "tripura",
    "uttar pradesh": "uttar-pradesh",
    "uttarakhand": "uttarakhand",
    "west bengal": "west-bengal",
    "dadra and nagar haveli and daman and diu": "dadra-and-nagar-haveli-and-daman-and-diu",
    "dadra & nagar haveli and daman & diu": "dadra-and-nagar-haveli-and-daman-and-diu",
    "dadra and nagar haveli": "dadra-and-nagar-haveli-and-daman-and-diu",
    "dadra & nagar haveli": "dadra-and-nagar-haveli-and-daman-and-diu",
    "dadra and nagar": "dadra-and-nagar-haveli-and-daman-and-diu",
    "daman and diu": "dadra-and-nagar-haveli-and-daman-and-diu",
    "daman & diu": "dadra-and-nagar-haveli-and-daman-and-diu",
    "dnhdd": "dadra-and-nagar-haveli-and-daman-and-diu",
    "dd": "dadra-and-nagar-haveli-and-daman-and-diu",
    # 2-letter abbreviations
    "an": "andaman-and-nicobar",
    "ap": "andhra-pradesh",
    "ar": "arunachal-pradesh",
    "as": "assam",
    "br": "bihar",
    "ch": "chandigarh",
    "cg": "chattisgarh",
    "dl": "delhi-ncr",
    "ga": "goa",
    "gj": "gujarat",
    "hr": "haryana",
    "hp": "himachal-pradesh",
    "jk": "jammu-and-kashmir",
    "jh": "jharkhand",
    "ka": "karnataka",
    "kl": "kerala",
    "la": "ladakh",
    "ld": "lakshadweep",
    "mp": "madhya-pradesh",
    "mh": "maharashtra",
    "mn": "manipur",
    "ml": "meghalaya",
    "mz": "mizoram",
    "nl": "nagaland",
    "od": "orissa",
    "py": "pondicherry",
    "pb": "punjab",
    "rj": "rajasthan",
    "sk": "sikkim",
    "tn": "tamil-nadu",
    "ts": "telangana",
    "tr": "tripura",
    "up": "uttar-pradesh",
    "uk": "uttarakhand",
    "wb": "west-bengal",
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

        # Extract condition hints encoded in the product key (e.g. "TW SATP Scooter")
        key_policy_type = self._extract_policy_type_from_key(product_key)
        key_sub_class = self._extract_sub_class_from_key(product_key)

        # Transform each entry to a rule
        rules = []
        for i, entry in enumerate(entries):
            priority = priorities.get(i, (i + 1) * 10)
            rule = self._transform_rule(
                entry, product_code, i, priority, master_data, raw
            )
            # Backfill product-key hints only when conditions lack the field
            if rule.conditions:
                if key_policy_type and not rule.conditions.policy_type:
                    rule.conditions.policy_type = key_policy_type
                if key_sub_class and not rule.conditions.vehicle_sub_class:
                    rule.conditions.vehicle_sub_class = key_sub_class
            rules.append(rule)

        # Determine sub-category
        sub_category = None
        if entries:
            first_entry = entries[0]
            if first_entry.product_type:
                sub_category = first_entry.product_type.lower()

        # Detect product-level metadata from product key and entries
        product_subproduct = self._detect_product_subproduct(product_key, entries)
        vehicle_type = self._detect_vehicle_type(product_key, entries)
        product_name = self._normalize_product_name(product_key, entries)

        return Product(
            product_code=product_code,
            product_name=product_name,
            product_category=product_category,
            sub_category=sub_category,
            commission_rules=rules,
            product_subproduct=product_subproduct,
            vehicle_type=vehicle_type,
        )

    def _normalize_product_name(
        self, product_key: str, entries: List[RawCommissionEntry]
    ) -> str:
        """Normalize raw product key to a canonical product name.

        Prefers the canonical name extracted by the LLM in Pass 3 (stored in
        entry.product_name when it differs from the raw key). Falls back to
        static PRODUCT_NAME_MAPPINGS substring matching.
        """
        _canonical = {"Private Car", "Two Wheeler", "Commercial Vehicle"}
        for entry in entries:
            if entry.product_name and entry.product_name in _canonical:
                return entry.product_name
        key_lower = product_key.lower()
        for pattern, canonical in PRODUCT_NAME_MAPPINGS:
            if pattern in key_lower:
                return canonical
        return product_key

    def _extract_policy_type_from_key(self, product_key: str) -> Optional[List[str]]:
        """Extract policy type hint encoded in a product key (e.g. 'SATP', 'Comp', 'SAOD')."""
        key_lower = product_key.lower()
        # Order matters — check most specific first
        hints = [
            ("satp", "third-party"),
            ("saod", "saod"),
            ("comp", "comprehensive"),
            (" tp ", "third-party"),
            ("/tp", "third-party"),
            ("third party", "third-party"),
            ("third-party", "third-party"),
        ]
        for pattern, policy in hints:
            if pattern in key_lower:
                return [policy]
        return None

    def _extract_sub_class_from_key(self, product_key: str) -> Optional[List[str]]:
        """Extract vehicle sub-class hint encoded in a product key (e.g. 'Scooter', 'School Bus')."""
        key_lower = product_key.lower()
        # Longer patterns first to avoid partial matches
        hints = [
            ("school bus", "school-bus"),
            ("staff bus", "staff-bus"),
            ("passenger bus", "passenger-bus"),
            ("mini bus", "mini-bus"),
            ("agri tractor", "agricultural-tractor"),
            ("agricultural tractor", "agricultural-tractor"),
            ("scooter", "scooter"),
            ("e-rickshaw", "e-rickshaw"),
            ("e rickshaw", "e-rickshaw"),
        ]
        for pattern, sub_class in hints:
            if pattern in key_lower:
                return [sub_class]
        return None

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

    def _detect_product_subproduct(
        self, product_key: str, entries: List[RawCommissionEntry]
    ) -> Optional[str]:
        """Detect product-subproduct from product key."""
        key_lower = product_key.lower()
        # Longer/more-specific patterns first
        mappings = [
            ("two wheeler", "two-wheeler"),
            ("2w", "two-wheeler"),
            ("goods carrying", "commercial-gcv"),
            ("gcv", "commercial-gcv"),
            ("gccv", "commercial-gcv"),
            ("passenger carrying", "commercial-pcv"),
            ("pcv", "commercial-pcv"),
            ("pccv", "commercial-pcv"),
            ("staff bus", "commercial-buses"),
            ("school bus", "commercial-buses"),
            ("passenger bus", "commercial-buses"),
            ("mini bus", "commercial-buses"),
            ("bus", "commercial-buses"),
            ("misc d", "commercial-miscd"),
            ("miscd", "commercial-miscd"),
            ("msv", "commercial-miscd"),
            ("tractor", "commercial-miscd"),
            ("tipper", "commercial-miscd"),
            ("truck", "commercial-miscd"),
            ("car", "car"),
        ]
        for pattern, value in mappings:
            if pattern in key_lower:
                return value
        # Fallback: check entries product_name for canonical names
        for entry in entries:
            if entry.product_name == "Two Wheeler":
                return "two-wheeler"
            if entry.product_name == "Commercial Vehicle":
                return None  # need subproduct context — can't determine without more info
            if entry.product_name == "Private Car":
                return "car"
        return None

    def _detect_vehicle_type(
        self, product_key: str, entries: List[RawCommissionEntry]
    ) -> Optional[str]:
        """Detect vehicle type from product key."""
        key_lower = product_key.lower()
        mappings = {
            "car": "car-taxi",
            "taxi": "car-taxi",
            "two wheeler": "two-wheeler",
            "2w": "two-wheeler",
            "bike": "two-wheeler",
            "scooter": "two-wheeler",
            "gcv": "gcv",
            "goods carrying": "gcv",
            "pcv": "pcv",
            "passenger carrying": "pcv",
            "bus": "buses",
            "misc": "misc-d",
        }
        for pattern, value in mappings.items():
            if pattern in key_lower:
                return value
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
        if entry.coverage_type_text:
            parts.append(self._slugify(entry.coverage_type_text)[:3].upper())

        # Ensure uniqueness with index
        parts.append(f"R{index + 1}")

        return "_".join(parts)

    def _has_conditions(self, conditions: RuleConditions) -> bool:
        """Check if conditions object has any non-null values."""
        return any(
            [
                conditions.vehicle_sub_class,
                conditions.policy_type,
                conditions.state,
                conditions.rto,
                conditions.number_of_wheels,
                conditions.gvw_start_range is not None,
                conditions.gvw_end_range is not None,
                conditions.cc_start_range is not None,
                conditions.cc_end_range is not None,
                conditions.seating_capacity_start_range is not None,
                conditions.seating_capacity_end_range is not None,
                conditions.school_bus_type,
                conditions.booking_mode,
                conditions.zero_dep is not None,
                conditions.ncb is not None,
                conditions.cpa is not None,
                conditions.fuel_type,
                conditions.vehicle_age is not None,
                conditions.vehicle_make,
                conditions.vehicle_model,
                conditions.vehicle_carrier,
                conditions.business_type,
                conditions.od_term is not None,
                conditions.tp_term is not None,
                conditions.deal_parameter,
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
        # Normalize top-level text fields
        booking_mode = self._normalize_list_field(
            entry.channel_text, BOOKING_MODE_MAPPINGS, "booking_mode"
        )
        business_type = self._normalize_list_field(
            entry.customer_type_text, BUSINESS_TYPE_MAPPINGS, "business_type"
        )
        policy_type = self._normalize_list_field(
            entry.coverage_type_text, POLICY_TYPE_MAPPINGS, "policy_type"
        )

        # Normalize vehicle sub-class from global_vehicle_type_text
        vehicle_sub_class = self._normalize_list_field(
            getattr(raw, "global_vehicle_type_text", None),
            VEHICLE_SUB_CLASS_MAPPINGS,
            "vehicle_sub_class",
        )

        # Normalize states
        location_text = entry.state_text or getattr(raw, "global_state_text", None)
        state = self._expand_state_names(location_text, master_data)

        # Extract/expand RTO codes from location_text
        rto = self._expand_rto_codes(location_text, master_data)

        # Extract domain-specific fields from entry.conditions dict
        entry_conditions = getattr(entry, "conditions", None) or {}

        # Fuel type — expand "non-petrol" group to its component fuels
        fuel_type = self._normalize_list_field(
            entry_conditions.get("fuel_type"), FUEL_TYPE_MAPPINGS, "fuel_type"
        )
        if fuel_type and "non-petrol" in fuel_type:
            fuel_type = sorted(set(fuel_type) - {"non-petrol"} | {"diesel", "cng", "lpg"})

        # Number of wheels
        number_of_wheels = self._normalize_list_field(
            entry_conditions.get("number_of_wheels"),
            NUMBER_OF_WHEELS_MAPPINGS,
            "number_of_wheels",
        )

        # Vehicle carrier
        vehicle_carrier = self._normalize_list_field(
            entry_conditions.get("vehicle_carrier"),
            VEHICLE_CARRIER_MAPPINGS,
            "vehicle_carrier",
        )

        # School bus type
        school_bus_type = self._normalize_list_field(
            entry_conditions.get("school_bus_type"),
            SCHOOL_BUS_TYPE_MAPPINGS,
            "school_bus_type",
        )

        # Deal parameter
        deal_parameter = self._normalize_list_field(
            entry_conditions.get("deal_parameter"),
            DEAL_PARAMETER_MAPPINGS,
            "deal_parameter",
        )

        # Vehicle sub-class from conditions dict (may override global)
        cond_vehicle_sub_class = self._normalize_list_field(
            entry_conditions.get("vehicle_sub_class"),
            VEHICLE_SUB_CLASS_MAPPINGS,
            "vehicle_sub_class",
        )
        if cond_vehicle_sub_class:
            vehicle_sub_class = cond_vehicle_sub_class

        # NCB (boolean)
        ncb = self._parse_bool_field(entry_conditions.get("ncb"))

        # CPA
        cpa = self._normalize_single_field(
            entry_conditions.get("cpa"),
            {"with cpa": "with-cpa", "with-cpa": "with-cpa", "without cpa": "without-cpa", "without-cpa": "without-cpa", "yes": "with-cpa", "no": "without-cpa"},
            "cpa",
        )

        # Zero dep
        zero_dep = self._normalize_single_field(
            entry_conditions.get("zero_dep"),
            {"yes": "yes", "no": "no", "true": "yes", "false": "no"},
            "zero_dep",
        )

        # Vehicle make/model (free-text arrays)
        vehicle_make = self._parse_text_list(entry_conditions.get("vehicle_make"))
        vehicle_model = self._parse_text_list(entry_conditions.get("vehicle_model"))

        # RTO from conditions dict — expand categories via master data.
        # The LLM may use "rto", "rto_category", "rto_zone", or similar — accept any.
        cond_rto_text = None
        for k in ("rto", "rto_category", "rto_zone", "rto_categories", "rto_cluster"):
            v = entry_conditions.get(k)
            if v:
                cond_rto_text = v if cond_rto_text is None else f"{cond_rto_text}, {v}"
        if cond_rto_text:
            expanded = self._expand_rto_codes(cond_rto_text, master_data)
            if expanded:
                rto = sorted(set((rto or []) + expanded))
            else:
                raw_items = self._parse_text_list(cond_rto_text)
                if raw_items:
                    rto = sorted(set((rto or []) + raw_items))

        # Integer range fields
        gvw_start, gvw_end = self._parse_range_field(entry_conditions.get("gvw"))
        cc_start, cc_end = self._parse_range_field(entry_conditions.get("cc"))
        seat_start, seat_end = self._parse_range_field(
            entry_conditions.get("seating_capacity")
            or getattr(raw, "global_seating_capacity_text", None)
        )

        # Vehicle age
        vehicle_age = self._parse_int_field(entry_conditions.get("vehicle_age"))

        # OD term / TP term
        od_term = self._parse_int_field(entry_conditions.get("od_term"))
        tp_term = self._parse_int_field(entry_conditions.get("tp_term"))

        return RuleConditions(
            vehicle_sub_class=vehicle_sub_class,
            policy_type=policy_type,
            state=state,
            rto=rto,
            number_of_wheels=number_of_wheels,
            gvw_start_range=gvw_start,
            gvw_end_range=gvw_end,
            cc_start_range=cc_start,
            cc_end_range=cc_end,
            seating_capacity_start_range=seat_start,
            seating_capacity_end_range=seat_end,
            school_bus_type=school_bus_type,
            booking_mode=booking_mode,
            zero_dep=zero_dep,
            ncb=ncb,
            cpa=cpa,
            fuel_type=fuel_type,
            vehicle_age=vehicle_age,
            vehicle_make=vehicle_make,
            vehicle_model=vehicle_model,
            vehicle_carrier=vehicle_carrier,
            business_type=business_type,
            od_term=od_term,
            tp_term=tp_term,
            deal_parameter=deal_parameter,
        )

    # ==================== NORMALIZATION HELPERS ====================

    def _normalize_list_field(
        self, text: Optional[str], mappings: Dict[str, str], field_name: str
    ) -> Optional[List[str]]:
        """Normalize a free-text value to a list of enum values using mappings."""
        if not text:
            return None

        normalized = []
        text_lower = text.lower().strip()

        # Sort by key length descending so longer keys match first (e.g., "new customer" before "new")
        sorted_keys = sorted(mappings.keys(), key=len, reverse=True)
        for key in sorted_keys:
            value = mappings[key]
            # Use word-boundary matching for short keys to avoid substring false positives
            if len(key) <= 5:
                pattern = rf"(?:^|[\s,;/\-])({re.escape(key)})(?=$|[\s,;/\-])"
            else:
                pattern = re.escape(key)
            if re.search(pattern, text_lower) and value not in normalized:
                normalized.append(value)

        if not normalized:
            self.warnings.append(f"Unknown {field_name}: {text}")
            return None

        return normalized

    def _normalize_single_field(
        self, text: Optional[str], mappings: Dict[str, str], field_name: str
    ) -> Optional[str]:
        """Normalize a free-text value to a single enum value."""
        if not text:
            return None

        text_lower = text.lower().strip()

        for key, value in mappings.items():
            if key in text_lower:
                return value

        self.warnings.append(f"Unknown {field_name}: {text}")
        return None

    def _parse_bool_field(self, text: Optional[str]) -> Optional[bool]:
        """Parse a text value to a boolean."""
        if text is None:
            return None
        if isinstance(text, bool):
            return text

        text_lower = str(text).lower().strip()
        if text_lower in ("yes", "true", "1", "y"):
            return True
        if text_lower in ("no", "false", "0", "n"):
            return False

        return None

    def _parse_int_field(self, text: Optional[str]) -> Optional[int]:
        """Parse a text value to an integer."""
        if text is None:
            return None
        if isinstance(text, (int, float)):
            return int(text)

        match = re.search(r"(\d+)", str(text))
        if match:
            return int(match.group(1))
        return None

    def _parse_text_list(self, text: Optional[str]) -> Optional[List[str]]:
        """Parse a comma-separated text or list value into a string list."""
        if not text:
            return None
        if isinstance(text, list):
            return text if text else None

        items = [item.strip() for item in str(text).split(",") if item.strip()]
        return items if items else None

    def _parse_range_field(
        self, text: Optional[str]
    ) -> Tuple[Optional[int], Optional[int]]:
        """Parse a range text into start/end integers."""
        if text is None:
            return None, None
        if isinstance(text, dict):
            return text.get("min"), text.get("max")

        text_str = str(text).strip()

        # Pattern: "1-2500" or "1 - 2500" or "1 to 2500"
        range_match = re.search(
            r"(\d+)\s*[-–to]+\s*(\d+)", text_str, re.IGNORECASE
        )
        if range_match:
            return int(range_match.group(1)), int(range_match.group(2))

        # Pattern: "> 12" or ">= 12" or "above 12"
        above_match = re.search(
            r"(?:>|>=|above|greater than|more than)\s*(\d+)", text_str, re.IGNORECASE
        )
        if above_match:
            return int(above_match.group(1)), None

        # Pattern: "< 12" or "<= 12"
        below_match = re.search(r"(?:<|<=|below|less than)\s*(\d+)", text_str, re.IGNORECASE)
        if below_match:
            return None, int(below_match.group(1))

        # Single number
        single_match = re.match(r"^\d+$", text_str)
        if single_match:
            val = int(text_str)
            return val, val

        return None, None

    def _expand_state_names(
        self, state_text: Optional[str], master_data: Optional[List[RawMasterDataEntry]]
    ) -> Optional[List[str]]:
        """
        Expand state text to canonical lowercase-hyphenated state names.
        Handles exclusion patterns by expanding to the complement set.
        """
        if not state_text:
            if master_data:
                for m in master_data:
                    if m.entry_type == "state_mapping" and m.values:
                        return self._expand_state_names(m.values, None)
            return None

        text = state_text.lower()
        discovered_names = []

        # Handle exclusions ("All states except...")
        if "except" in text or "excluding" in text or "other than" in text:
            exclude_pattern = (
                r"(?:except|excluding|other than)\s+([^,;]+(?:[,;][^,;]+)*)"
            )
            match = re.search(exclude_pattern, state_text, re.IGNORECASE)
            if match:
                exclude_text = match.group(1)
                excluded_names = self._expand_state_names(exclude_text, None)
                if excluded_names:
                    return sorted(VALID_STATE_NAMES - set(excluded_names))

        # Proactive master data expansion
        if master_data:
            upper_text = state_text.upper()
            for m in master_data:
                if not m.key:
                    continue
                key_upper = m.key.upper()
                if re.search(rf"\b{re.escape(key_upper)}\b", upper_text):
                    if m.values:
                        names = self._expand_state_names(m.values, None)
                        if names:
                            discovered_names.extend(names)

        # Standard state name/abbreviation mapping
        for name, canonical in STATE_NAME_MAPPINGS.items():
            if len(name) <= 3:
                pattern = rf"(?:^|[\s,_\-\/]){re.escape(name)}(?=$|[\s,_\-\/])"
            else:
                pattern = rf"{re.escape(name)}"

            if re.search(pattern, text, re.IGNORECASE):
                discovered_names.append(canonical)

        if discovered_names:
            return sorted(set(discovered_names))

        return None

    def _expand_rto_codes(
        self, rto_text: Optional[str], master_data: Optional[List[RawMasterDataEntry]]
    ) -> Optional[List[str]]:
        """
        Expand RTO text to canonical RTO codes.
        Resolves category labels (CAT 0, Zone A, Category 1) via rto_mapping master data entries.
        Accepts both hyphenated (MH-01) and concatenated (MH01) code formats.
        """
        if not rto_text:
            return None

        code_pattern = r"\b[A-Z]{2}-?\d{1,3}\b"

        def _normalize_label(s: str) -> str:
            # Collapse common abbreviations and whitespace so that
            # "CAT 0", "Category 0", "category-0", "cat0" all normalize equivalently.
            s = s.upper()
            s = re.sub(r"\bCATEGORY\b", "CAT", s)
            s = re.sub(r"\bZONES?\b", "ZONE", s)
            s = re.sub(r"\bTIERS?\b", "TIER", s)
            s = re.sub(r"\bCLUSTERS?\b", "CLUSTER", s)
            s = re.sub(r"\bGROUPS?\b", "GROUP", s)
            s = re.sub(r"[-_]", " ", s)
            s = re.sub(r"\s+", " ", s).strip()
            return s

        literal_codes = re.findall(code_pattern, rto_text.upper())
        discovered = list(literal_codes)

        if master_data:
            normalized_text = _normalize_label(rto_text)
            for m in master_data:
                if m.entry_type != "rto_mapping" or not m.key or not m.values:
                    continue
                key_norm = _normalize_label(m.key)
                if not key_norm:
                    continue
                if re.search(rf"\b{re.escape(key_norm)}\b", normalized_text):
                    codes = re.findall(code_pattern, m.values.upper())
                    discovered.extend(codes)

        if not discovered:
            return None

        return sorted(set(discovered))

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

    def _normalize_amount_text(self, text: str) -> str:
        """Strip currency prefixes and normalise amount text for regex parsing."""
        return re.sub(r"(?i)rs\.?\s*|inr\s*|₹\s*", "", text).strip()

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
