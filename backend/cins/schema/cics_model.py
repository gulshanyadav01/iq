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
from datetime import datetime


class NumericRange(BaseModel):
    """Numeric range with optional min/max boundaries"""
    min: Optional[float] = Field(None, description="Minimum value (inclusive)")
    max: Optional[float] = Field(None, description="Maximum value (inclusive)")
    unit: Optional[str] = Field(None, description="Unit of measurement (e.g., 'INR', 'months', 'years', 'percent')")


class RuleConditions(BaseModel):
    """Multi-dimensional conditions for rule matching (all conditions are ANDed together)"""
    channel: Optional[List[Literal["online", "offline", "partner", "direct", "broker"]]] = Field(None, description="Sales channel(s)")
    customer_type: Optional[List[str]] = Field(None, description="Customer type(s)")
    vehicle_type: Optional[List[str]] = Field(None, description="Vehicle type for motor insurance (flexible to handle various formats)")
    coverage_type: Optional[List[Literal["comprehensive", "third-party", "od-only", "package"]]] = Field(None, description="Coverage type")
    state: Optional[List[str]] = Field(None, description="State(s) where this rule applies")
    state_exclude: Optional[List[str]] = Field(None, description="State(s) to exclude from this rule (use when rule applies to all states except specific ones)")
    custom_conditions: Optional[Dict[str, Any]] = Field(None, description="Escape hatch for provider-specific or complex conditions")

    @field_validator('channel', 'customer_type', 'vehicle_type', 'coverage_type', 'state', 'state_exclude', mode='before')
    @classmethod
    def convert_str_to_list(cls, v):
        """Convert single string values to lists"""
        if isinstance(v, str):
            return [v]
        return v

    class Config:
        extra = "allow"  # Allow additional fields for flexibility


class CommissionTier(BaseModel):
    """Commission tier for tiered commission structures"""
    threshold: NumericRange = Field(..., description="Threshold range for this tier")
    value: float = Field(..., description="Commission value for this tier (percentage or flat)")
    value_type: Literal["percentage", "flat"] = Field(..., description="Type of value (percentage or flat amount)")


class Bonus(BaseModel):
    """Bonus structure"""
    bonus_type: str = Field(..., description="Type of bonus (e.g., 'volume-bonus', 'target-achievement', 'early-payment')")
    value: float = Field(..., description="Bonus value (percentage or flat)")
    value_type: Literal["percentage", "flat"] = Field(..., description="Type of value")
    conditions: Optional[str] = Field(None, description="Conditions for bonus eligibility")


class Deduction(BaseModel):
    """Deduction structure"""
    deduction_type: str = Field(..., description="Type of deduction (e.g., 'processing-fee', 'cancellation-penalty')")
    value: float = Field(..., description="Deduction value (percentage or flat)")
    value_type: Literal["percentage", "flat"] = Field(..., description="Type of value")
    conditions: Optional[str] = Field(None, description="Conditions for deduction application")


class CommissionOutcome(BaseModel):
    """Commission outcome when conditions are met"""
    commission_type: Literal["percentage", "flat", "tiered", "hybrid"] = Field(..., description="Type of commission calculation")
    percentage_value: Optional[float] = Field(None, description="Commission percentage (for percentage type)")
    flat_value: Optional[float] = Field(None, description="Flat commission amount (for flat type)")
    flat_currency: Optional[str] = Field("INR", description="Currency for flat commission")
    tiers: Optional[List[CommissionTier]] = Field(None, description="Tiered commission structure (for tiered/hybrid types)")
    bonuses: Optional[List[Bonus]] = Field(None, description="Additional bonuses applicable")
    deductions: Optional[List[Deduction]] = Field(None, description="Deductions applicable")
    gst_applicable: Optional[bool] = Field(None, description="Whether GST applies to this commission")
    gst_percentage: Optional[float] = Field(None, description="GST percentage if applicable")
    calculation_notes: Optional[str] = Field(None, description="Additional calculation instructions or notes")
    applied_on: Optional[str] = Field(None, description="What the commission is applied on (e.g., 'OD Premium', 'Total Premium', 'GWP')")


class CommissionRule(BaseModel):
    """Commission rule with conditions and outcome"""
    rule_id: str = Field(..., pattern=r"^[A-Za-z0-9_-]+$", description="Unique identifier for this rule within the product")
    priority: int = Field(..., description="Rule evaluation priority (1 = highest priority)")
    conditions: Optional[RuleConditions] = Field(None, description="Conditions that must be met for this rule to apply")
    outcome: CommissionOutcome = Field(..., description="Commission outcome when conditions are met")
    valid_from: Optional[str] = Field(None, description="Optional start date for rule validity (YYYY-MM-DD)")
    valid_to: Optional[str] = Field(None, description="Optional end date for rule validity (YYYY-MM-DD)")
    rule_description: Optional[str] = Field(None, description="Human-readable description of this rule")


class Product(BaseModel):
    """Insurance product with commission rules"""
    product_code: str = Field(..., pattern=r"^[A-Za-z0-9_-]+$", description="Unique code for this product")
    product_name: str = Field(..., description="Human-readable product name")
    product_category: Optional[Literal["motor", "health", "life", "travel", "home", "other"]] = Field(None, description="High-level product category")
    sub_category: Optional[str] = Field(None, description="Sub-category or product type")
    commission_rules: List[CommissionRule] = Field(..., min_length=1, description="Ordered list of commission rules (evaluated by priority)")


class SourceLocation(BaseModel):
    """Location in source document"""
    page: Optional[int] = Field(None, description="Page number in PDF")
    section: Optional[str] = Field(None, description="Section or heading in document")
    table: Optional[str] = Field(None, description="Table name or identifier")
    row: Optional[int] = Field(None, description="Row number in table/spreadsheet")
    column: Optional[str] = Field(None, description="Column identifier in table/spreadsheet")


class SourceTrace(BaseModel):
    """Source traceability record"""
    trace_id: str = Field(..., pattern=r"^[A-Za-z0-9_-]+$", description="Unique identifier for this trace")
    entity_type: Literal["product", "rule", "condition", "outcome", "provider", "period"] = Field(..., description="Type of entity being traced")
    entity_id: str = Field(..., description="ID of the entity being traced (e.g., product_code, rule_id)")
    source_location: Optional[SourceLocation] = Field(None, description="Location in source document")
    quoted_text: Optional[str] = Field(None, description="Exact text excerpt from source document")
    confidence: Optional[float] = Field(None, description="Confidence score for this specific extraction (0-1)")
    extraction_notes: Optional[str] = Field(None, description="Notes about ambiguities or interpretation decisions")


class DocumentMetadata(BaseModel):
    """Document metadata"""
    document_id: str = Field(..., pattern=r"^[A-Za-z0-9_-]+$", description="Unique identifier for this normalized document")
    source_type: Optional[Literal["email", "pdf", "excel", "api", "manual"]] = Field(None, description="Type of source document")
    source_file: Optional[str] = Field(None, description="Original filename or identifier")
    extracted_at: str = Field(..., description="ISO 8601 timestamp of extraction")
    extractor: str = Field(..., description="Identifier of extraction system (e.g., 'gpt-4', 'gemini-2.0')")
    notes: Optional[str] = Field(None, description="Additional context or extraction notes")
    additional_info: Optional[Dict[str, Any]] = Field(None, description="Additional metadata like master data sheets, merged sheets info, etc.")


class Provider(BaseModel):
    """Insurance provider information"""
    provider_id: str = Field(..., pattern=r"^[A-Za-z0-9_-]+$", description="Unique identifier for the insurance provider")
    provider_name: str = Field(..., description="Full name of the insurance provider")
    contact_email: Optional[str] = Field(None, description="Primary contact email for commission queries")
    contact_phone: Optional[str] = Field(None, description="Primary contact phone number")


class CommissionPeriod(BaseModel):
    """Commission period information"""
    effective_from: str = Field(..., description="Start date of commission period (YYYY-MM-DD)")
    effective_to: Optional[str] = Field(None, description="End date of commission period (YYYY-MM-DD), null if ongoing")
    period_label: Optional[str] = Field(None, description="Human-readable period label (e.g., 'Q1 2024', 'Jan 2024')")


class ChangeReference(BaseModel):
    """Reference to previous period for tracking changes"""
    previous_document_id: Optional[str] = Field(None, description="Document ID of previous period's commission structure")
    previous_period: Optional[str] = Field(None, description="Effective date of previous period (YYYY-MM-DD)")
    is_inherited: Optional[bool] = Field(None, description="True if this document inherits from previous period")
    inheritance_notes: Optional[str] = Field(None, description="Details about inheritance or changes from previous period")


class Validation(BaseModel):
    """Validation and confidence tracking"""
    overall_confidence: Optional[float] = Field(None, description="Overall confidence score for the entire extraction (0-1)")
    requires_manual_review: Optional[bool] = Field(None, description="Flag indicating if manual review is recommended")
    validation_notes: Optional[List[str]] = Field(None, description="List of validation warnings or issues")


class CICSDocument(BaseModel):
    """
    Commission Intelligence Canonical Schema (CICS) v1.0

    Root document model for standardized insurance commission data.
    """
    schema_version: Literal["1.0"] = Field("1.0", description="Version of the CICS schema being used")
    document_metadata: DocumentMetadata = Field(..., description="Document metadata and extraction info")
    provider: Provider = Field(..., description="Insurance provider information")
    commission_period: CommissionPeriod = Field(..., description="Commission period information")
    products: List[Product] = Field(..., description="List of insurance products with their commission rules")
    change_reference: Optional[ChangeReference] = Field(None, description="Reference to previous period for tracking changes")
    source_traces: Optional[List[SourceTrace]] = Field(None, description="Array of source traceability records")
    validation: Optional[Validation] = Field(None, description="Validation and confidence tracking")

    class Config:
        json_schema_extra = {
            "title": "Commission Intelligence Canonical Schema (CICS)",
            "description": "Standardized schema for normalizing heterogeneous insurance commission data"
        }
