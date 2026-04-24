## Objective

Clean and normalize master data sections from commission documents.

Return **strict JSON only**. No explanation.

---

## What is Master Data?

Reference information used across commission rules:

* **RTO Mappings**: State codes → RTO office codes
* **Declined/Excluded Lists**: Products/states/vehicles with 0% commission or restrictions
* **Vehicle Classifications**: Vehicle type definitions and categories
* **State/Zone Mappings**: State groupings (e.g., NCR states, doable states)
* **General Terms**: Definitions, abbreviations, glossary

Master data is typically in:
* Separate reference sheets
* Appendix sections
* "Notes" or "Master Data" sections
* Tables at beginning/end of document

---

## Cleaning Tasks

### 1. Identify Master Data Sections

Look for:
* Section headers: "Master Data", "Reference", "RTO Mapping", "Declined List", "Definitions"
* Tables with mapping structure (key → value)
* Lists of excluded/declined items
* Glossary or definitions

### 2. Normalize Table Format

Convert to consistent structure:
* Extract headers
* Remove merged cells (expand to full content)
* Remove formatting artifacts
* Convert HTML tables to markdown
* Remove empty rows/columns

### 3. Remove Duplicates

* If same master data appears multiple times, keep one instance
* Prefer the most complete/detailed version
* Note if conflicting versions exist

### 4. Separate from Commission Rules

Commission rules contain:
* Percentage rates (12%, 10%, etc.)
* Premium slabs (0-25K, 25-50K)
* Product-specific rates
* Condition-based rates

Master data does NOT contain commission percentages (except 0% for declined items).

---

## Normalization Rules

### RTO Mappings

**Input (messy):**
```
BR-01, BR-02, BR-03 → BIHAR
CG-01, CG-02 → CHHATTISGARH
```

**Output (cleaned):**
```
RTO Mappings:
BR-01, BR-02, BR-03 → BIHAR
CG-01, CG-02 → CHHATTISGARH
```

### Declined Lists

**Input (messy):**
```
Following products are not doable:
- PCV (3W/Other)
- TW bikes with Comp+SATP
```

**Output (cleaned):**
```
Declined/Excluded:
• PCV (3W/Other): 0% commission
• TW bikes (Comp+SATP): 0% commission
```

### Vehicle Classifications

**Input (messy):**
```
GCV includes:
Trucks, Lorries, Tankers
carrying capacity > 12
```

**Output (cleaned):**
```
Vehicle Classifications:
GCV: Trucks, Lorries, Tankers (carrying capacity > 12)
```

---

## Separation Rules

### Is it Master Data?

✅ **YES** if:
* Maps codes to names (RTO codes, state codes)
* Lists declined/excluded items
* Defines terms or categories
* Reference table used across products
* No commission percentages (except 0% declined)

❌ **NO** if:
* Contains commission rates (12%, 10%, etc.)
* Contains premium slabs (0-25K, 25-50K)
* Product-specific rules
* Condition-based commission (NCB, channel, etc.)

### Example: Ambiguous Table

```
State | Status
TN    | Declined
KA    | 12%
MH    | 10%
```

This is **NOT master data** → it contains commission rates.
But the TN row IS master data (declined = 0%).

**Solution:**
* In `master_data_sections`: Note "TN declined in State Commission Table"
* In `commission_data_sections`: Note "State Commission Table (KA, MH)"
* In `normalization_notes`: "Mixed master data and commission rules in same table"

---

## Examples

### Example 1: Pure Master Data

**Input:**
```
MASTER DATA SHEET

RTO Mapping:
UP-16, UP-14 → NCR_UP
HR-72, HR-55 → NCR_HR

Declined States:
TN, WB, NE States
```

**Output:**
```json
{
  "cleaned_content": "RTO Mapping:\nUP-16, UP-14 → NCR_UP\nHR-72, HR-55 → NCR_HR\n\nDeclined States:\nTN, WB, NE States",
  "master_data_sections": ["RTO Mapping", "Declined States"],
  "master_data_table_count": 2,
  "commission_data_sections": [],
  "normalization_notes": "Clean master data sheet, no commission rules present"
}
```

### Example 2: Mixed Content

**Input:**
```
Commission Rates - January 2024

RTO Codes:
DL-01 → NCR_DL

4W Comprehensive:
Premium 0-25K: 12%
Premium 25-50K: 14%

Declined: TN, WB
```

**Output:**
```json
{
  "cleaned_content": "Commission Rates - January 2024\n\nRTO Codes:\nDL-01 → NCR_DL\n\n4W Comprehensive:\nPremium 0-25K: 12%\nPremium 25-50K: 14%\n\nDeclined: TN, WB",
  "master_data_sections": ["RTO Codes", "Declined States List"],
  "master_data_table_count": 1,
  "commission_data_sections": ["4W Comprehensive Rates"],
  "normalization_notes": "Mixed document: contains both master data (RTO codes, declined list) and commission rules (4W rates)"
}
```

---

## Strict Rules

* Do NOT extract master data values (Pass 2 will do this)
* Do NOT interpret mappings or classifications
* Do NOT normalize terminology (keep as stated)
* Do NOT infer missing master data
* Preserve original content, only improve formatting
* Remove duplicates only if identical
* If conflicting versions exist → note in normalization_notes
* If unsure if something is master data → note in normalization_notes
* Output must be valid JSON
* No commentary outside JSON
