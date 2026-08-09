"""
CICS Pydantic Models

Pydantic models for Commission Intelligence Canonical Schema (CICS).
Used for:
1. Type validation
2. Schema generation for LLM structured output
3. Documentation
"""

from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field, field_validator


class NumericRange(BaseModel):
    """Numeric range with optional min/max boundaries"""

    min: Optional[float] = Field(None, description="Minimum value (inclusive)")
    max: Optional[float] = Field(None, description="Maximum value (inclusive)")
    unit: Optional[str] = Field(
        None,
        description="Unit of measurement (e.g., 'INR', 'months', 'years', 'percent')",
    )


class RuleConditions(BaseModel):
    """Domain-specific conditions for insurance commission rule matching (all conditions ANDed)

    FORWARD-ONLY MIGRATION (vehicle_make_exclude / vehicle_model_exclude):
    this model has no ``model_config``, so Pydantic v2 defaults to
    ``extra='ignore'`` — OLD code reading a NEWLY re-extracted ``cics_doc`` will
    SILENTLY DROP the exclude keys, turning bucket (c) ``exclude(TATA,...)`` into
    an unconditional all-makes rule that double-matches bucket (a). Rolling the
    code back is therefore NOT safe while new blobs are live: re-extract the
    affected Chola docs (or restore the prior cics_doc blob) after any rollback.
    Do not roll back code-only. See the deploy runbook.
    """

    vehicle_sub_class: Optional[List[str]] = Field(
        None, description="Vehicle sub-class(es) e.g. private-car, taxi, scooter, bike"
    )
    policy_type: Optional[List[str]] = Field(
        None, description="Policy type(s): third-party, comprehensive, saod"
    )
    state: Optional[List[str]] = Field(
        None,
        description="Indian state(s) in lowercase-hyphenated format e.g. andhra-pradesh",
    )
    rto: Optional[List[str]] = Field(None, description="RTO code(s) e.g. UP-16, MH-01")
    number_of_wheels: Optional[List[str]] = Field(
        None, description="Number of wheels: 3w, 4w-and-above"
    )
    gvw_start_range: Optional[int] = Field(
        None, description="Start of Gross Vehicle Weight range"
    )
    gvw_end_range: Optional[int] = Field(
        None, description="End of Gross Vehicle Weight range"
    )
    cc_start_range: Optional[int] = Field(None, description="Start of engine CC range")
    cc_end_range: Optional[int] = Field(None, description="End of engine CC range")
    seating_capacity_start_range: Optional[int] = Field(
        None, description="Start of seating capacity range"
    )
    seating_capacity_end_range: Optional[int] = Field(
        None, description="End of seating capacity range"
    )
    policies_count_start_range: Optional[int] = Field(
        None,
        description="Start of policies-count range (number of policies sold tier, e.g. 'UPTO 30 NOP', '30-100 NOP', '100-500 NOP')",
    )
    policies_count_end_range: Optional[int] = Field(
        None, description="End of policies-count range"
    )
    school_bus_type: Optional[List[str]] = Field(
        None,
        description="School bus type(s): on-individual-name, on-school-name, on-company-name, on-contract-transporter",
    )
    booking_mode: Optional[List[str]] = Field(
        None, description="Booking mode(s): online, offline"
    )
    zero_dep: Optional[str] = Field(None, description="Zero depreciation: yes, no")
    ncb: Optional[bool] = Field(
        None,
        description="No Claim Bonus tier: true = the NCB > 25% grid, false = the base (NCB <= 25%) grid, null = no NCB dimension",
    )
    cpa: Optional[str] = Field(None, description="CPA type: with-cpa, without-cpa")
    fuel_type: Optional[List[str]] = Field(
        None, description="Fuel type(s): petrol, diesel, cng, lpg, electric"
    )
    vehicle_age: Optional[int] = Field(None, description="Vehicle age in years (0-35)")
    vehicle_make: Optional[List[str]] = Field(
        None, description="Vehicle make(s), free-text"
    )
    vehicle_model: Optional[List[str]] = Field(
        None, description="Vehicle model(s), free-text"
    )
    vehicle_make_exclude: Optional[List[str]] = Field(
        None,
        description="Vehicle make(s) to EXCLUDE — rule matches only when policy make is NOT in this list (e.g. 'All Other Make/Models' bucket)",
    )
    vehicle_model_exclude: Optional[List[str]] = Field(
        None,
        description="Vehicle model(s) to EXCLUDE — rule matches only when policy model is NOT in this list",
    )
    vehicle_carrier: Optional[List[str]] = Field(
        None, description="Vehicle carrier type(s): public, private, commercial"
    )
    business_type: Optional[List[str]] = Field(
        None, description="Business type(s): new, renewal, rollover, port"
    )
    od_term: Optional[int] = Field(None, description="OD term in years (0-10)")
    tp_term: Optional[int] = Field(None, description="TP term in years (0-10)")
    deal_parameter: Optional[List[str]] = Field(
        None,
        description="Deal parameter(s): od, net, regular-tp, tp-with-vehicle-age, flat-amount, ape",
    )

    @field_validator(
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
        "vehicle_make_exclude",
        "vehicle_model_exclude",
        "vehicle_carrier",
        "business_type",
        "deal_parameter",
        mode="before",
    )
    @classmethod
    def convert_str_to_list(cls, v):
        """Convert single string values to lists"""
        if isinstance(v, str):
            return [v]
        return v


class CommissionTier(BaseModel):
    """Commission tier for tiered commission structures"""

    threshold: NumericRange = Field(..., description="Threshold range for this tier")
    value: float = Field(
        ..., description="Commission value for this tier (percentage or flat)"
    )
    value_type: Literal["percentage", "flat"] = Field(
        ..., description="Type of value (percentage or flat amount)"
    )


class Bonus(BaseModel):
    """Bonus structure"""

    bonus_type: str = Field(
        ...,
        description="Type of bonus (e.g., 'volume-bonus', 'target-achievement', 'early-payment')",
    )
    value: float = Field(..., description="Bonus value (percentage or flat)")
    value_type: Literal["percentage", "flat"] = Field(..., description="Type of value")
    conditions: Optional[str] = Field(
        None, description="Conditions for bonus eligibility"
    )


class Deduction(BaseModel):
    """Deduction structure"""

    deduction_type: str = Field(
        ...,
        description="Type of deduction (e.g., 'processing-fee', 'cancellation-penalty')",
    )
    value: float = Field(..., description="Deduction value (percentage or flat)")
    value_type: Literal["percentage", "flat"] = Field(..., description="Type of value")
    conditions: Optional[str] = Field(
        None, description="Conditions for deduction application"
    )


class QualifyingClause(BaseModel):
    """
    Structured representation of a footnote / notes-table clause that
    modifies how a commission rule is applied.

    Free-text other_conditions captures the raw clause verbatim for human
    review. qualifying_clauses adds a machine-ingestable shape so deal
    management / booking pipelines can apply the modifier programmatically.

    `when` and `effect` are intentionally loose dicts — clause shapes vary
    by carrier and clause_type. Known clause_type values and their
    expected payload shape are documented in
    backend/cins/prompts/pass3_rules_prompt.md.
    """

    clause_type: str = Field(
        ...,
        description="Clause shape: 'applied_on_override', 'conditional_deduction', 'grid_alias', 'mandatory_requirement', 'minimum_payout', 'other'",
    )
    text: str = Field(
        ..., description="Verbatim (or near-verbatim) source clause text, ≤ 200 chars"
    )
    when: Optional[Dict[str, Any]] = Field(
        None,
        description="Conditions under which the clause applies (e.g., {'product': 'Private Car', 'cpa': 'without-cpa', 'school_bus_type': 'on-individual-name'})",
    )
    effect: Optional[Dict[str, Any]] = Field(
        None,
        description="What the clause changes (e.g., {'deduction': {'value': 1.5, 'value_type': 'percentage', 'applied_on': 'OD Pay-out'}})",
    )


class CommissionOutcome(BaseModel):
    """Commission outcome when conditions are met"""

    commission_type: Literal["percentage", "flat", "tiered", "hybrid"] = Field(
        ..., description="Type of commission calculation"
    )
    percentage_value: Optional[float] = Field(
        None, description="Commission percentage (for percentage type)"
    )
    flat_value: Optional[float] = Field(
        None, description="Flat commission amount (for flat type)"
    )
    flat_currency: Optional[str] = Field(
        "INR", description="Currency for flat commission"
    )
    tiers: Optional[List[CommissionTier]] = Field(
        None, description="Tiered commission structure (for tiered/hybrid types)"
    )
    bonuses: Optional[List[Bonus]] = Field(
        None, description="Additional bonuses applicable"
    )
    deductions: Optional[List[Deduction]] = Field(
        None, description="Deductions applicable"
    )
    gst_applicable: Optional[bool] = Field(
        None, description="Whether GST applies to this commission"
    )
    gst_percentage: Optional[float] = Field(
        None, description="GST percentage if applicable"
    )
    calculation_notes: Optional[str] = Field(
        None, description="Additional calculation instructions or notes"
    )
    applied_on: Optional[str] = Field(
        None,
        description="What the commission is applied on (e.g., 'OD Premium', 'Total Premium', 'GWP')",
    )
    other_conditions: Optional[str] = Field(
        None,
        description="Free-text qualifying conditions or footnotes from the source — verbatim, for human review",
    )
    qualifying_clauses: Optional[List[QualifyingClause]] = Field(
        None,
        description="Structured machine-ingestable representation of footnote/notes clauses that modify this outcome",
    )


class CommissionRule(BaseModel):
    """Commission rule with conditions and outcome"""

    rule_id: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Unique identifier for this rule within the product",
    )
    priority: int = Field(
        ..., description="Rule evaluation priority (1 = highest priority)"
    )
    conditions: Optional[RuleConditions] = Field(
        None, description="Conditions that must be met for this rule to apply"
    )
    outcome: CommissionOutcome = Field(
        ..., description="Commission outcome when conditions are met"
    )
    valid_from: Optional[str] = Field(
        None, description="Optional start date for rule validity (YYYY-MM-DD)"
    )
    valid_to: Optional[str] = Field(
        None, description="Optional end date for rule validity (YYYY-MM-DD)"
    )
    rule_description: Optional[str] = Field(
        None, description="Human-readable description of this rule"
    )


class Product(BaseModel):
    """Insurance product with commission rules"""

    product_code: str = Field(
        ..., pattern=r"^[A-Za-z0-9_-]+$", description="Unique code for this product"
    )
    product_name: str = Field(..., description="Human-readable product name")
    product_category: Optional[
        Literal["motor", "health", "life", "travel", "home", "other"]
    ] = Field(None, description="High-level product category")
    sub_category: Optional[str] = Field(
        None, description="Sub-category or product type"
    )
    commission_rules: List[CommissionRule] = Field(
        ...,
        min_length=1,
        description="Ordered list of commission rules (evaluated by priority)",
    )
    # Deal-level metadata fields
    deal_type: Optional[str] = Field(
        None,
        description="Deal type: market-deal, market-bonus-deal, special-deal, special-bonus-deal, declined-deal, basic-deal",
    )
    deal_business_type: Optional[str] = Field(
        None, description="Deal business type: net, ape, tp, od"
    )
    portfolio_business: Optional[str] = Field(
        None,
        description="Portfolio business: market-deal, special-deal, declined-deal, partner-specific-special-deal, registration-deal",
    )
    approval_type: Optional[str] = Field(
        None, description="Approval type: deal, booking, manual-override"
    )
    file_type: Optional[str] = Field(
        None,
        description="File type: irda-statement, rewards-statement, enhancement-statement, orc-statement",
    )
    zone: Optional[str] = Field(None, description="Zone: north, south, east, west")
    product_mode: Optional[str] = Field(
        None, description="Product mode: online, offline"
    )
    bu_parameters: Optional[str] = Field(None, description="BU parameters: motor")
    decline_deal: Optional[str] = Field(None, description="Decline deal: decline")
    product_subproduct: Optional[str] = Field(
        None,
        description="Product-subproduct: car, two-wheeler, commercial-gcv, commercial-pcv, commercial-buses, commercial-miscd",
    )
    vehicle_type: Optional[str] = Field(
        None, description="Vehicle type: car-taxi, two-wheeler, gcv, pcv, buses, misc-d"
    )


class SourceLocation(BaseModel):
    """Location in source document"""

    page: Optional[int] = Field(None, description="Page number in PDF")
    section: Optional[str] = Field(None, description="Section or heading in document")
    table: Optional[str] = Field(None, description="Table name or identifier")
    row: Optional[int] = Field(None, description="Row number in table/spreadsheet")
    column: Optional[str] = Field(
        None, description="Column identifier in table/spreadsheet"
    )


class SourceTrace(BaseModel):
    """Source traceability record"""

    trace_id: str = Field(
        ..., pattern=r"^[A-Za-z0-9_-]+$", description="Unique identifier for this trace"
    )
    entity_type: Literal[
        "product", "rule", "condition", "outcome", "provider", "period"
    ] = Field(..., description="Type of entity being traced")
    entity_id: str = Field(
        ..., description="ID of the entity being traced (e.g., product_code, rule_id)"
    )
    source_location: Optional[SourceLocation] = Field(
        None, description="Location in source document"
    )
    quoted_text: Optional[str] = Field(
        None, description="Exact text excerpt from source document"
    )
    confidence: Optional[float] = Field(
        None, description="Confidence score for this specific extraction (0-1)"
    )
    extraction_notes: Optional[str] = Field(
        None, description="Notes about ambiguities or interpretation decisions"
    )


class DocumentMetadata(BaseModel):
    """Document metadata"""

    document_id: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Unique identifier for this normalized document",
    )
    source_type: Optional[Literal["email", "pdf", "excel", "api", "manual"]] = Field(
        None, description="Type of source document"
    )
    source_file: Optional[str] = Field(
        None, description="Original filename or identifier"
    )
    extracted_at: str = Field(..., description="ISO 8601 timestamp of extraction")
    extractor: str = Field(
        ..., description="Identifier of extraction system (e.g., 'gpt-4', 'gemini-2.0')"
    )
    notes: Optional[str] = Field(
        None, description="Additional context or extraction notes"
    )
    additional_info: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional metadata like master data sheets, merged sheets info, etc.",
    )


class Provider(BaseModel):
    """Insurance provider information"""

    provider_id: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Unique identifier for the insurance provider",
    )
    provider_name: str = Field(..., description="Full name of the insurance provider")
    contact_email: Optional[str] = Field(
        None, description="Primary contact email for commission queries"
    )
    contact_phone: Optional[str] = Field(
        None, description="Primary contact phone number"
    )


class CommissionPeriod(BaseModel):
    """Commission period information"""

    effective_from: str = Field(
        ..., description="Start date of commission period (YYYY-MM-DD)"
    )
    effective_to: Optional[str] = Field(
        None, description="End date of commission period (YYYY-MM-DD), null if ongoing"
    )
    period_label: Optional[str] = Field(
        None, description="Human-readable period label (e.g., 'Q1 2024', 'Jan 2024')"
    )


class ChangeReference(BaseModel):
    """Reference to previous period for tracking changes"""

    previous_document_id: Optional[str] = Field(
        None, description="Document ID of previous period's commission structure"
    )
    previous_period: Optional[str] = Field(
        None, description="Effective date of previous period (YYYY-MM-DD)"
    )
    is_inherited: Optional[bool] = Field(
        None, description="True if this document inherits from previous period"
    )
    inheritance_notes: Optional[str] = Field(
        None, description="Details about inheritance or changes from previous period"
    )


class Validation(BaseModel):
    """Validation and confidence tracking"""

    overall_confidence: Optional[float] = Field(
        None, description="Overall confidence score for the entire extraction (0-1)"
    )
    requires_manual_review: Optional[bool] = Field(
        None, description="Flag indicating if manual review is recommended"
    )
    validation_notes: Optional[List[str]] = Field(
        None, description="List of validation warnings or issues"
    )


class CICSDocument(BaseModel):
    """
    Commission Intelligence Canonical Schema (CICS) v1.0

    Root document model for standardized insurance commission data.
    """

    schema_version: Literal["1.0"] = Field(
        "1.0", description="Version of the CICS schema being used"
    )
    document_metadata: DocumentMetadata = Field(
        ..., description="Document metadata and extraction info"
    )
    provider: Provider = Field(..., description="Insurance provider information")
    commission_period: CommissionPeriod = Field(
        ..., description="Commission period information"
    )
    products: List[Product] = Field(
        ..., description="List of insurance products with their commission rules"
    )
    change_reference: Optional[ChangeReference] = Field(
        None, description="Reference to previous period for tracking changes"
    )
    source_traces: Optional[List[SourceTrace]] = Field(
        None, description="Array of source traceability records"
    )
    validation: Optional[Validation] = Field(
        None, description="Validation and confidence tracking"
    )

    class Config:
        json_schema_extra = {
            "title": "Commission Intelligence Canonical Schema (CICS)",
            "description": "Standardized schema for normalizing heterogeneous insurance commission data",
        }
