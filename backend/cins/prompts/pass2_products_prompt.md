# Objective

Extract **reference/master data only**.

Return **strict JSON only**. No explanation.

---

## Extract ONLY

### 1. Master Data Entries

Extract reference sheets, mappings, and definitions.

Examples:

* RTO mappings (including RTO-to-category/zone/tier tables)
* Category/zone/tier definitions and their RTO assignments
* Declined/excluded lists
* Vehicle classifications
* State/zone mappings
* General definitions or global reference tables

Do NOT extract:

* Products
* Commission rates
* Rule-level conditions

---

## Output Structure (Per Entry)

For each master entry, extract:

* `entry_type`
  One of:
  `rto_mapping` | `declined` | `vehicle_classification` | `state_mapping` | `general_term`

* `applies_to`
  "all" if global, otherwise exact product/vehicle reference (e.g. "Scooter", "Bike", "2W", "4W"). For tables with separate columns per vehicle type, create one entry per vehicle type column.

* `key`
  Identifier (state code, vehicle type, term name, etc.)

* `values`
  Copy the exact verbatim text from the document. If no verbatim text exists in the document for this entry, use an empty string "". Do NOT explain, justify, or elaborate.

* `notes`
  Only if explicitly stated

* `source_sheet`
  Sheet name if applicable, else empty

* `is_global_rule`
  true only if applies to entire document

* `priority`
  Only if explicitly defined. Do not infer.

---

## Identification Rules

### Global Master Data Section

If a section titled similar to:
“MASTER DATA”
“GLOBAL RULES”
“REFERENCE SHEET”

→ Extract all entries from that section.

Default:

* `is_global_rule = true`
  unless explicitly restricted.

---

## Strict Rules

* Do NOT extract commission percentages
* Do NOT extract product names
* Do NOT infer classifications
* Do NOT summarize — extract verbatim
* Do NOT create entries from explanatory notes
* If no master data exists → return empty array
* Do NOT repeat identical entries
* Each unique (entry_type + key + applies_to) must appear only once
* Output must be valid JSON
* No commentary
* No duplicate keys

---

## Document Structure Notes

Additionally extract:

`document_structure_notes`
Briefly describe how commission rules are organized (1–2 lines max).

Do NOT describe master data here.