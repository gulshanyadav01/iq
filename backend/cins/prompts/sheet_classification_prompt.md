# Sheet Classification Task

You are analyzing an Excel sheet from an insurance commission document to determine its purpose and content type.

## Sheet Information
- **Sheet Name**: `{sheet_name}`
- **Dimensions**: {rows} rows × {cols} columns
- **Detected Headers**: Row(s) {header_rows}
- **Merged Cells**: {merged_count} ranges

## Sheet Content Preview (First 20 Rows)
```csv
{csv_preview}
```

## Classification Categories

### 1. COMMISSION_DATA
Sheets containing actual commission rates, percentages, or amounts.

**Characteristics**:
- **Contains numeric commission values** (e.g., 0.45, 15%, ₹500, "Discount up to 99%")
- May be small (2-10 rows) or large (50+ rows)
- Defines how much commission is paid for specific scenarios
- May include conditions: segments, vehicle types, states, channels, premium ranges
- May have effective dates or validity periods
- Purpose: Defines commission calculation logic

**Examples**:
- "Commission Rates", "Rate Card", "Premium Slabs", "Q1 2024 Commissions"
- "School Bus Discount", "SATP", "Two Wheeler Rates"
- Any sheet with a column showing rates, percentages, or amounts

### 2. MASTER_DATA
Supporting information that does NOT contain commission rates/amounts.

**Characteristics**:
- **Does NOT contain commission rates, percentages, or amounts**
- Code mappings: RTO code → State name, Vehicle type → Category
- Document metadata: Provider name, effective period, contact info
- Definitions: What terms mean (NCB = No Claim Bonus)
- Aggregated totals or summaries (not individual rates)
- Simple lookup tables without rate/amount columns
- Purpose: Provides context or definitions, not commission logic

**Examples**:
- "Master Data", "RTO Codes", "State Mapping" (just codes, no rates)
- "Document Info", "Metadata", "Contact Details"
- "Definitions", "Glossary", "Legend"
- "Summary", "Total Commission" (aggregated, not individual rates)

**Important distinction**: A table with RTO codes AND numeric percentage/amount columns is COMMISSION_DATA, not MASTER_DATA. The presence of RTO/location columns does not make it master data — what matters is whether it contains rate values.

## Your Task

Analyze the sheet information and content preview above, then classify this sheet into one of the two categories.

## Decision Guidelines

- **Primary Decision**: Does this sheet contain commission calculation rules?
  - **YES** → COMMISSION_DATA
  - **NO** → MASTER_DATA

- **Key Indicators for COMMISSION_DATA**:
  - **Most Important**: Contains actual commission rates, percentages, or amounts (e.g., "0.45", "15%", "₹500")
  - Has premium amount ranges or slabs
  - Has segments, vehicle types, or channels with associated rates
  - Contains conditions that determine commission eligibility
  - May have effective dates or validity periods for rates
  - Can be small (even 2-3 data rows) if it contains rate information
  - **GVW columns with numeric values**: Column headers containing "GVW" (e.g. "SCV <2000 GVW", ">2000 GVW to <2450 GVW") are vehicle weight categories used as commission dimensions — numeric values in those columns (e.g. 65.0, 30.0) are commission rates, not vehicle weights
  - **Decimal rate values (0.xx format)**: Values like 0.35, 0.30 in a state/RTO-keyed grid are commission rates in decimal form (35%, 30%), even if the sheet includes a note mentioning "LR" (Loss Ratio) — that note explains how rates were derived, not what the sheet contains
  - **"Decline" or "No Biz" entries**: These mean "no commission offered in this geography/segment" — their presence does NOT make a sheet master data. A grid mixing numeric rates with Decline/No Biz values is COMMISSION_DATA

- **Key Indicators for MASTER_DATA**:
  - **Does NOT contain commission rates or amounts**
  - Simple code-to-name mappings (RTO codes → state names) — with NO additional numeric rate/amount columns
  - Document metadata (provider name, contact info, version)
  - Term definitions and glossaries (what is "NCB"?, "IDV"?)
  - Aggregated summaries or totals (no individual rates)
  - Reference tables without rate/amount columns

- **Critical Rule**: If the sheet contains any commission rates, percentages, or amounts (no matter how small), it is COMMISSION_DATA.

- **When sheet seems empty or unclear**:
  - Check sheet name for classification hints
  - Look for numeric values that could be rates or amounts
  - When in doubt, classify as COMMISSION_DATA (default assumption)

- **When sheet has mixed content**:
  - Classify by primary purpose (what takes up most rows)
  - Note the mix in reasoning

## Important Notes

- Focus on **what the sheet contains**, not what you think it should contain
- Use the sheet name as a strong signal, but verify with content
- If content doesn't match name, trust content over name (but lower confidence)
- Sheets with aggregated/summary data should be classified as MASTER_DATA (they'll be skipped during extraction)
