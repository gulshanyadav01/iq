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
    (`SCOOTER`, `BIKE`) → `conditions["vehicle_sub_class"]`.
    - **Electric kW bands** (`7 KW Bike`, `7 KW SCOOTER`, `7 KW - 16 KW`, `> 16 KW` under the
      `All Electric Make` group) are power ratings, NOT engine CC. `parse_cc_range` returns ""
      for them, so leave `cc` UNSET — electric two-wheelers carry no CC filter. Still set
      `conditions["fuel_type"]="Electric"` (from the `All Electric Make` bucket) and route the
      body word (`BIKE`/`SCOOTER`) to `conditions["vehicle_sub_class"]`. Never invent a cc from
      the kW number (that produced the cc=(7,7) collapse bug).
  - `part[-1]` → commission value via `to_pct`.

One rule per non-empty, non-zero `Make > band > value` cell, inheriting the row's state +
policies_count + customer_type — this preserves the make × CC fan-out.

**Make-not-repeated guard:** some sub-columns under one OEM omit the make and the cell is just
`<band> > <value>` (e.g. `150_350cc > 0.3`). Do NOT treat `part[0]` as the make when it parses
as a CC band (`parse_cc_range(part[0])` non-empty) or a body type — carry the LAST seen make
forward, use `part[0]` as the band, and take the VALUE from `part[-1]`. Never put a CC band
(`150_350cc`) into `vehicle_make`, and never put a rate (`0.205`) into `cc`.

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

**CRITICAL codegen shape — do NOT append trailing to every data row.** The trailing
`parts[2:]` of each HEADER cell belongs to the ONE merged `1_GCCV_3W` row, not to the
positional weight-band data rows below. Emit the 3-wheeler rules in a SEPARATE loop over the
header's `col_map` (once per state×coverage), each with
`conditions["number_of_wheels"]="3W"`, `vehicle_sub_class="1_GCCV_3W"`, and NO gvw. Then run
the normal per-data-row loop using ONLY `row[col_idx]` (never `vals.extend(trailing)`) — mixing
the header's 3W values into every weight-band row misattributes rates and inflates the rule
count several-fold.

**Only recover the merged 3W header for the GCCV table** (`product_type == "GCV"`). Every
table reuses the same copy-pasted header template, so the trailing `1_GCCV_3W` values also
appear in the `PCCV < 6` / `PCCV > 6` / `MSV` headers — but there they are stale duplicates,
NOT real data. Skip the trailing-value 3W emission for any non-GCCV table (otherwise goods
3-wheelers leak into Buses / MISC-D as `number_of_wheels=3W` rules with no sub-class).

Passenger 3-wheelers — the `1_3W_AUTO` / `1_3W_AUTO[Electric]` rows under "PCCV < 6" — appear
as PROPER positional data rows (not merged into the header). Parse them with the normal
`col_map`, but force `conditions["number_of_wheels"] = "3W"` and do NOT set `conditions["gvw"]`
(`parse_gvw_range("1_3W_AUTO")` is empty, so keep the residual as `vehicle_sub_class`).

All heavier GCCV sub-classes (`2_UPTO_3.5T`, `3_3.5T_TO_7.5T`, `4_7.5T_TO_12T`, … through
`10_ABOVE_47T`, and `2_4W_LT_1500CC` / `3_4W_GT_1500CC`) are **4-Wheeler-and-Above** weight
bands — parse exactly as today (`parse_gvw_range` on the sub-class label → `conditions["gvw"]`,
`number_of_wheels = "4W & Above"`). `2_UPTO_3.5T` is a genuine 4-wheel light LCV band, NOT a
3-wheeler.

### Chola — Commercial Vehicle positional `[Electric]` fuel rows

Aside from the merged 3W header (above), the heavier bands publish the **electric sibling as
its own positional data row** whose SUBCLASS label carries a trailing `[Electric]` token
(e.g. `2_UPTO_3.5T[Electric]`, `4_7.5T_TO_12T[Electric]`). Treat it as a distinct rule:

- Strip `[Electric]` from the label BEFORE `parse_gvw_range` / sub-class extraction (so the
  GVW band still parses, e.g. `2_UPTO_3.5T[Electric]` → gvw band `UPTO 3.5T`).
- Set `conditions["fuel_type"] = "Electric"` on that row.
- The non-suffixed sibling (`2_UPTO_3.5T`) leaves `fuel_type` UNSET — it applies to all fuels.

This keeps Electric and Non-Electric as separate rows so downstream deal management assigns
each its own Deal ID.

### Chola — Commercial-vehicle COLUMN LAYOUT (MAKE / MODEL are load-bearing)

The GCCV / PCCV / MSV data rows have a FIXED leading-column layout — read all four dimension
columns, not just SUBCLASS. After `parse_md_row`, the leading cells are:

```
row[0], row[1]  = blank spacer columns
row[2]          = PROD      (segment: GCCV / PCCV<6 / PCCV>6 / MSV → product_type)
row[3]          = SUBCLASS  (weight/wheel band slug, e.g. 2_UPTO_3.5T)
row[4]          = MAKE      → conditions["vehicle_make"]
row[5]          = MODEL     → conditions["vehicle_model"]
row[6:]         = the State × ACT/PACK rate cells (aligned to col_map)
```

Your generated code MUST read `row[4]` and `row[5]`. Rules:

- Set `conditions["vehicle_make"]` from the MAKE cell and `conditions["vehicle_model"]` from the
  MODEL cell (canonical keys — the short keys `make`/`model` are dropped by the transformer).
- SKIP make/model when the MAKE cell is blank OR equals the SUBCLASS slug (the heavier bands
  repeat the slug in MAKE/MODEL — that is not a real split, so leave make/model unset).
- Keep generic buckets verbatim (`All Other Make/Models`).

The light-LCV band `2_UPTO_3.5T` (and `2_UPTO_3.5T[Electric]`) IS graded by make/model — it
publishes two separate data rows with DIFFERENT rates:

```
| GCCV | 2_UPTO_3.5T | TATA/ Maruti/ Mahindra | Ace, Super Ace, Yodha, Xenon, … | 0.425 | 0.475 | … |
| GCCV | 2_UPTO_3.5T | All Other Make/Models   | All Other Make/Models           | 0.2   | 0.3   | … |
```

Each is its own data row, so the normal per-row loop already emits two rules — just make sure
`vehicle_make`/`vehicle_model` are attached (else the two collapse to indistinguishable
duplicates with conflicting rates).

### Chola — Commercial Vehicle segment → `product_type` (SubProduct)

Each commercial table names the vehicle **segment** in its PROD column / table title. Set
`product_type` per row so the transformer splits Commercial Vehicle into its four
sub-products; keep `product_name = "Commercial Vehicle"` for all of them:

- `GCCV` table  → `product_type = "GCV"`
- `PCCV < 6` table → `product_type = "PCV"`
- `PCCV > 6` table → `product_type = "Buses"`
- `MSV` table → `product_type = "MISC-D"`

Do NOT fold `PCCV > 6` (school-bus / staff-bus / big-taxi rows) into PCV — buses are their
own sub-product.
