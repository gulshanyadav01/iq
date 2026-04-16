# Objective

Extract **document-level metadata and universal settings only**.

Return **strict JSON only**. No explanation.

---

## Extract ONLY These Fields

### 1. Source Information

* `filename`
* `document_type` (email | pdf | excel | other)
* `structure_notes` (brief description if relevant)

### 2. Provider Information

* `provider_name`
* `provider_email`
* `provider_phone`

Extract exactly as written.
If not present → return empty string.

### 3. Commission Period

* `period_label`
* `start_date`
* `end_date`
* `effective_email_date` (if email thread)

For email threads:

* Use the **latest email date** as reference.
* Do not calculate or normalize dates.
* Extract exactly as written.

**Inferring end date from email thread context:**

If the email thread contains messages that implies that rate/commission structure will continue:

→ Use the **latest month mentioned** in continuation/confirmation messages as the end of the period.
→ Set `end_date` to the last day of that month (e.g., "July 2025" → "July 31, 2025").
→ Set `period_label` to reflect the full range (e.g., "May–July 2025").

If multiple months are mentioned (e.g., "June 2025" and "July 2025"), use the latest one.

### 4. Global Settings (Universal Only)

Populate only if the condition applies to **every commission rule in the document**.

Examples of valid global settings:

* GST percentage applicable to all commissions
* Commission applied on OD premium (if universal)
* Vehicle type restriction (if entire document is for one type)
* State restriction (if entire document applies to same geography)
* Universal deductions

If a condition varies by:

* product
* fuel
* NCB
* geography
* business type
* slab

→ Leave it empty.

---

## Inheritance Check

Set `inherits_from_previous: true` ONLY if the document **explicitly confirms** rates are unchanged, for example:

* “Rates unchanged”
* “Same as previous”
* “Continued from last month”
* “No change in commission”
* “Please continue the same grid”
* “Same structure applies”
* "A simple OK message in response to confirmation question"

Then also add a short explanation in `inheritance_note`.

**Do NOT set `inherits_from_previous: true` if:**

* The email is a request for confirmation, not a confirmation itself

---

## Strict Rules

* Do NOT extract products
* Do NOT extract commission rates
* Do NOT extract rule-level conditions
* Do NOT infer missing data
* Do NOT repeat values
* If information is absent → return empty field
* Output must be valid JSON
* No duplicate keys
* No commentary
