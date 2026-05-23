"""
RawExtraction Schema - Simplified extraction target for LLMs

This schema is designed to be simple and flat for AI extraction.
AI extracts only what it MUST (cannot be derived). Everything else
is computed deterministically by the transformer.

Key simplifications:
- No nested objects (NumericRange, SourceLocation) - use text strings
- No enums - use free-text that transformer normalizes
- No generated IDs - transformer creates them
- No priority - computed from rule specificity
- Flat source traceability - just section and quoted text
"""

from typing import Any, Dict, List
from pydantic import BaseModel, Field


class RawProviderInfo(BaseModel):
    """Flat provider information - no nested structures"""

    name: str = Field(..., description="Provider name as stated in document")


class RawCommissionEntry(BaseModel):
    """
    Single commission rule as text-based entry.
    AI extracts conditions and values as free-form text.
    No enums, no nested objects, no generated IDs.
    """

    # Core extraction - what the rule says
    rule_text: str = Field(
        ...,
        description="The full rule as stated in document, verbatim or near-verbatim",
    )
    commission_value: str = Field(
        ...,
        description="The commission value as stated (e.g., '12%', 'Rs. 500', '10-15% tiered', '0-25K: 10%, 25K-50K: 12%')",
    )

    # Product identification
    product_name: str = Field(
        '',
        description=(
            "Canonical product name. Motor products must use exactly one of: "
            "'Private Car', 'Two Wheeler', or 'Commercial Vehicle'. "
            "Non-motor products (health, life, travel, fire, marine): name as written in document."
        ),
    )
    product_type: str = Field(
        '',
        description="Product type/category (e.g., 'motor', 'health', 'life', 'GCV', 'PCV'). For motor, subtype if known.",
    )

    # Condition fields as free-text (no enums, no nested objects)
    customer_type_text: str = Field(
        '',
        description=(
            "Business type as stated. Standard values: 'new', 'renewal', 'rollover', 'port'. "
            "Do NOT put 'Preferred'/'Non Preferred' here — those are grid column groups; "
            "use conditions dict instead: {\"preferred\": \"Preferred\"}."
        ),
    )
    channel_text: str = Field(
        '',
        description="Booking mode as stated (e.g., 'online', 'offline')",
    )
    coverage_type_text: str = Field(
        '',
        description="Policy type as stated (e.g., 'comprehensive', 'TP', 'SAOD', 'third party')",
    )
    state_text: str = Field(
        '',
        description="States/regions as stated (e.g., 'BR, CG', 'all states except TN', 'Maharashtra')",
    )
    # Structured key-value conditions for grid dimensions without a dedicated field
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
            "Examples: {\"fuel_type\": \"Non Petrol\", \"ncb\": \"Yes\", \"vehicle_sub_class\": \"Private Car\"}"
        ),
    )
    # Custom/other conditions as free-text catch-all
    other_conditions: str = Field(
        '',
        description=(
            "Free-text conditions with no dedicated field AND no clean key-value structure "
            "(e.g., 'tourist permit only', 'corporate-registered vehicles'). "
            "Do NOT use for dimensions that fit as key-value pairs — use conditions dict instead. "
            "Do NOT put footnote definitions here."
        ),
    )
    qualifying_clauses: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Structured machine-ingestable representation of footnote / notes-table "
            "clauses that modify this rule's outcome (applied_on override, conditional "
            "deduction, grid alias, mandatory requirement, etc.). Each item: "
            "{clause_type, text, when?, effect?}. See pass3_rules_prompt.md for shape."
        ),
    )

    # Commission modifiers
    applied_on_text: str = Field(
        '',
        description="What commission is applied on (e.g., 'OD Premium', 'Total Premium', 'Net Premium', 'GWP')",
    )
    gst_applicable: bool = Field(
        False, description="Whether GST is mentioned as applicable"
    )

    # Source traceability - simple flat strings
    source_section: str = Field(
        '',
        description="Section/table/heading where this was found (e.g., 'Commission Rates Table', 'Page 2 - Motor Section')",
    )
    quoted_text: str = Field(
        '',
        description="Exact text excerpt from source document supporting this rule",
    )

    # Confidence for this specific entry
    confidence_note: str = Field(
        '', description="Any uncertainty or ambiguity notes for this specific rule"
    )


class RawMasterDataEntry(BaseModel):
    """
    Master data/metadata entries extracted from reference sheets.
    Used for RTO mappings, declined states, vehicle classifications, etc.
    """

    entry_type: str = Field(
        ...,
        description="Type of master data: 'rto_mapping', 'declined', 'vehicle_classification', 'state_mapping', 'general_term'",
    )
    applies_to: str = Field(
        '',
        description="What this entry applies to (product name, vehicle type, etc.)",
    )
    key: str = Field(
        ...,
        description="The key/identifier (state code, vehicle type, term name, etc.)",
    )
    values: str = Field(
        '',
        description="Associated values (RTO codes as comma-separated, conditions, definitions, etc.)",
    )
    notes: str = Field('', description="Additional notes or conditions")
    source_sheet: str = Field(
        '', description="Sheet name where this was found (for Excel files)"
    )


class RawExtraction(BaseModel):
    """
    Root extraction model - flat structure for AI extraction.

    No IDs, no priorities, no complex nesting.
    AI focuses on WHAT is in the document, not on formatting/structure.
    """

    # Source information
    source_file: str = Field('', description="Original filename")
    source_type: str = Field(
        '', description="Type of source: 'email', 'pdf', 'excel'"
    )
    extraction_notes: str = Field(
        '', description="General notes about the extraction or document structure"
    )

    # Provider - simple flat structure
    provider: RawProviderInfo = Field(..., description="Insurance provider information")

    # Commission period - simple strings
    start_date: str = Field(
        ...,
        description="Start date in any recognizable format (e.g., 'January 1, 2024', '2024-01-01', '1st Jan 2024')",
    )
    end_date: str = Field('', description="End date if specified")
    period_label: str = Field(
        '',
        description="Human-readable period label (e.g., 'Q1 2024', 'January 2024', 'FY 2024-25')",
    )

    # Commission entries - flat list of rules
    commission_entries: List[RawCommissionEntry] = Field(
        ...,
        description="List of commission rules extracted from the document",
    )

    # Master data entries, for reference sheets
    master_data: List[RawMasterDataEntry] = Field(
        [],
        description="Master data/metadata if present (RTO mappings, state codes, vehicle classifications)",
    )

    # Global settings that apply to all rules
    global_gst_percentage: str = Field(
        '', description="Default GST if mentioned globally (e.g., '18%')"
    )
    global_deductions: str = Field(
        '',
        description="Deductions that apply to all rules (e.g., 'Platform fee 0.5% on all online transactions')",
    )
    global_applied_on: str = Field(
        '',
        description="What commission is generally applied on if stated globally (e.g., 'Net Premium', 'OD Premium', 'Total Premium'). Extract exactly as stated in the document.",
    )
    global_vehicle_type_text: str = Field(
        '',
        description="Comma-separated vehicle type(s) that apply to all rules (e.g., 'Staff Bus', 'GCV')",
    )
    global_state_text: str = Field(
        '',
        description="States that apply to all rules (e.g., 'All states except TN')",
    )
    global_seating_capacity_text: str = Field(
        '',
        description="Seating capacity that applies to all rules (e.g., '> 12')",
    )
    global_other_conditions: str = Field(
        '',
        description="Other conditions that apply to all rules (e.g., 'Registered in corporate name')",
    )

    # Inheritance information
    inherits_from_previous: bool = Field(
        False, description="True if document says rates are same as previous period"
    )
    inheritance_note: str = Field(
        '', description="Note about what is inherited or changed from previous period"
    )

    # Overall extraction quality
    ambiguities: List[str] = Field(
        [],
        description="List of ambiguities, contradictions, or unclear items found in the document",
    )
    requires_review: bool = Field(
        False,
        description="True if significant uncertainty exists and manual review is recommended",
    )

    @classmethod
    def from_passes(cls, metadata, catalog, rules_list):
        """
        Create RawExtraction from multi-pass extraction results.

        Args:
            metadata: MetadataExtraction from Pass 1
            catalog: ProductCatalogExtraction from Pass 2
            rules_list: List[RulesExtraction] from Pass 3

        Returns:
            RawExtraction: Validated RawExtraction instance
        """
        from cins.schema.extraction_passes import merge_passes

        raw_dict = merge_passes(metadata, catalog, rules_list)
        return cls.model_validate(raw_dict)

    class Config:
        json_schema_extra = {
            "title": "Raw Commission Extraction",
            "description": "Simplified extraction schema for AI. Captures observable data without transformation.",
        }
