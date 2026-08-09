# Objective

Extract **document-level metadata and universal settings only**.

Return **strict JSON only**. No explanation.

---

## Extract ONLY These Fields

### 1. Source Information

- `filename`
- `document_type` (email | pdf | excel | other)
- `structure_notes` (brief description if relevant)

### 2. Provider Information

- `provider_name`
- `provider_email`
- `provider_phone`

For `provider_name`, return the **short code** of the insurance carrier — must be one of the 20 below:

```text
Bajaj    HDFC      Liberty     Oriental    Shriram
Chola    ICICI     Magma       Reliance    United
Digit    Iffco     National    RSGI        Universal
FG       Kotak     New India   SBI         Zuno
```

`FG` = Future Generali. `RSGI` = Royal Sundaram (Royal Sundaram General Insurance). `Zuno` = Zuno General (formerly Edelweiss General).

Identify the carrier from any signal in the document — Subject line, To/Cc/From recipient domains, body text, attached document, or quoted/forwarded thread. **Match the underwriting carrier, NOT the broker, aggregator, or forwarder** (e.g., pbpartners.com, policybazaar.com, insurance-broker domains are NOT carriers — look past them to the carrier they correspond with).

If the carrier cannot be confidently identified as one of the 20 → return empty string. Do not invent or approximate.

Extract `provider_email` and `provider_phone` exactly as written. If not present → return empty string.

### 3. Commission Period

- `period_label`
- `start_date`
- `end_date`
- `effective_email_date` (if email thread)

For email threads:

- Use the **latest email date** as reference.
- Do not calculate or normalize dates.
- Extract exactly as written.

**Fallback when no explicit period is stated (emails):**

Many grids arrive as a plain email with rates/tables but **no written commission
period** (no "w.e.f.", no "valid till", no month named in the body). In that case
**do not leave `start_date` empty** — use the email's own sent date from the
`Date:` header as `start_date`.

- Extract it exactly as written in the `Date:` header (e.g. `Date: Wed, 02 Jul 2025 ...` → `start_date: "02 Jul 2025"`).
- Leave `end_date` empty unless a continuation/end is stated.
- Never use today's date, the processing date, or any date not present in the document.

**Inferring end date from email thread context:**

If the email thread contains messages that implies that rate/commission structure will continue:

→ Use the **latest month mentioned** in continuation/confirmation messages as the end of the period.
→ Set `end_date` to the last day of that month (e.g., "July 2025" → "July 31, 2025").
→ Set `period_label` to reflect the full range (e.g., "May–July 2025").

If multiple months are mentioned (e.g., "June 2025" and "July 2025"), use the latest one.

### 4. Global Settings (Universal Only)

Populate only if the condition applies to **every commission rule in the document**.

Examples of valid global settings:

- GST percentage applicable to all commissions
- Commission applied on (e.g., "Net Premium", "OD Premium") — only if stated universally for all rules
- Vehicle type restriction (if entire document is for one type)
- State restriction (if entire document applies to same geography)
- Universal deductions

If a condition varies by:

- product
- fuel
- NCB
- geography
- business type
- slab

→ Leave it empty.

---

## Commission Data Detection

Set `has_commission_data: true` if the **email body** (not attachments) contains any of:

- Commission rates, percentages, or grid values (e.g. "12%", "0.15", "15 per cent")
- Commission tables or rate grids
- Product-wise commission structures (e.g. "Private Car: 12%", "4W Comp | 12% | 10%")
- Tier/slab-based commission rules (e.g. "up to 1 lakh: 15%", "slab 1-3: 10%")
- Conditions tied to specific commission values (e.g. "for comprehensive: 12%, for TP: 8%")

**Do NOT set `has_commission_data: true` if** the email body only contains:

- Metadata (provider name, period, dates)
- Global settings (GST %, applied on, deductions)
- References to attachments ("please find attached", "see attached file")
- Master/reference data (RTO lists, declined lists, state codes)
- Inheritance/continuation confirmations ("same as last month", "rates unchanged")

---

## Inheritance Check

Set `inherits_from_previous: true` ONLY if the document **explicitly confirms** rates are unchanged, for example:

- "Rates unchanged"
- "Same as previous"
- "Continued from last month"
- "No change in commission"
- "Please continue the same grid"
- "Same structure applies"
- "A simple OK message in response to confirmation question"

Then also add a short explanation in `inheritance_note`.

**Do NOT set `inherits_from_previous: true` if:**

- The email is a request for confirmation, not a confirmation itself
- **A commission grid/rate table is actually attached in the document content**
  (any sheet or table with commission rates). A forwarded cover note may say
  "continuing the same grid as last month" while still shipping the full month's
  grid — in that case the attached grid is the source of truth and MUST be
  extracted, so set `inherits_from_previous: false`. Only inherit when there is
  genuinely no rate table anywhere in the document to extract.

---

## Strict Rules

- Do NOT extract products
- Do NOT extract commission rates
- Do NOT extract rule-level conditions
- Do NOT infer missing data
- Do NOT repeat values
- If information is absent → return empty field
- Output must be valid JSON
- No duplicate keys
- No commentary
