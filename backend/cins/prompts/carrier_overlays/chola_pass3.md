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

Grouping note: for Chola, CNG / LPG / Electric follow the **Petrol** grid, not the
Diesel grid. The deterministic transformer expands these labels accordingly
(`[P]` → {Petrol, CNG, LPG, Electric}; `[D]` → {Diesel} only), so emit the bare
`"Petrol"` / `"Non Petrol"` label here and let the transformer expand it.

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

This CPA deduction applies to **all products, including Commercial Vehicle**, not just
Private Car — attach the structured `conditional_deduction` clause (via
`detect_qualifying_clauses`) to CV rules as well. The downstream `cpa_split` materializer
applies the deduction to the rule's own payout column (OD for Private-Car comprehensive,
Net for CV), so the clause's `applied_on` basis does not need to be product-specific; what
matters is that the clause is **attached** to CV rules. TP/PA-only rules are never deducted.

### Chola — Standalone OD (SAOD) grid tagging

Chola publishes a separate **Standalone OD** grid (headed "SAOD", "Standalone OD", or
"Stand Alone OD") alongside the Package/Comprehensive grid. Rules parsed from a Standalone-OD
section MUST be tagged as standalone-OD, NOT comprehensive:

- set `coverage_type_text = "SAOD"` (the transformer maps this to `policy_type = "saod"`),
- keep `conditions["deal_parameter"] = "OD"` (or the section's OD basis).

Do NOT emit Standalone-OD rows as `coverage_type_text = "Comprehensive"` — comprehensive-OD
and standalone-OD are distinct policy types in the deal grid. Only the Package grid is
comprehensive.

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

### Chola — Commercial Vehicle GCCV 3-Wheeler row merged into the header

In Chola's commercial GCCV grids (table names like "GCCV Commission", "PCCV < 6",
"PCCV > 6"), the **first data row — the goods 3-wheeler, `1_GCCV_3W`** — was merged by the
source into the column-header row. Its commission values are NOT a separate data row; they
are appended as a trailing value segment inside each state×coverage **header** cell:

```
SUBCLASS > 1_GCCV_3W > 1_GCCV_3W[Electric] | ... | AP/TS > ACT > 0.325 | AP/TS > PACK > 0.375 | TN > ACT | TN > PACK > 0.275 | MH/GA > PACK > 0.47 > 0.45 | GJ > PACK > 0.5 | ...
```

So each header cell is `<STATE> > <ACT|PACK> [> <value>] [> <electric_value>]`. The first two
`>` segments are the state group and the coverage sub-column you ALREADY use to build the
`col_map` (keep doing that for the positional weight-band rows below). The **third (and
optional fourth) segment is the swallowed `1_GCCV_3W` commission row** and must be recovered
into 3-wheeler rules — otherwise goods 3-wheelers get no rates at all.

After building `col_map`, for EACH `(state, coverage, col)` whose header cell has a trailing
value, emit a 3-wheeler rule:

- split the header cell on `>`; `parts[0]` = state, `parts[1]` = coverage (`ACT`/`PACK`),
  `parts[2:]` = the 3W value(s). Skip the cell if there is no `parts[2]` or it is blank.
- `conditions["number_of_wheels"] = "3W"`. Do NOT set `conditions["gvw"]` — the goods
  3-wheeler is all-GVW (never weight-graded).
- `coverage_type_text` = the coverage token verbatim (`ACT` → third-party, `PACK` →
  comprehensive; the transformer maps these).
- `conditions["vehicle_sub_class"] = "1_GCCV_3W"`; `product_name = "Commercial Vehicle"`.
- `state_text` = the state group (`AP/TS`, `MH/GA`, `GJ`, …) verbatim — the transformer
  expands the slash-grouped codes.
- Fuel split (only when a 4th segment is present):
  - one trailing value (`> v`) → ONE rule, all fuel types (leave `fuel_type` unset),
    `commission_value = to_pct(v)`.
  - two trailing values (`> v_base > v_elec`) → emit TWO rules: one with
    `conditions["fuel_type"] = "Petrol, Diesel, CNG, LPG"` value `v_base`, and one with
    `conditions["fuel_type"] = "Electric"` value `v_elec`.
- Attach the same CPA `conditional_deduction` qualifying clause you attach to every other CV
  rule (see "Chola — CPA deduction footnote"); 3-wheeler without-CPA is deducted too.
- Skip `0` / `0%` / empty trailing values.

The `[Electric]` token in the merged SUBCLASS label (`1_GCCV_3W > 1_GCCV_3W[Electric]`) is
the reason a cell can carry two trailing values — it is NOT a state or coverage column.

Passenger 3-wheelers — the `1_3W_AUTO` / `1_3W_AUTO[Electric]` rows under "PCCV < 6" — appear
as PROPER positional data rows (not merged into the header). Parse them with the normal
`col_map`, but force `conditions["number_of_wheels"] = "3W"` and do NOT set `conditions["gvw"]`
(`parse_gvw_range("1_3W_AUTO")` is empty, so keep the residual as `vehicle_sub_class`).

All heavier GCCV sub-classes (`2_UPTO_3.5T`, `3_3.5T_TO_7.5T`, `4_7.5T_TO_12T`, … through
`10_ABOVE_47T`, and `2_4W_LT_1500CC` / `3_4W_GT_1500CC`) are **4-Wheeler-and-Above** weight
bands — parse exactly as today (`parse_gvw_range` on the sub-class label → `conditions["gvw"]`,
`number_of_wheels = "4W & Above"`). `2_UPTO_3.5T` is a genuine 4-wheel light LCV band, NOT a
3-wheeler.
