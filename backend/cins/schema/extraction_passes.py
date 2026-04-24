"""
Multi-Pass Extraction Schemas

Defines simplified schemas for multi-pass extraction approach:
- Pass 0A: EmailCleaningExtraction - Clean email content (optional, email sources only)
- Pass 0B: MasterDataCleaningExtraction - Clean and normalize master data sections
- Pass 1: MetadataExtraction - Document metadata, provider, period, global settings
- Pass 2: ProductCatalogExtraction - Product list and master data
- Pass 3: RulesExtraction - Commission rules for a single product

Each pass has a smaller, focused schema to reduce LLM cognitive load and improve
extraction accuracy. Passes are then merged into RawExtraction format.
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


# ============================================================================
# PASS 0A: EMAIL CLEANING (PREPROCESSING)
# ============================================================================


class EmailCleaningExtraction(BaseModel):
    """
    Pass 0A: Clean email content to extract only commission-relevant data.

    Focus: Remove noise (headers, signatures, disclaimers, thread history),
           preserve commission data.
    Ignore: Actual extraction (handled in later passes).
    """

    cleaned_content: str = Field(
        ...,
        description="Email content with headers, signatures, thread history, and disclaimers removed. Only commission-relevant content remains.",
    )

    removed_sections: List[str] = Field(
        [],
        description="Types of content removed (e.g., 'email headers', 'signature', 'legal disclaimer', 'thread history')",
    )

    tables_found: int = Field(
        0, description="Number of tables identified in email content"
    )

    attachments_mentioned: List[str] = Field(
        [], description="Attachments mentioned in email (filenames only)"
    )

    cleaning_notes: str = Field(
        "",
        description="Notes about cleaning process, content structure, or issues encountered",
    )

    class Config:
        json_schema_extra = {
            "title": "Pass 0A: Email Content Cleaning",
            "description": "Clean email content by removing noise and preserving commission data only",
        }


# ============================================================================
# PASS 0B: MASTER DATA CLEANING (PREPROCESSING)
# ============================================================================


class MasterDataCleaningExtraction(BaseModel):
    """
    Pass 0B: Clean and normalize master data sections.

    Focus: Extract master data in clean, normalized format for Pass 2.
           Separate master data from commission rules.
    Ignore: Actual master data extraction (handled in Pass 2).
    """

    cleaned_content: str = Field(
        ...,
        description="Full cleaned content with master data normalized and separated from commission rules",
    )

    master_data_sections: List[str] = Field(
        [],
        description="Cleaned master data sections identified (e.g., 'RTO Mappings Table', 'Declined States List')",
    )

    master_data_table_count: int = Field(
        0, description="Number of master data tables found"
    )

    commission_data_sections: List[str] = Field(
        [], description="Sections containing commission rules (preserved for Pass 3)"
    )

    normalization_notes: str = Field(
        "",
        description="Notes about normalization, cleaning applied, or issues encountered",
    )

    class Config:
        json_schema_extra = {
            "title": "Pass 0B: Master Data Cleaning",
            "description": "Clean and normalize master data sections for better extraction",
        }


# ============================================================================
# PASS 0C: CSV TABLE DETECTION (PREPROCESSING)
# ============================================================================


class DetectedTable(BaseModel):
    """
    A single table detected within a CSV sheet.
    The AI returns row boundaries only — Python slices the original CSV lines.
    """

    table_name: str = Field(
        ...,
        description=(
            "Descriptive name for this table derived from its header or preceding section label. "
            "Examples: '4W Comprehensive New Business', '2W TP Renewal', 'GCV 7.5T-12T'. "
            "Use the product/segment name if visible in a title row or header."
        ),
    )

    table_type: str = Field(
        ...,
        description=(
            "Classification of the table's purpose. Must be one of: "
            "'commission' (contains actual commission rates/percentages to extract as rules), "
            "'master' (lookup/reference table that defines mappings or categories, e.g., state→zone, "
            "state→preferred/non-preferred, RTO codes, vehicle classifications — does NOT contain commission rates), "
            "'notes' (free-text notes, terms, conditions, disclaimers, or payout rules that apply to the commission "
            "tables — e.g., 'Please note that...', SAOD rules, CPA requirements, minimum payout clauses)."
        ),
    )

    table_index: int = Field(
        ...,
        description="1-based index of this table within the sheet (1 = first table detected).",
    )

    start_row: int = Field(
        ...,
        description=(
            "1-based line number in the CSV where this table's header (or preceding title row) begins. "
            "Refers to the line numbers shown in the input."
        ),
    )

    end_row: int = Field(
        ...,
        description="1-based line number in the CSV where this table ends (inclusive).",
    )

    start_col: Optional[int] = Field(
        None,
        description="Optional 1-based column number where this table starts. Use for side-by-side tables.",
    )

    end_col: Optional[int] = Field(
        None,
        description="Optional 1-based column number where this table ends (inclusive). Use for side-by-side tables.",
    )

    description: str = Field(
        "", description="Brief description of what this table covers."
    )

    confidence: float = Field(
        1.0,
        description="Confidence that this is a distinct table (0.0-1.0). Below 0.7 flags for review.",
    )

    detection_notes: str = Field(
        "", description="Notes about how boundaries were identified or any ambiguities."
    )


class CSVTableDetectionExtraction(BaseModel):
    """
    Pass 0C: Detect multiple distinct tables within a single CSV sheet.

    Focus: Identify table boundaries (start/end row numbers) for each distinct
           commission table. Python will slice the original CSV using these boundaries.
    Ignore: Interpreting commission rates or business rules.
    """

    sheet_name: str = Field(..., description="Name of the sheet being analyzed.")

    tables: List[DetectedTable] = Field(
        ...,
        description=(
            "List of distinct tables found. If the sheet contains only one coherent table, "
            "return a list with a single entry covering the full row range."
        ),
    )

    total_tables_found: int = Field(
        ...,
        description="Total number of distinct tables detected. Must equal len(tables).",
    )

    sheet_has_multiple_tables: bool = Field(
        ..., description="True only if more than one distinct table was detected."
    )

    detection_notes: str = Field(
        "", description="Overall notes about the sheet structure or detection process."
    )

    class Config:
        json_schema_extra = {
            "title": "Pass 0C: CSV Table Detection",
            "description": "Detect distinct commission table boundaries within a single CSV sheet",
        }


# ============================================================================
# PASS 1: METADATA EXTRACTION
# ============================================================================


class MetadataExtraction(BaseModel):
    """
    Pass 1: Extract document-level metadata and global settings.

    Focus: Provider info, period, global settings that apply to all rules.
    Ignore: Products and commission rules (handled in later passes).
    """

    # Source information
    source_file: str = Field("", description="Original filename")
    source_type: str = Field("", description="Type of source: 'email', 'pdf', 'excel'")
    extraction_notes: str = Field(
        "", description="General notes about the document structure"
    )

    # Provider - simple flat structure
    provider_name: str = Field(..., description="Provider name as stated in document")

    # Commission period - simple strings
    start_date: str = Field(
        ...,
        description="Start date in any recognizable format (e.g., 'January 1, 2024', '2024-01-01')",
    )
    end_date: str = Field("", description="End date if specified")
    period_label: str = Field(
        "",
        description="Human-readable period label (e.g., 'Q1 2024', 'January 2024')",
    )

    # Global settings that apply to all rules
    global_gst_percentage: str = Field(
        "", description="Default GST if mentioned globally (e.g., '18%')"
    )
    global_deductions: str = Field(
        "",
        description="Deductions that apply to all rules (e.g., 'Platform fee 0.5%')",
    )
    global_applied_on: str = Field(
        "",
        description="What commission is applied on if stated globally (e.g., 'OD Premium')",
    )
    global_vehicle_type_text: str = Field(
        "",
        description="Comma separated vehicle type that applies to all rules (e.g., 'Staff Bus', 'GCV')",
    )
    global_state_text: str = Field(
        "",
        description="States that apply to all rules (e.g., 'All states except TN')",
    )
    global_seating_capacity_text: str = Field(
        "",
        description="Seating capacity that applies to all rules (e.g., '> 12')",
    )
    global_other_conditions: str = Field(
        "",
        description="Other conditions that apply to all rules",
    )

    has_commission_data: bool = Field(
        False,
        description="True if the source content (e.g. email body) contains commission rates, tables, or rule structures — not just metadata or provider info. Used to determine whether email content should be included in Pass 3 extraction.",
    )

    # Inheritance information
    inherits_from_previous: bool = Field(
        False, description="True if document says rates are same as previous period"
    )
    inheritance_note: str = Field(
        "", description="Note about what is inherited from previous period"
    )

    class Config:
        json_schema_extra = {
            "title": "Pass 1: Metadata Extraction",
            "description": "Extract document metadata, provider, period, and global settings only",
        }


# ============================================================================
# PASS 2: MASTER DATA EXTRACTION
# ============================================================================


class MasterDataEntry(BaseModel):
    """
    Master data/metadata entries from reference sheets.
    Used for RTO mappings, declined states, vehicle classifications, etc.
    """

    entry_type: str = Field(
        ...,
        description="Type: 'rto_mapping', 'declined', 'vehicle_classification', 'state_mapping', 'general_term'",
    )
    applies_to: str = Field(
        "",
        description="What this entry applies to (product name, vehicle type, etc.)",
    )
    key: str = Field(
        ...,
        description="The key/identifier (state code, vehicle type, term name, etc.)",
    )
    values: str = Field(
        "",
        description="Associated values (RTO codes, conditions, definitions, etc.)",
    )
    notes: str = Field("", description="Additional notes or conditions")
    source_sheet: str = Field(
        "", description="Sheet name where this was found (for Excel files)"
    )
    is_global_rule: bool = Field(
        True,
        description="Whether this applies globally (true) or to specific context (false)",
    )
    priority: Optional[int] = Field(
        None,
        description="Priority if multiple master data entries conflict (1=highest)",
    )


class MasterDataExtraction(BaseModel):
    """
    Pass 2: Extract master data only.

    Focus: Extract reference data (RTO mappings, declined lists, etc.).
    Ignore: Products and commission rules (handled in Pass 3).
    """

    master_data: List[MasterDataEntry] = Field(
        [],
        description="Master data/metadata if present (RTO mappings, declined lists, etc.)",
    )

    document_structure_notes: str = Field(
        "",
        description="Notes about how rules are organized in the document (e.g., 'Separate tables per product', 'Single grid with product column')",
    )

    class Config:
        json_schema_extra = {
            "title": "Pass 2: Master Data Extraction",
            "description": "Extract master data only, not products or commission rules",
        }


# Backward compatibility alias
ProductCatalogExtraction = MasterDataExtraction


# ============================================================================
# PASS 3: PRODUCTS AND RULES EXTRACTION
# ============================================================================


class ProductIdentification(BaseModel):
    """Single product identification from document."""

    product_name: str = Field(
        ...,
        description="Product name as stated (e.g., 'Four Wheeler Comprehensive', 'Two Wheeler TP')",
    )
    product_type: str = Field(
        ...,
        description="Product type/category (e.g., 'motor', 'health', '4W', '2W', 'GCV')",
    )
    description: str = Field(
        "",
        description="Brief description or additional context about this product",
    )


class RuleEntry(BaseModel):
    """
    Single commission rule entry for a specific product.
    Simplified text-based extraction - transformer will normalize.
    """

    # Core extraction
    rule_text: str = Field(
        ...,
        description="The full rule as stated, verbatim or near-verbatim",
    )
    commission_value: str = Field(
        ...,
        description="Commission value (e.g., '12%', 'Rs. 500', '0-25K: 10%, 25K-50K: 12%')",
    )

    # Condition fields as free-text
    customer_type_text: str = Field(
        "",
        description=(
            "Business type as stated. Standard: 'new', 'renewal', 'rollover', 'port'. "
            "Do NOT put 'Preferred'/'Non Preferred' here — those are grid column groups, "
            'use conditions dict: {"preferred": "Preferred"} or {"preferred": "Non Preferred"}.'
        ),
    )
    channel_text: str = Field(
        "",
        description="Booking mode as stated (e.g., 'online', 'offline')",
    )
    coverage_type_text: str = Field(
        "",
        description="Policy type as stated (e.g., 'comprehensive', 'TP', 'SAOD', 'third party')",
    )
    state_text: str = Field(
        "",
        description="(e.g., 'BR, CG', 'all states except TN')",
    )
    conditions: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Key-value pairs for any grid dimension conditions that have no dedicated field. "
            "Use for: fuel_type, ncb, cpa, zero_dep, vehicle_sub_class, vehicle_carrier, "
            "number_of_wheels, school_bus_type, gvw, cc, seating_capacity, vehicle_age, "
            "vehicle_make, vehicle_model, od_term, tp_term, deal_parameter, rto, etc. "
            "Keys should be snake_case. Values should be the exact label from the grid header. "
            "Do NOT put here: state (use state_text), booking mode (use channel_text), "
            "policy type (use coverage_type_text), business type (use customer_type_text). "
            'Examples: {"fuel_type": "Non Petrol", "ncb": "Yes", "vehicle_sub_class": "Private Car"}'
        ),
    )
    other_conditions: str = Field(
        "",
        description=(
            "Free-text conditions with no dedicated field AND no clean key-value structure "
            "(e.g., 'tourist permit only', 'corporate-registered vehicles'). "
            "Do NOT use for dimensions that fit as key-value pairs — use conditions dict instead. "
            "Do NOT put footnote definitions here."
        ),
    )

    # Commission modifiers
    applied_on_text: str = Field(
        "",
        description="What commission is applied on (e.g., 'OD Premium', 'Net Premium')",
    )
    gst_applicable: bool = Field(
        False, description="Whether GST is mentioned as applicable"
    )

    # Source traceability
    source_section: str = Field(
        "",
        description="Section/table/heading where this was found",
    )
    quoted_text: str = Field(
        "",
        description="Exact text excerpt supporting this rule",
    )

    # Quality
    confidence_note: str = Field(
        "", description="Any uncertainty or ambiguity notes for this rule"
    )


class ProductWithRules(BaseModel):
    """
    A product with its commission rules.
    """

    # Product identification
    product_name: str = Field(..., description="Product name as stated in document")
    product_type: str = Field(..., description="Product type/category")
    description: str = Field(
        "",
        description="Brief description or additional context about this product",
    )

    # Commission rules for this product
    rules: List[RuleEntry] = Field(
        ...,
        min_length=0,
        description="List of commission rules for this product",
    )

    # Quality indicators
    ambiguities: List[str] = Field(
        [],
        description="List of ambiguities or unclear items for this product",
    )
    requires_review: bool = Field(
        False,
        description="True if significant uncertainty exists for this product",
    )


class RulesExtraction(BaseModel):
    """
    Pass 3: Extract ALL products and their commission rules.

    This schema extracts all products and rules in a single pass.

    Focus: Identify products from commission data and extract their rules.
    Context: Metadata from Pass 1, master data from Pass 2.
    """

    # All products with their rules
    products: List[ProductWithRules] = Field(
        ...,
        min_length=0,
        description="List of all products found in document with their commission rules",
    )

    # Overall quality indicators
    overall_ambiguities: List[str] = Field(
        [],
        description="Document-level ambiguities not specific to any product",
    )
    overall_requires_review: bool = Field(
        False,
        description="True if significant uncertainty exists at document level",
    )

    class Config:
        json_schema_extra = {
            "title": "Pass 3: Products and Rules Extraction",
            "description": "Extract all products and their commission rules from the document",
        }


# ============================================================================
# MERGE LOGIC: Combine passes into RawExtraction
# ============================================================================


def merge_passes(
    metadata: MetadataExtraction,
    master_data_extraction: MasterDataExtraction,
    rules_extraction: RulesExtraction,
) -> dict:
    """
    Merge multi-pass extraction results into RawExtraction format.

    Args:
        metadata: Pass 1 metadata extraction
        master_data_extraction: Pass 2 master data extraction
        rules_extraction: Pass 3 products and rules extraction

    Returns:
        dict: Data in RawExtraction format (ready for model_validate)
    """
    # Import here to avoid circular dependency
    from cins.schema.raw_extraction import RawProviderInfo, RawCommissionEntry

    # Build provider info
    provider = RawProviderInfo(
        name=metadata.provider_name,
    )

    # Merge all rules from all products
    all_commission_entries = []
    all_ambiguities = []
    overall_requires_review = rules_extraction.overall_requires_review

    # Add document-level ambiguities
    if rules_extraction.overall_ambiguities:
        all_ambiguities.extend(rules_extraction.overall_ambiguities)

    for product_with_rules in rules_extraction.products:
        product_name = product_with_rules.product_name
        product_type = product_with_rules.product_type

        # Convert each rule to RawCommissionEntry
        for rule in product_with_rules.rules:
            entry = RawCommissionEntry(
                # Core
                rule_text=rule.rule_text,
                commission_value=rule.commission_value,
                # Product
                product_name=product_name,
                product_type=product_type,
                # Conditions
                customer_type_text=rule.customer_type_text,
                channel_text=rule.channel_text,
                coverage_type_text=rule.coverage_type_text,
                state_text=rule.state_text,
                conditions=rule.conditions,
                other_conditions=rule.other_conditions,
                # Modifiers
                applied_on_text=rule.applied_on_text,
                gst_applicable=rule.gst_applicable,
                # Traceability
                source_section=rule.source_section,
                quoted_text=rule.quoted_text,
                # Quality
                confidence_note=rule.confidence_note,
            )
            all_commission_entries.append(entry)

        # Collect product-specific ambiguities
        if product_with_rules.ambiguities:
            all_ambiguities.extend(
                [f"[{product_name}] {amb}" for amb in product_with_rules.ambiguities]
            )

        # Aggregate review flag
        if product_with_rules.requires_review:
            overall_requires_review = True

    # Build RawExtraction dict
    raw_extraction_dict = {
        # Source
        "source_file": metadata.source_file,
        "source_type": metadata.source_type,
        "extraction_notes": metadata.extraction_notes,
        # Provider
        "provider": provider.model_dump(),
        # Period
        "start_date": metadata.start_date,
        "end_date": metadata.end_date,
        "period_label": metadata.period_label,
        # Commission entries
        "commission_entries": [entry.model_dump() for entry in all_commission_entries],
        # Master data
        "master_data": [md.model_dump() for md in master_data_extraction.master_data],
        # Global settings
        "global_gst_percentage": metadata.global_gst_percentage,
        "global_deductions": metadata.global_deductions,
        "global_applied_on": metadata.global_applied_on,
        "global_vehicle_type_text": metadata.global_vehicle_type_text,
        "global_state_text": metadata.global_state_text,
        "global_seating_capacity_text": metadata.global_seating_capacity_text,
        "global_other_conditions": metadata.global_other_conditions,
        # Inheritance
        "inherits_from_previous": metadata.inherits_from_previous,
        "inheritance_note": metadata.inheritance_note,
        # Quality
        "ambiguities": all_ambiguities,
        "requires_review": overall_requires_review,
    }

    return raw_extraction_dict
