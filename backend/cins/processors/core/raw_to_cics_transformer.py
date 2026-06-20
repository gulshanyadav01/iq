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

import json
import re
import hashlib
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any

from cins.schema.carriers import CARRIER_FULL_NAMES, normalize_carrier
from cins.schema.raw_extraction import (
    RawExtraction,
    RawCommissionEntry,
    RawMasterDataEntry,
)
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
    QualifyingClause,
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
    "pack": "comprehensive",  # Cholamandalam: PACK = comprehensive
    "third-party": "third-party",
    "third party": "third-party",
    "tp": "third-party",
    "tp only": "third-party",
    "satp": "third-party",
    "act": "third-party",  # Cholamandalam: ACT = third-party (act-only)
    "saod": "saod",
    "sa od": "saod",
    "stand alone od": "saod",
    "standalone od": "saod",
    "sod": "saod",  # Cholamandalam: SOD = standalone OD
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
        # Short carrier code (e.g. "Chola") for carrier-scoped transforms.
        # Set per-document in transform(); empty until then.
        self._carrier_code: str = ""

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
        # Resolve the carrier code once for carrier-scoped transforms (Fix i).
        self._carrier_code = (
            normalize_carrier(getattr(raw.provider, "name", "") or "") or ""
        )

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
        """
        Transform provider from raw extraction.

        Normalises whatever the LLM returned (or the alias the codegen path
        passes through) to one of the 25 canonical short codes via
        `cins.schema.carriers.normalize_carrier`. Anything that fails to map
        becomes `UNKNOWN_PROVIDER` and a transformer warning, so downstream
        callers can flag it.
        """
        raw_name = (raw_provider.name or "").strip()
        short_code = normalize_carrier(raw_name)

        if short_code:
            # `provider_id` must match ^[A-Za-z0-9_-]+$ per CICS schema —
            # spaces in canonical codes (e.g. "New India") are converted to
            # underscores for the ID, but the human-facing `provider_name`
            # uses the canonical full name from the alias table.
            return Provider(
                provider_id=short_code.replace(" ", "_"),
                provider_name=CARRIER_FULL_NAMES.get(short_code, short_code),
            )

        # No alias match — keep the original string visible (so a human can
        # see what the LLM returned) but flag the doc as needing review.
        if raw_name:
            self.warnings.append(
                f"Provider '{raw_name}' did not match any of the 25 valid carriers; "
                f"setting provider_id=UNKNOWN_PROVIDER."
            )
        return Provider(
            provider_id="UNKNOWN_PROVIDER",
            provider_name=raw_name or "Unknown Provider",
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

        # Detect product-level metadata up-front so we can gate per-rule
        # backfills (e.g. wheel-category extraction) by subproduct.
        product_subproduct = self._detect_product_subproduct(product_key, entries)
        vehicle_type = self._detect_vehicle_type(product_key, entries)

        # Compute priorities based on specificity
        priorities = self._compute_priorities(entries)

        # Extract condition hints encoded in the product key (e.g. "TW SATP Scooter")
        key_policy_type = self._extract_policy_type_from_key(product_key)
        key_sub_class = self._extract_sub_class_from_key(product_key)
        # Wheel hint is only meaningful for commercial vehicles. "4W" in a
        # Private Car product name does not mean number_of_wheels, it means
        # the product itself.
        # LLM Pass 3 normalises product_name to the canonical "Commercial
        # Vehicle" label and strips slug suffixes, so wheel info baked into
        # the original sheet header (e.g. ``1_GCCV_3W``) survives only in
        # entries' source_section or in the raw global_vehicle_type_text.
        is_commercial = bool(
            product_subproduct and product_subproduct.startswith("commercial-")
        )
        product_key_wheels = (
            self._extract_wheels_from_key(product_key) if is_commercial else None
        )
        global_wheels = (
            self._extract_wheels_from_key(
                getattr(raw, "global_vehicle_type_text", "") or ""
            )
            if is_commercial
            else None
        )

        # Transform each entry to a rule
        rules = []
        for i, entry in enumerate(entries):
            priority = priorities.get(i, (i + 1) * 10)
            rule = self._transform_rule(
                entry, product_code, i, priority, master_data, raw
            )
            # Backfill product-key hints. Materialise an empty RuleConditions
            # on rules that had none so commercial-vehicle wheel inference
            # can land. Without this, `_transform_rule` drops the conditions
            # object whenever the LLM emitted only outcome/text fields, and
            # downstream bifurcation never sees the rule.
            # The GCV section token (e.g. "1_3W_AUTO", "1_TRAC", "2_EXCAVATOR")
            # commonly survives only in the rule's free text, not in
            # source_section, so probe both for wheel- and sub-class hints.
            section = getattr(entry, "source_section", "") or ""
            rule_text = getattr(entry, "rule_text", "") or ""
            entry_wheels = None
            entry_sub_class = None
            if is_commercial:
                for hint_src in (section, rule_text):
                    if not hint_src:
                        continue
                    entry_wheels = entry_wheels or self._extract_wheels_from_key(
                        hint_src
                    )
                    entry_sub_class = (
                        entry_sub_class or self._extract_sub_class_from_key(hint_src)
                    )
            inferred_wheels = (
                entry_wheels or product_key_wheels or global_wheels
                if is_commercial
                else None
            )
            inferred_sub_class = key_sub_class or entry_sub_class
            needs_backfill = bool(
                key_policy_type or inferred_sub_class or inferred_wheels
            )
            # GCV with no wheel hint anywhere must still surface a
            # conditions object so bifurcation fans it out into 3W + 4W.
            if product_subproduct == "commercial-gcv" and not inferred_wheels:
                needs_backfill = True
            if needs_backfill and rule.conditions is None:
                rule.conditions = RuleConditions()
            if rule.conditions is not None:
                if key_policy_type and not rule.conditions.policy_type:
                    rule.conditions.policy_type = key_policy_type
                if inferred_sub_class and not rule.conditions.vehicle_sub_class:
                    rule.conditions.vehicle_sub_class = inferred_sub_class
                if inferred_wheels and not rule.conditions.number_of_wheels:
                    rule.conditions.number_of_wheels = inferred_wheels
            rules.append(rule)

        # De-overlap GVW / CC / seating-capacity boundaries within groups of
        # rules that share every other condition. Source documents express
        # buckets with shared boundaries ("UPTO 3.5T" / "3.5T TO 7.5T"), which
        # the parser maps to (0,3500) and (3500,7500). Downstream consumers
        # treat ranges as inclusive on both ends, so a vehicle at 3500 would
        # match two rules. Bump each successive start to prev_end + 1 so the
        # ladder is monotonic and gap-free.
        # Collapse identical-condition + identical-outcome duplicates before
        # boundary dedupe. LLM extraction often emits the same logical rule
        # multiple times with slightly different vehicle_make values (one per
        # source-table row); leaving them confuses the boundary-bump walk,
        # which can leave the second duplicate at the un-bumped boundary
        # (invert-guard hits).
        rules = self._collapse_duplicate_rules(rules)

        self._dedupe_range_overlaps(rules)

        # Evidence-gated cross-group boundary normalization (D1=B). Closes the
        # gap when a CC/GVW ladder is fragmented across states/RTOs so the
        # per-group bump above can't see the lower rung. Runs before GCV
        # bifurcation so wheel clones inherit corrected boundaries.
        self._normalize_boundary_overlaps(rules)

        # GCV rules must be scoped to a single number_of_wheels value.
        # Source documents commonly publish one GCV row that implicitly
        # covers both 3W and 4W & Above (or lists both in one cell), but
        # downstream deal management requires one deal per wheel category.
        if product_subproduct == "commercial-gcv":
            rules = self._bifurcate_gcv_wheels(rules)

        # Dimension-collapse guard: a commercial-vehicle product whose every
        # rule still carries the (0, 0) "no GVW filter" sentinel almost always
        # means the GVW dimension was lost in extraction (and _recover_gvw_range
        # could not recover it). Surface it so the next silently-dropped
        # dimension is visible instead of shipping wrong commission tiers.
        if (
            product_subproduct
            and product_subproduct.startswith("commercial")
            and rules
            and all(
                r.conditions
                and getattr(r.conditions, "gvw_start_range", 0) == 0
                and getattr(r.conditions, "gvw_end_range", 0) == 0
                for r in rules
            )
        ):
            self.warnings.append(
                f"Dimension-collapse: all {len(rules)} rules in commercial "
                f"product '{product_key}' have no GVW band — possible lost "
                f"weight dimension"
            )

        # Determine sub-category
        sub_category = None
        if entries:
            first_entry = entries[0]
            if first_entry.product_type:
                sub_category = first_entry.product_type.lower()

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
        # Longer/more-specific patterns first to avoid partial matches.
        # The bare tokens (school / staff / trac / excavator / harvest / maxi)
        # match the abbreviated GCV section keys Chola bakes into the row text
        # (e.g. "1_TRAC", "2_STAFF", "2_EXCAVATOR", "3_HARVESTOR", "4_MAXI").
        # These special-purpose CV bodies carry no GVW band and no wheel token,
        # so without a sub-class hint they collapse into the (0,0) catch-all and
        # get cross-cloned into 3W by bifurcation. The matched sub-class also
        # routes them to 4W-and-Above only (see _is_four_wheel_only_subclass).
        hints = [
            ("school bus", "school-bus"),
            ("staff bus", "staff-bus"),
            ("passenger bus", "passenger-bus"),
            ("mini bus", "mini-bus"),
            ("agri tractor", "agricultural-tractor"),
            ("agricultural tractor", "agricultural-tractor"),
            ("school", "school-bus"),
            ("staff", "staff-bus"),
            ("excavator", "excavator"),
            ("harvest", "harvester"),
            ("maxi", "maxicab"),
            ("trac", "tractor"),
            ("scooter", "scooter"),
            ("e-rickshaw", "e-rickshaw"),
            ("e rickshaw", "e-rickshaw"),
        ]
        for pattern, sub_class in hints:
            if pattern in key_lower:
                return [sub_class]
        return None

    # Stems of special-purpose CV bodies that are always 4-Wheel-and-Above
    # (never 3-wheelers). A wheel-less rule scoped to one of these must
    # bifurcate to 4W only — cloning it to 3W produces spurious rows and (0,0)
    # catch-alls with conflicting rates. Matched by substring so plurals and
    # variants from either our extractor or the LLM codegen all resolve (e.g.
    # "excavator" / "excavators", "tractor" / "agricultural-tractor").
    _FOUR_WHEEL_ONLY_SUBCLASS_STEMS = (
        "bus",
        "tractor",
        "excavator",
        "harvest",
        "maxicab",
        "maxi-cab",
        "dumper",
        "tipper",
        "trailer",
        "tanker",
        "crane",
    )

    @classmethod
    def _is_four_wheel_only_subclass(cls, conditions) -> bool:
        """True when conditions carry a vehicle_sub_class that is 4W-only."""
        if conditions is None:
            return False
        sub = getattr(conditions, "vehicle_sub_class", None)
        if not sub:
            return False
        values = sub if isinstance(sub, (list, tuple)) else [sub]
        return any(
            stem in str(v).lower()
            for v in values
            for stem in cls._FOUR_WHEEL_ONLY_SUBCLASS_STEMS
        )

    def _extract_wheels_from_key(self, product_key: str) -> Optional[List[str]]:
        """Extract number_of_wheels hint encoded in a commercial-vehicle product key.

        Source documents commonly publish CV grids with the wheel category
        baked into the sheet/section header, e.g. ``"1_GCCV_3W"``,
        ``"GCCV 4W & ABOVE"``, ``"PCV 3-Wheeler"``. The wheel value never
        appears as a per-row condition in such grids, so without this
        extractor the entire sheet's rules end up with no number_of_wheels.

        Callers must gate this on a commercial subproduct: "4W" inside a
        Private Car product key means the product itself, not a wheel count.
        """
        # Use a tokenised match so substrings inside other tokens don't trip
        # the pattern (e.g. "g4w" or "34W"). Word-ish boundaries treat
        # underscores, hyphens, spaces, and slashes as separators.
        boundary = r"(?:^|[\s_\-/])"
        after = r"(?=$|[\s_\-/])"
        key = product_key.lower()

        three_patterns = [
            rf"{boundary}3\s*w{after}",
            rf"{boundary}3\s*-?\s*wheeler{after}",
            rf"{boundary}three\s*-?\s*wheeler{after}",
        ]
        four_patterns = [
            rf"{boundary}4\s*w(?:\s*(?:&|and)\s*above)?{after}",
            rf"{boundary}4\s*-?\s*wheeler{after}",
            rf"{boundary}four\s*-?\s*wheeler{after}",
        ]

        wheels: List[str] = []
        if any(re.search(p, key) for p in three_patterns):
            wheels.append("3w")
        if any(re.search(p, key) for p in four_patterns):
            wheels.append("4w-and-above")
        return wheels or None

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
        # Fallback 1: probe each entry's product_type and source_section
        # (LLM Pass 3 normalises product_name to the canonical label and
        # strips the original slug — but the slug usually survives on
        # product_type ("GCV") or source_section ("1_GCCV_3W")).
        for entry in entries:
            for source in (
                getattr(entry, "product_type", "") or "",
                getattr(entry, "source_section", "") or "",
            ):
                if not source:
                    continue
                source_lower = source.lower()
                for pattern, value in mappings:
                    if pattern in source_lower:
                        return value
        # Fallback 2: canonical product names (TW / Car derivable; CV needs
        # subproduct context which the patterns above already exhausted).
        for entry in entries:
            if entry.product_name == "Two Wheeler":
                return "two-wheeler"
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

        # Fuel type — expand grid-group tokens to their component fuels.
        fuel_type = self._normalize_list_field(
            entry_conditions.get("fuel_type"), FUEL_TYPE_MAPPINGS, "fuel_type"
        )
        fuel_type = self._expand_fuel_group(fuel_type)

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

        # Deal parameter — fall back to applied_on_text or global_applied_on
        # so every rule has one of: od, net, regular-tp, tp-with-vehicle-age, flat-amount, ape
        deal_parameter_text = (
            entry_conditions.get("deal_parameter")
            or entry.applied_on_text
            or getattr(raw, "global_applied_on", None)
        )
        deal_parameter = self._normalize_list_field(
            deal_parameter_text,
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
            {
                "with cpa": "with-cpa",
                "with-cpa": "with-cpa",
                "without cpa": "without-cpa",
                "without-cpa": "without-cpa",
                "yes": "with-cpa",
                "no": "without-cpa",
            },
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
        if gvw_start is None and gvw_end is None:
            gvw_start, gvw_end = 0, 0
        elif gvw_start is None:
            gvw_start = 0
        elif gvw_end is None:
            # Open-ended upper bound ("ABOVE 40T") — use the canonical
            # gvwRange ceiling from PRODUCT_FIELD_VALUES.md (999999 kg).
            gvw_end = 999999

        # GVW recovery: when the parse above hit the (0, 0) "no GVW filter"
        # sentinel, the weight band may still be baked into a sub-class slug
        # ("2_UPTO_3.5T"). Codegen stores grid dims under conditions["subclass"]
        # — a key the transformer never reads — so the band is dropped at the
        # key level on the codegen path. Recover it deterministically here.
        if gvw_start == 0 and gvw_end == 0:
            recovered = self._recover_gvw_range(entry_conditions, raw)
            if recovered is not None:
                gvw_start, gvw_end = recovered

        cc_start, cc_end = self._parse_range_field(entry_conditions.get("cc"))
        if cc_start is None and cc_end is None:
            cc_start, cc_end = 0, 99999
        elif cc_start is None:
            cc_start = 0
        elif cc_end is None:
            cc_end = 99999

        seat_start, seat_end = self._parse_range_field(
            entry_conditions.get("seating_capacity")
            or getattr(raw, "global_seating_capacity_text", None)
        )
        if seat_start is None and seat_end is None:
            seat_start, seat_end = 0, 99
        elif seat_start is None:
            seat_start = 0
        elif seat_end is None:
            seat_end = 99

        # Policies count (NOP) — number of policies sold tier. Common Chola TW
        # qualifier on row labels: "NEW(UPTO 30 NOP)", "NEW(30-100 NOP)",
        # "NEW(100-500 NOP)". When absent the rule applies to any count, so
        # default to (0, 999999) — the canonical open range used elsewhere.
        nop_start, nop_end = self._parse_range_field(
            entry_conditions.get("policies_count")
        )
        if nop_start is None and nop_end is None:
            nop_start, nop_end = 0, 999999
        elif nop_start is None:
            nop_start = 0
        elif nop_end is None:
            nop_end = 999999

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
            policies_count_start_range=nop_start,
            policies_count_end_range=nop_end,
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

        # Mask matched spans so shorter overlapping keys cannot re-match inside a
        # longer match. Without this, "Non Petrol" → "non petrol" matches AND
        # "petrol" matches the substring within, producing both "non-petrol" and
        # "petrol" in the output. Use a sentinel char that no key contains.
        masked = list(text_lower)
        sentinel = "\x00"

        # Sort by key length descending so longer keys match first (e.g., "new customer" before "new")
        sorted_keys = sorted(mappings.keys(), key=len, reverse=True)
        for key in sorted_keys:
            value = mappings[key]
            # Use word-boundary matching for short keys to avoid substring false positives
            if len(key) <= 5:
                pattern = rf"(?:^|[\s,;/\-])({re.escape(key)})(?=$|[\s,;/\-])"
                group_index = 1
            else:
                pattern = re.escape(key)
                group_index = 0

            current = "".join(masked)
            for match in re.finditer(pattern, current):
                if value not in normalized:
                    normalized.append(value)
                start, end = match.span(group_index)
                for i in range(start, end):
                    masked[i] = sentinel

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
        """Parse a range text into start/end integers.

        Handles decimals and tonnage units. Tonnage suffixes (T, TON, TONNE,
        TONNES, MT) trigger a ×1000 conversion so commercial-vehicle GVW
        labels like "UPTO 3.5T" land on 3500 kg, matching the canonical
        gvwRange buckets in PRODUCT_FIELD_VALUES.md. Underscore separators
        from indexer slugs ("3.5T_TO_7.5T") are normalised to whitespace
        before pattern matching.
        """
        if text is None:
            return None, None
        if isinstance(text, dict):
            return text.get("min"), text.get("max")

        text_str = str(text).strip()
        if not text_str:
            return None, None

        # Normalise indexer slug separators ("3.5T_TO_7.5T" -> "3.5T TO 7.5T").
        normalized = text_str.replace("_", " ")

        # Tonnage detection: a digit immediately followed (optional whitespace)
        # by T / TON / TONNE / TONNES / MT at a word boundary. Word-boundary
        # guard prevents "TO", "TRACTOR", "TOTAL" from triggering.
        tonne_pattern = r"\d\s*(?:TONNES|TONNE|TONS|TON|MT|T)\b"
        multiplier = 1000 if re.search(tonne_pattern, normalized, re.IGNORECASE) else 1

        # Strip tonnage units attached to numbers so the range regexes see
        # plain decimals ("3.5T TO 7.5T" -> "3.5 TO 7.5").
        stripped = re.sub(
            r"(\d+(?:\.\d+)?)\s*(?:TONNES|TONNE|TONS|TON|MT|T)\b",
            r"\1",
            normalized,
            flags=re.IGNORECASE,
        )

        def _to_int(num: str) -> int:
            return int(round(float(num) * multiplier))

        # Pattern: "1-2500", "1 – 2500", "1 to 2500", "3.5 TO 7.5".
        range_match = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)",
            stripped,
            re.IGNORECASE,
        )
        if range_match:
            return _to_int(range_match.group(1)), _to_int(range_match.group(2))

        # Pattern: "> 12", ">= 12", "above 12", "above 20T".
        above_match = re.search(
            r"(?:>=|>|above|greater than|more than)\s*(\d+(?:\.\d+)?)",
            stripped,
            re.IGNORECASE,
        )
        if above_match:
            return _to_int(above_match.group(1)), None

        # Pattern: "< 12", "<= 12", "upto 12", "up to 12", "UPTO 3.5T".
        below_match = re.search(
            r"(?:<=|<|below|less than|upto|up to)\s*(\d+(?:\.\d+)?)",
            stripped,
            re.IGNORECASE,
        )
        if below_match:
            return None, _to_int(below_match.group(1))

        # Single bare number (optionally decimal). Use fullmatch so we don't
        # accidentally swallow a fragment from a larger string.
        single_match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", stripped)
        if single_match:
            val = _to_int(single_match.group(1))
            return val, val

        return None, None

    # Non-range condition fields used to decide which rules share a "group"
    # for boundary de-overlap. Two rules collide on a range only if every
    # other dimension matches — same logic as RuleOverlapChecker.
    # vehicle_make and vehicle_model are deliberately excluded: source docs
    # commonly use them for sentinel values ("All Other Make/Models") and
    # indexer-slug leakage ("2_UPTO_3.5T[Electric]") that split otherwise-
    # identical ladder rules into separate groups, defeating boundary dedupe.
    # Real make-specific commission rules are rare; the validator will still
    # surface any genuine make/model overlap.
    _DEDUPE_LIST_FIELDS = (
        "vehicle_sub_class",
        "policy_type",
        "state",
        "rto",
        "number_of_wheels",
        "school_bus_type",
        "booking_mode",
        "fuel_type",
        "vehicle_carrier",
        "business_type",
        "deal_parameter",
    )
    _DEDUPE_SCALAR_FIELDS = (
        "zero_dep",
        "cpa",
        "ncb",
        "vehicle_age",
        "od_term",
        "tp_term",
    )
    # (start_field, end_field) tuples to de-overlap. The "other" ranges in
    # this list participate in the group key, so different CC buckets don't
    # get conflated when we bump GVW boundaries.
    _DEDUPE_RANGES = (
        ("gvw_start_range", "gvw_end_range"),
        ("cc_start_range", "cc_end_range"),
        ("seating_capacity_start_range", "seating_capacity_end_range"),
        ("policies_count_start_range", "policies_count_end_range"),
    )

    # Open-range ceilings per range type. When a ladder ends with a bare
    # singleton (e.g. CC header "350cc" meaning ">350"), promotion extends
    # its end to this ceiling — matching the defaults used in
    # _build_conditions when a range field is absent entirely.
    _RANGE_CEILINGS = {
        "gvw_end_range": 999999,
        "cc_end_range": 99999,
        "seating_capacity_end_range": 99,
        "policies_count_end_range": 999999,
    }

    # Sentinel (start, end) pairs that mean "no filter on this dimension".
    # _transform_conditions emits these when the source entry omits the
    # field entirely. They must NOT participate in dedupe — bumping their
    # start would shift the boundary of an unrelated real-range rule that
    # shares the same default lower bound (e.g. a (0, 0) gvw sentinel next
    # to a real (0, 3500) gvw rule would push the real rule to (1, 3500)).
    _RANGE_SENTINELS = {
        ("gvw_start_range", "gvw_end_range"): {(0, 0)},
        ("cc_start_range", "cc_end_range"): {(0, 99999)},
        ("seating_capacity_start_range", "seating_capacity_end_range"): {(0, 99)},
        ("policies_count_start_range", "policies_count_end_range"): {(0, 999999)},
    }

    # Canonical GCV wheel categories per PRODUCT_FIELD_VALUES.md.
    def _expand_fuel_group(self, fuel_type):
        """Expand grid-group fuel tokens to their component fuels.

        Default (all carriers): the diesel grid token "non-petrol" expands to
        {diesel, cng, lpg} — the historical behavior.

        Chola (Cholamandalam): CNG/LPG/Electric follow the *Petrol* grid, not the
        Diesel grid (the carrier's grid-alias rule, confirmed by client
        reconciliation). So for Chola the diesel grid is diesel-only and the
        petrol grid carries the alternative fuels:
            [D] non-petrol -> {diesel}
            [P] petrol     -> {petrol, cng, lpg, electric}

        Idempotent: the "non-petrol" group token is consumed on first pass, and
        re-adding the petrol set is a stable union, so a second pass is a no-op.
        Returns the input unchanged when no group token is present.
        """
        if not fuel_type:
            return fuel_type
        fuels = set(fuel_type)
        if self._carrier_code == "Chola":
            changed = False
            if "non-petrol" in fuels:
                fuels = (fuels - {"non-petrol"}) | {"diesel"}
                changed = True
            if "petrol" in fuels:
                fuels |= {"petrol", "cng", "lpg", "electric"}
                changed = True
            return sorted(fuels) if changed else fuel_type
        # Default carriers: preserve historical non-petrol -> {diesel, cng, lpg}.
        if "non-petrol" in fuels:
            return sorted((fuels - {"non-petrol"}) | {"diesel", "cng", "lpg"})
        return fuel_type

    _GCV_WHEEL_VALUES = ("3w", "4w-and-above")
    _GCV_WHEEL_SUFFIX = {"3w": "3W", "4w-and-above": "4WAA"}

    # GVW upper bounds at/above this are treated as "all-GVW" (open-ended), not
    # a real weight grade. Real Chola GCV bands top out at 47000kg; client
    # all-GVW rows use 999999. Anything in between is a genuine weight grade.
    _ALL_GVW_END_THRESHOLD = 90000

    @classmethod
    def _is_weight_graded_band(cls, conditions) -> bool:
        """True when conditions carry a real (bounded) GVW weight grade.

        A goods 3-wheeler (auto) is never weight-graded — only 4-wheel LCVs and
        trucks carry GVW bands. So a wheel-less rule with any genuine band, light
        (``0-3500``) or heavy (``3500-7500`` …), belongs to 4W-and-Above only.
        Cloning such a rule to 3W produced spurious 3W rows the client never
        publishes (client 3W exists only as all-GVW auto). The ``(0,0)`` "no
        band" sentinel and open all-GVW bands are NOT weight grades and still
        fan out to both wheel categories.
        """
        if conditions is None:
            return False
        start = getattr(conditions, "gvw_start_range", None)
        end = getattr(conditions, "gvw_end_range", None)
        try:
            start = int(start) if start is not None else 0
            end = int(end) if end is not None else 0
        except (TypeError, ValueError):
            return False
        if start > 0:
            return True  # lower-bounded heavy band
        # start == 0: a finite positive upper bound is a real light grade
        # (e.g. 0-3500). end == 0 is the sentinel; a very large end is all-GVW.
        return 0 < end < cls._ALL_GVW_END_THRESHOLD

    def _bifurcate_gcv_wheels(
        self, rules: List[CommissionRule]
    ) -> List[CommissionRule]:
        """Expand each GCV rule to one rule per canonical wheel category.

        Bifurcates when number_of_wheels is missing/empty (rule applies to
        both 3W and 4W & Above) or contains more than one value. Rules
        already scoped to a single wheel category pass through unchanged.

        GVW-aware (Fix iii): a wheel-less rule carrying a *real heavy* GVW band
        (``gvw_start_range > 0`` — a lower-bounded band above the light/3W
        range) maps to 4W-and-Above only. A 3-wheeler cannot carry a band that
        starts above zero, so cloning it to 3W produced spurious rows (e.g. 3W
        paired with a 40-tonne band) and (0,0) catch-alls with conflicting
        rates. Bands that start at 0 (all-GVW, light bands like 0-3500, or the
        (0,0) unknown sentinel) still fan out to both categories. An explicit
        multi-value ``number_of_wheels`` is authoritative and always respected.

        Cloned rules inherit priority, outcome, dates, and description from
        the source; rule_id gets a wheel-category suffix to stay unique.
        """
        expanded: List[CommissionRule] = []
        for rule in rules:
            wheels = rule.conditions.number_of_wheels if rule.conditions else None
            if wheels and len(wheels) == 1:
                expanded.append(rule)
                continue

            if wheels:
                # Explicit multi-value list — authoritative, fan to each.
                targets = wheels
            elif self._is_four_wheel_only_subclass(rule.conditions):
                # Wheel-less special-purpose body (tractor, bus, excavator,
                # harvester, maxicab) -> 4W & Above only. These carry no GVW
                # band; cloning them to 3W produced the (0,0) catch-alls with
                # conflicting rates.
                targets = ["4w-and-above"]
            elif self._is_weight_graded_band(rule.conditions):
                # Wheel-less + any real GVW weight grade (light or heavy) ->
                # 4W & Above only. 3-wheelers are never weight-graded.
                targets = ["4w-and-above"]
            else:
                targets = list(self._GCV_WHEEL_VALUES)
            for wheel in targets:
                if rule.conditions is not None:
                    cloned_conditions = rule.conditions.model_copy(deep=True)
                else:
                    cloned_conditions = RuleConditions()
                cloned_conditions.number_of_wheels = [wheel]
                suffix = self._GCV_WHEEL_SUFFIX.get(wheel, wheel.upper())
                expanded.append(
                    CommissionRule(
                        rule_id=f"{rule.rule_id}_{suffix}",
                        priority=rule.priority,
                        conditions=cloned_conditions,
                        outcome=rule.outcome,
                        valid_from=rule.valid_from,
                        valid_to=rule.valid_to,
                        rule_description=rule.rule_description,
                    )
                )
        return expanded

    # Condition fields excluded from duplicate-collapse identity. Same
    # rationale as _DEDUPE_LIST_FIELDS: vehicle_make / vehicle_model commonly
    # carry sentinel ("All Other Make/Models") or indexer-slug junk that
    # makes two otherwise-identical rules appear distinct.
    _COLLAPSE_IGNORE_CONDITION_FIELDS = ("vehicle_make", "vehicle_model")

    def _collapse_duplicate_rules(
        self, rules: List[CommissionRule]
    ) -> List[CommissionRule]:
        """Drop rules with identical conditions (modulo make/model) AND
        identical outcome, keeping the highest-priority duplicate (lowest
        priority number). Preserves rule order otherwise.

        Lossless de-duplication: if two rules have different outcomes the
        collapse skips them so rule_overlap_checker can surface the genuine
        ambiguity. Source: TW July sheet emitted 1953 duplicate rules
        differing only by vehicle_make values like 'Suzuki', 'Honda',
        'Others' on identical commission ladders.
        """
        if not rules:
            return rules

        seen: Dict[str, int] = {}
        winning_priority: Dict[str, int] = {}
        keep = [True] * len(rules)

        for i, rule in enumerate(rules):
            key = self._duplicate_collapse_key(rule)
            if key not in seen:
                seen[key] = i
                winning_priority[key] = rule.priority
                continue
            if rule.priority < winning_priority[key]:
                keep[seen[key]] = False
                seen[key] = i
                winning_priority[key] = rule.priority
            else:
                keep[i] = False

        return [r for r, k in zip(rules, keep) if k]

    def _duplicate_collapse_key(self, rule: CommissionRule) -> str:
        """Build a stable string key from a rule's conditions+outcome for
        duplicate collapse. Excludes make/model fields from conditions.
        """
        cond_dump: Dict[str, Any] = {}
        if rule.conditions is not None:
            cond_dump = rule.conditions.model_dump(exclude_none=False)
            for field in self._COLLAPSE_IGNORE_CONDITION_FIELDS:
                cond_dump.pop(field, None)
            # Normalize list ordering so [a, b] and [b, a] collapse.
            for k, v in list(cond_dump.items()):
                if isinstance(v, list):
                    cond_dump[k] = sorted(v, key=str)

        outcome_dump: Dict[str, Any] = {}
        if rule.outcome is not None:
            outcome_dump = rule.outcome.model_dump(exclude_none=False)

        return json.dumps(
            {"conditions": cond_dump, "outcome": outcome_dump},
            sort_keys=True,
            default=str,
        )

    def _dedupe_range_overlaps(self, rules: List[CommissionRule]) -> None:
        """Bump successive start = prev_end + 1 for GVW/CC/seating/policies
        ranges within rules that share all other conditions. Mutates rules
        in place.

        Also promotes ladder-edge singletons: if the lowest rule in a group
        has start == end and shares its boundary with the next rule's
        start, it's the "UPTO N" bucket — extend its start to 0. Same for
        the highest rule against the prior rule's end — extend to the
        canonical ceiling. Source: Chola TW July grid encodes CC buckets
        as headers "150cc | 150_350cc | 350cc" where the bare singletons
        are open-ended boundary buckets, not point ranges.
        """
        for start_field, end_field in self._DEDUPE_RANGES:
            sentinels = self._RANGE_SENTINELS.get((start_field, end_field), set())
            groups: Dict[Tuple, List[CommissionRule]] = {}
            for rule in rules:
                if not rule.conditions:
                    continue
                start = getattr(rule.conditions, start_field, None)
                end = getattr(rule.conditions, end_field, None)
                if start is None or end is None:
                    continue
                # Skip "no filter" placeholders so they don't shift adjacent
                # real-range rules.
                if (start, end) in sentinels:
                    continue
                key = self._dedupe_group_key(rule.conditions, start_field, end_field)
                groups.setdefault(key, []).append(rule)

            ceiling = self._RANGE_CEILINGS.get(end_field)

            for group_rules in groups.values():
                if len(group_rules) < 2:
                    continue
                group_rules.sort(
                    key=lambda r: (
                        getattr(r.conditions, start_field),
                        getattr(r.conditions, end_field),
                    )
                )

                # Ladder-edge singleton promotion (before overlap-bump so
                # the bump step normalises the resulting shared boundary).
                first = group_rules[0].conditions
                first_start = getattr(first, start_field)
                first_end = getattr(first, end_field)
                if (
                    first_start == first_end
                    and getattr(group_rules[1].conditions, start_field) == first_end
                ):
                    setattr(first, start_field, 0)

                last = group_rules[-1].conditions
                last_start = getattr(last, start_field)
                last_end = getattr(last, end_field)
                if (
                    ceiling is not None
                    and last_start == last_end
                    and getattr(group_rules[-2].conditions, end_field) == last_start
                ):
                    setattr(last, end_field, ceiling)

                prev_end = getattr(group_rules[0].conditions, end_field)
                for rule in group_rules[1:]:
                    start = getattr(rule.conditions, start_field)
                    end = getattr(rule.conditions, end_field)
                    if start <= prev_end:
                        new_start = prev_end + 1
                        # Don't create an inverted range; skip the bump
                        # and let validator surface the genuine overlap.
                        if new_start <= end:
                            setattr(rule.conditions, start_field, new_start)
                            start = new_start
                    prev_end = max(prev_end, end)

    def _dedupe_group_key(
        self, conditions: RuleConditions, skip_start: str, skip_end: str
    ) -> Tuple:
        """Build a hashable key from all non-range conditions plus the
        OTHER two range fields. The range field being de-overlapped is
        excluded so its buckets get grouped together.
        """
        parts: List[Any] = []
        for field in self._DEDUPE_LIST_FIELDS:
            value = getattr(conditions, field, None)
            parts.append(tuple(sorted(value)) if value else None)
        for field in self._DEDUPE_SCALAR_FIELDS:
            parts.append(getattr(conditions, field, None))
        for start_field, end_field in self._DEDUPE_RANGES:
            if start_field == skip_start:
                continue
            parts.append(getattr(conditions, start_field, None))
            parts.append(getattr(conditions, end_field, None))
        return tuple(parts)

    # Fields dropped from the evidence-group key for cross-group boundary
    # normalization. One vehicle's CC/GVW ladder is commonly split across
    # states / RTO zones with identical engine bands; dropping these regroups
    # the fragmented ladder so a shared tier boundary present anywhere in the
    # same product+coverage counts as evidence. make/model already excluded
    # from _DEDUPE_LIST_FIELDS for the same reason.
    _BOUNDARY_DROP_FIELDS = ("state", "rto")

    def _boundary_group_key(
        self, conditions: RuleConditions, skip_start: str, skip_end: str
    ) -> Tuple:
        """Relaxed _dedupe_group_key: same key minus the fields that legitimately
        fragment a single vehicle's range ladder (state, rto, make, model). Used
        only by _normalize_boundary_overlaps so a tier boundary present anywhere
        in the same product+coverage is treated as evidence — without collapsing
        genuinely-different vehicle contexts (fuel, sub_class, wheels stay)."""
        parts: List[Any] = []
        for field in self._DEDUPE_LIST_FIELDS:
            if field in self._BOUNDARY_DROP_FIELDS:
                continue
            value = getattr(conditions, field, None)
            parts.append(tuple(sorted(value)) if value else None)
        for field in self._DEDUPE_SCALAR_FIELDS:
            parts.append(getattr(conditions, field, None))
        for start_field, end_field in self._DEDUPE_RANGES:
            if start_field == skip_start:
                continue
            parts.append(getattr(conditions, start_field, None))
            parts.append(getattr(conditions, end_field, None))
        return tuple(parts)

    def _normalize_boundary_overlaps(self, rules: List[CommissionRule]) -> None:
        """Evidence-gated boundary normalization (D1=B). Mutates rules in place.

        Closes the gap left by _dedupe_range_overlaps when a CC/GVW ladder is
        fragmented across states/RTOs so the per-group bump can't see the lower
        rung. A band's start is bumped to start+1 ONLY when that start value
        coincides with some band's END within the same product+coverage ladder
        (evidence the boundary is a real shared tier edge). A start that merely
        equals an unrelated context's end is left alone — the relaxed-but-not-
        empty group key prevents the unsound global flip.

        Floor point-range promotion (X,X) -> (0,X) only when a sibling band in
        the same group starts at X with a wider end (the real ladder rung) — a
        lone (X,X) point rate is never silently widened. Every change is logged.
        """
        for start_field, end_field in self._DEDUPE_RANGES:
            sentinels = self._RANGE_SENTINELS.get((start_field, end_field), set())
            groups: Dict[Tuple, List[CommissionRule]] = {}
            for rule in rules:
                if not rule.conditions:
                    continue
                start = getattr(rule.conditions, start_field, None)
                end = getattr(rule.conditions, end_field, None)
                if start is None or end is None:
                    continue
                if (start, end) in sentinels:
                    continue
                key = self._boundary_group_key(rule.conditions, start_field, end_field)
                groups.setdefault(key, []).append(rule)

            for group_rules in groups.values():
                if len(group_rules) < 2:
                    continue
                # Snapshot original boundaries so evidence is order-independent.
                pairs = [
                    (
                        getattr(r.conditions, start_field),
                        getattr(r.conditions, end_field),
                    )
                    for r in group_rules
                ]
                ends = {e for _s, e in pairs}
                # Values where a wider band starts — confirms a ladder floor.
                floor_starts = {s for s, e in pairs if e != s}

                for rule in group_rules:
                    cond = rule.conditions
                    start = getattr(cond, start_field)
                    end = getattr(cond, end_field)
                    # Floor point-range: (X,X) with a sibling band starting at X.
                    if start == end and start > 0 and start in floor_starts:
                        setattr(cond, start_field, 0)
                        self.warnings.append(
                            f"Boundary floor-promotion {start_field} "
                            f"({start},{end}) -> (0,{end})"
                        )
                        continue
                    # Evidence-gated bump: start sits on another band's end.
                    if 0 < start < end and start in ends:
                        new_start = start + 1
                        setattr(cond, start_field, new_start)
                        self.warnings.append(
                            f"Boundary bump {start_field} {start} -> {new_start} "
                            f"(shared tier edge in ladder)"
                        )

    def _gvw_slug_or_empty(self, header: Optional[str]) -> str:
        """Return the verbatim label if it encodes a GVW range (tonnage / kg /
        qualified weight), else ''. Mirrors code_gen_executor.parse_gvw_range so
        the deterministic recovery path applies the same shape-guard — CC/KW
        engine-displacement headers are rejected (they belong to cc)."""
        if header is None:
            return ""
        h = str(header).strip()
        if not h:
            return ""
        hu = h.upper().replace("_", " ")
        if not any(c.isdigit() for c in hu):
            return ""
        if (
            "CC" in hu.split()
            or hu.endswith("CC")
            or "KW" in hu.split()
            or hu.endswith("KW")
        ):
            return ""
        if "GVW" in hu or "KG" in hu:
            return h
        # Tonnage scan: a digit immediately followed by 'T' not part of another
        # word (so "SEATER", "TRACTOR", "TO" don't trigger).
        i = 0
        while i < len(hu):
            if hu[i] == "T":
                j = i - 1
                while j >= 0 and hu[j] == " ":
                    j -= 1
                prev_digit = j >= 0 and hu[j].isdigit()
                nxt = hu[i + 1] if i + 1 < len(hu) else ""
                if prev_digit and not nxt.isalpha():
                    return h
            i += 1
        qualifiers = (
            "UPTO",
            "UP TO",
            "ABOVE",
            "BELOW",
            "GREATER THAN",
            "MORE THAN",
            "LESS THAN",
            ">",
            "<",
        )
        if any(q in hu for q in qualifiers):
            return h
        return ""

    def _recover_gvw_range(
        self, entry_conditions: Dict[str, Any], raw: RawExtraction
    ) -> Optional[Tuple[int, int]]:
        """Recover a GVW (start, end) from sub-class slugs when conditions["gvw"]
        was absent. Codegen stores grid dims under conditions["subclass"] (a key
        the transformer otherwise ignores) and weight bands are sometimes baked
        into the sub-class slug ("2_UPTO_3.5T"). Shape-guarded via
        _gvw_slug_or_empty so a real sub-class ("3 WHEELER", "16 SEATER") is
        never misread. Open ends normalise to 0 / 999999. Returns None on miss."""
        candidates = [
            entry_conditions.get("subclass"),
            entry_conditions.get("vehicle_sub_class"),
            getattr(raw, "global_vehicle_type_text", None),
        ]
        for cand in candidates:
            slug = self._gvw_slug_or_empty(cand)
            if not slug:
                continue
            rs, re_ = self._parse_range_field(slug)
            if rs is None and re_ is None:
                continue
            start = 0 if rs is None else rs
            end = 999999 if re_ is None else re_
            self.warnings.append(
                f"GVW recovered from sub-class slug '{cand}' -> ({start}, {end})"
            )
            return start, end
        return None

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

        # Proactive master data expansion — only for state-grouping entries.
        # Skip 'declined' and other non-state entries whose values may contain
        # exclusion phrases that would over-expand to all states.
        if master_data:
            upper_text = state_text.upper()
            for m in master_data:
                if not m.key:
                    continue
                if m.entry_type not in ("state_mapping", "rto_mapping"):
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

        # Preserve raw free-text conditions/footnotes (e.g. "if CPA is not
        # collected for individual name, 1.5% of OD Pay-out is deducted").
        # These are qualifying rules from notes/master sections that the
        # structured Deduction parser cannot model. Surface them verbatim
        # so downstream consumers (UI, export, manual review) keep the
        # information.
        other_conditions_parts = []
        entry_other = (entry.other_conditions or "").strip()
        if entry_other:
            other_conditions_parts.append(entry_other)
        global_other = (getattr(raw, "global_other_conditions", "") or "").strip()
        if global_other and global_other not in other_conditions_parts:
            other_conditions_parts.append(global_other)
        if other_conditions_parts:
            outcome_dict["other_conditions"] = " | ".join(other_conditions_parts)

        # Structured machine-ingestable representation of qualifying clauses
        # (applied_on overrides, conditional deductions, grid aliases). The LLM
        # emits these in Pass 3 alongside other_conditions; codegen path leaves
        # them empty. Validate each dict against QualifyingClause so malformed
        # entries don't pollute the CICS output.
        raw_clauses = getattr(entry, "qualifying_clauses", None) or []
        validated_clauses: List[QualifyingClause] = []
        for raw_clause in raw_clauses:
            try:
                validated_clauses.append(QualifyingClause.model_validate(raw_clause))
            except Exception as e:
                self.warnings.append(f"Dropping malformed qualifying_clause: {e}")
        if validated_clauses:
            outcome_dict["qualifying_clauses"] = validated_clauses

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
                # An explicit '%' sign means percentage. Values > 100 are almost
                # always upstream bugs (e.g. fraction multiplied by 100 twice:
                # 0.65 -> 65 -> "6500%"). Flag for manual review instead of
                # silently flipping units to flat INR.
                self.warnings.append(
                    f"Commission value '{value}' exceeds 100% — likely an upstream "
                    f"unit error; flagging for manual review"
                )
                self.errors.append(
                    f"Commission value '{value}' > 100% requires manual review"
                )
                return "percentage", {
                    "percentage_value": None,
                    "calculation_notes": (
                        f"UNPARSED: {value} exceeds 100% - REQUIRES MANUAL REVIEW"
                    ),
                }
            return "percentage", {"percentage_value": pct_value}

        # Plain decimal/number without % sign (e.g., "0.26" means 26%, "26" means 26%)
        plain_number_pattern = r"^(\d*\.?\d+)$"
        plain_match = re.match(plain_number_pattern, value.strip())
        if plain_match:
            num_value = float(plain_match.group(1))
            # If value < 1, assume it's a decimal representation (0.26 = 26%)
            if num_value < 1:
                return "percentage", {"percentage_value": num_value * 100}
            # Plain number > 100 with no currency marker is ambiguous — could be
            # a flat INR amount or a unit error. Flag for manual review rather
            # than guessing a currency.
            elif num_value > 100:
                self.warnings.append(
                    f"Commission value '{value}' exceeds 100 with no currency or "
                    f"% marker — flagging for manual review"
                )
                self.errors.append(
                    f"Ambiguous commission value '{value}' requires manual review"
                )
                return "percentage", {
                    "percentage_value": None,
                    "calculation_notes": (
                        f"UNPARSED: {value} ambiguous (>100, no unit) - REQUIRES MANUAL REVIEW"
                    ),
                }
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
