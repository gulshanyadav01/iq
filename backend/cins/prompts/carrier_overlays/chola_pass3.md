## CARRIER-SPECIFIC RULES — Chola

These rules apply ONLY to this document because its provider is **Chola**. They are
injected into the Pass-3 codegen prompt at runtime based on the resolved provider; do not
generalize them to other carriers.

### Chola — Private Car fuel-type suffix

When `product_name` resolves to `Private Car`, the SUBCLASS / CC labels in Chola's
Standalone OD grids use a trailing fuel-type suffix in square brackets that MUST be parsed
into `conditions["fuel_type"]`:

- `[P]` (e.g. `UPTO 1000 CC[P]`, `1000 TO 1500 CC[P]`, `ABOVE 1500 CC[P]`) →
  `conditions["fuel_type"] = "Petrol"`
- `[D]` (e.g. `UPTO 1000 CC[D]`, `ABOVE 1500 CC[D]`) →
  `conditions["fuel_type"] = "Non Petrol"`

Strip the suffix from the label before passing it to `parse_cc_range` so the CC
range parses cleanly (`"UPTO 1000 CC[P]"` → CC range `"UPTO 1000 CC"`,
fuel_type `"Petrol"`).

If the SUBCLASS label has NO `[P]` / `[D]` suffix (e.g. the Package grid uses
plain `UPTO 1000 CC`), leave `fuel_type` unset — the rule applies to all fuel
types.

### Chola — CPA deduction footnote

The document often contains a notes/footnote clause of the form "if CPA is not collected
for individual name then 1.5% of OD Pay-out will be deducted" (wording, percentage, and
premium basis may vary). `extract_notes(tables)` will surface it in `notes_text`. Pass it
through verbatim on every rule's `other_conditions` field — do NOT try to translate the
clause into the rule's `commission_value` or `conditions` fields. The structured rule
schema cannot model a customer-type-dependent percentage deduction off OD Pay-out at the
rule level.

Additionally emit a structured representation under `qualifying_clauses` (see section
"QUALIFYING CLAUSES" in the base prompt) so downstream booking pipelines can ingest the
modifier programmatically.

### Chola — Two Wheeler flattened make × CC grid

When `product_name` resolves to `Two Wheeler`, the grid is flattened so each commission
cell is `<Make> > <band> > <value>` (the rate is INSIDE the cell), and each row's leading
cells carry the state and policy-count tier:

```
| | State (RTO) > AP | Type > NEW(100-500 NOP) | Hero OEM > 150cc > 0.425 | Hero OEM > SCOOTER > 0.425 | TVS OEM > 150_350cc > 0.3 | Honda > 350cc > 0 | All Electric Make > 7 KW Bike > 0.17 | ... |
```

Do NOT use the generic `State > Coverage` column-map here. For EACH row, parse cell-by-cell:

- `State (RTO) > AP` / `State > AP` → `state_text = "AP"` for the row.
- `Type > NEW(100-500 NOP)` → `customer_type_text` from the business word + run
  `parse_nop_range(...)` → `conditions["policies_count"]`.
- `<Make> > <band> > <value>` → emit ONE rule (skip empty / `0%` values):
  - `part[0]` → `conditions["vehicle_make"]` (`Hero OEM`, `Honda`, `Bajaj`, …). Generic
    buckets stay verbatim (`Others`; `All Electric Make` → also set
    `conditions["fuel_type"]="Electric"`).
  - `part[1]` → `parse_cc_range(part[1])`; non-empty → `conditions["cc"]`. A body type
    (`SCOOTER`, `BIKE`) → `conditions["vehicle_sub_class"]`. `7 KW`-style → `cc`.
  - `part[-1]` → commission value via `to_pct`.

One rule per non-empty, non-zero `Make > band > value` cell, inheriting the row's state +
policies_count + customer_type — this preserves the make × CC fan-out.
