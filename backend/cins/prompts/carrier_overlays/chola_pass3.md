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
and standalone-OD are distinct policy types in the deal grid.

The **Package grid is NOT wholesale comprehensive**: it carries BOTH a `PACK` (comprehensive)
and an `ACT` (third-party) sub-column per state, and each must be tagged from its own column
header — see "Chola — Private Car Package COLUMN LAYOUT (ACT / PACK are load-bearing)" below.
Only the SOD grid is standalone-OD.

### Chola — Private Car Package COLUMN LAYOUT (ACT / PACK are load-bearing)

The Private Car **Package** table pairs TWO coverage sub-columns per state — the header row
reads `<state> > ACT | <state> > PACK` for every state group (e.g.
`AP/TS > ACT | AP/TS > PACK | ... | TN > ACT | TN > PACK | ...`). `ACT` is the **Third-Party /
Liability** payout (T&C: "Private Car Liability policy Pay-out is on TP/PA Premium"); `PACK` is
the **Package / Comprehensive (OD)** payout. **Both columns carry real rates** and BOTH must be
emitted — do NOT collapse the whole grid to comprehensive from the `PC [PACK]` PROD token.

**Which table this applies to — scope by the PROD token, NOT the title:**

- **Package table** — PROD token `PC [PACK]`: coverage is **column-derived**. Build the `col_map`
  with one entry per `(state, ACT|PACK, col_index)` and set `coverage_type_text` to the sub-column
  header token **verbatim** — `"ACT"` or `"PACK"` (the transformer maps `ACT` → third-party,
  `PACK` → comprehensive). Emit one rule per non-blank `(state, coverage)` cell; **skip blank /
  `0` / `0%` cells** (states with a blank ACT rate simply produce no third-party rule —
  source-faithful).
- **SOD tables** — PROD token `PC [SOD]`: coverage is **table-derived**, unchanged from the SAOD
  section above — every emitted cell gets `coverage_type_text = "SAOD"` regardless of which
  sub-column the value sits in. The SOD grids reuse the same `ACT | PACK` header shape but their
  ACT sub-columns are blank; the graded value is the SOD rate. **Per-column ACT/PACK coverage
  applies to the Package table ONLY** — a naive "read PACK per column" rule here would wrongly
  re-tag SOD rows comprehensive.

**This per-column ACT/PACK rule is Private Car Package ONLY.** It applies solely to a grid
that has PAIRED `<state> > ACT | <state> > PACK` header columns (two distinct rate cells per
state). Do NOT apply it to any other grid. In particular, the **Two Wheeler** grid is a
FLATTENED single-value make×band grid (`<Make> > <band> > <value>`) — each cell holds ONE rate.
Never emit both a `comprehensive` AND a `third-party` rule from a single-value cell: that
fabricates a duplicate third-party deal carrying the Package rate. A grid cell with one value
produces exactly one rule; emit third-party ONLY from a cell that lives in its own ACT column
(or an ACT-labelled row with its own distinct value).

**Private Car Package layout is NOT the CV layout.** Package rows are CC-band row labels
(`UPTO 1000 CC`, `1000 TO 1500 CC`, `ABOVE 1500 CC`) with NO MAKE / MODEL columns — do NOT apply
the CV `row[4] = MAKE / row[5] = MODEL` recipe here, and leave `vehicle_make` / `vehicle_model`
unset. Strip any `[P]`/`[D]` fuel suffix per the fuel-type section above (SOD grids only; the
plain Package bands have no suffix, so `fuel_type` stays unset).

**ACT / third-party rows are never CPA-deducted.** Do NOT attach the CPA `conditional_deduction`
qualifying clause to ACT rules (see "Chola — CPA deduction footnote": "TP/PA-only rules are never
deducted"). Only the `PACK` (comprehensive-OD) rows carry the CPA clause and feed the `cpa_split`.

**Column indices are LITERAL and the state text is VERBATIM.** Build `col_map` by
enumerating the header row with `parse_md_row` and using the raw split index `i` — the
markdown preserves blank spacer cells, so `i` is the true column of the data cell below.
Filtering blank headers or compacting the list before indexing shifts every column after
a spacer by one, so a state reads a _neighbouring_ state's rate (the progressive
non-uniform drift observed on the Apr grid: JH read GJ's column, CG read JH's, …). Take
the state as everything before the **last** `>` unchanged — `"UP- East > ACT"` is state
`"UP- East"`, coverage `"ACT"` (NOT `"East"`).

Generated-code shape (Package table):

```python
# Build col_map by LITERAL enumerate — never filter/compact the header cells first.
cells = parse_md_row(header_line)
col_map = []
for i, h in enumerate(cells):                       # i is the literal column index
    if ">" in h:                                    # a "<state> > <ACT|PACK>" header
        state = h.rsplit(">", 1)[0].strip()         # verbatim; NOT h.split()[-1]
        coverage = h.rsplit(">", 1)[1].strip()      # "ACT" or "PACK"
        col_map.append((state, coverage, i))
# Self-check: one col_map entry per ">"-bearing header cell. If this fails you
# compacted the header row and the indices are shifted.
assert len(col_map) == sum(1 for h in cells if ">" in h)

for state, coverage, col_idx in col_map:            # coverage is "ACT" or "PACK", verbatim
    raw_val = row[col_idx].strip() if col_idx < len(row) else ""
    pct = to_pct(raw_val)
    if not pct or pct == "0%":
        continue
    rule = {
        "product_name": "Private Car",
        "coverage_type_text": coverage,             # "ACT" -> third-party, "PACK" -> comprehensive
        "state_text": state,
        "conditions": dict(base_conditions),        # CC band only; NO make/model
        "commission_value": pct,
        # PACK rules carry the CPA clause; ACT rules do NOT:
        "qualifying_clauses": list(qualifying_clauses) if coverage.upper() == "PACK" else [],
        # ... other fields as per base template ...
    }
    result.append(rule)
```

### Chola — NCB > 25% parallel table set

Chola Private Car ships the CC-band grids TWICE: a **base** set and a parallel set whose
titles end `… - NCB GT 25%` and whose row labels carry a `[ NCB GT 25%]` suffix (e.g.
`UPTO 1000 CC[ NCB GT 25%]`). The NCB > 25% grid pays **higher rates** — the two sets are
**NOT duplicates**. Both table sets **must be emitted**.

- Put the **full table title verbatim** into `source_section` for every rule (e.g.
  `"Private Car Package - NCB GT 25%"`). The transformer detects the `NCB GT 25%` marker
  there and sets the NCB tier.
- **Preserve the `[ NCB GT 25%]` row-label suffix in `rule_text`** — do not strip it. It is a
  second place the marker survives.
- **Do NOT set `conditions["ncb"]` yourself.** The transformer derives the NCB tier from the
  marker (NCB>25% → `ncb=True`) and flips the paired base twin to `ncb=False`. A codegen that
  guesses `"ncb": "Yes"` on the base table would invert the entire product.

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

**Make-less body-only columns:** a few trailing columns are just `<body> > <value>` with the
make omitted AND no adjacent make to carry forward (e.g. `SCOOTER > 0.37`, `SCOOTER > -0.055`).
The body word (`SCOOTER`/`BIKE`) is a `vehicle_sub_class`, NEVER a `vehicle_make`, and the numeric
`part[-1]` is the commission VALUE — never route it into `cc`. Emit the rule with
`conditions["vehicle_sub_class"] = "scooter"` (leave `vehicle_make` unset, `cc` unset). Do NOT put
`SCOOTER` in `vehicle_make` or `0.37`/`-0.05` in `cc`.

#### Chola — Two Wheeler ACT (Third-Party / Liability) rows

Besides the `NEW(... NOP)` / renewal **Package** tier rows (which are comprehensive), each state
also has ONE **ACT row** whose `Type` cell reads `ACT` instead of a NOP tier
(e.g. `| | AP | ACT | 0 | 0.3 | 0 | 0 | ... |`). This row carries the **Third-Party / Liability**
rate (`TW Liability policy is on TP/PA Premium`), positionally aligned to the SAME make×band
columns as the Package rows. It is **load-bearing** — do NOT drop it, and do NOT confuse it with a
Package tier.

Build the make×band column-index map ONCE from the header row's `<Make> > <band>` columns (the
same map the Package NOP-tier rows use). Then for an ACT row, for EACH make×band column with a
**non-zero** value, emit ONE rule:

- `coverage_type_text = "ACT"` — the transformer maps ACT → `policy_type = "third-party"`.
- `state_text` = the row's state; `vehicle_make` = the column's make; the column's band → `cc`
  (via `parse_cc_range`) or `vehicle_sub_class` for a body word (`SCOOTER`/`BIKE`) — exactly as
  the Package parsing routes it. `All Electric Make` → `fuel_type = "Electric"`.
- `commission_value = to_pct(cell)` — the **ACT row's own cell value** (e.g. `0.3` → the Liability
  rate). NEVER copy the Package/NOP-tier value onto the ACT rule; read the ACT row's own cells.
- Do **NOT** set `policies_count` / `customer_type_text` — Liability is not NOP-tiered.
- Do **NOT** attach the CPA `conditional_deduction` clause — TP/PA rules are never CPA-deducted.
- Skip `0` / blank cells (most ACT cells are 0; the real rates cluster in the SCOOTER band).

The ACT row is the ONLY source of Two Wheeler third-party rules. A TW `third-party` rule whose
value equals the Package rate for the same make×band is a duplication bug (see the single-value
cell rule above), not a real Liability rate.

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
`10_ABOVE_47T`) are **4-Wheeler-and-Above** weight bands — parse exactly as today
(`parse_gvw_range` on the sub-class label → `conditions["gvw"]`,
`number_of_wheels = "4W & Above"`). `2_UPTO_3.5T` is a genuine 4-wheel light LCV band, NOT a
3-wheeler.

### Chola — PCCV < 6 (passenger taxis under 6 seats)

The `PCCV < 6` table splits taxis by seating and engine CC. Every row here is a **taxi** with
seating band **UPTO 5** (`conditions["seating_capacity"] = "UPTO 5"`,
`conditions["vehicle_sub_class"] = "taxi"`, `product_type = "PCV"`).

- `1_3W_AUTO` / `1_3W_AUTO[Electric]` — passenger 3-wheeler auto. Set
  `conditions["number_of_wheels"] = "3W"`, seating `"UPTO 5"`. Do NOT set `conditions["gvw"]`.
  Handle `[Electric]` exactly like the GVW `[Electric]` siblings (strip → `fuel_type = "Electric"`).
- `2_4W_LT_1500CC` / `3_4W_GT_1500CC` — 4-wheel taxis split by engine CC at 1500. These are
  **CC bands, NOT GVW bands**: set `conditions["cc"] = parse_cc_range(label)`
  (`2_4W_LT_1500CC` → `"0-1500"`, `3_4W_GT_1500CC` → `">1500"`) and
  `conditions["number_of_wheels"] = "4W & Above"`, seating `"UPTO 5"`. Do **NOT** call
  `parse_gvw_range` and do **NOT** set `conditions["gvw"]` on these rows. Positional
  `[Electric]` siblings (`2_4W_LT_1500CC[Electric]`) strip-then-parse like the GVW bands.

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

**Never copy the `TATA/Maruti/Mahindra` make/model list onto the `All Other Make/Models` row.**
Keep the sentinel row's make/model verbatim as `All Other Make/Models` — the transformer fans
it out into the correct include/exclude buckets (TMM-excluding-models, and all-other-makes)
downstream. Copying the named list onto the sentinel makes the two rows collapse into one and
loses a deal (the historical export bug).

### Chola — Commercial Vehicle segment → `product_type` (SubProduct)

Each commercial table names the vehicle **segment** in its PROD column / table title. Set
`product_type` per row so the transformer splits Commercial Vehicle into its sub-products;
keep `product_name = "Commercial Vehicle"` for all of them:

- `GCCV` table → `product_type = "GCV"`
- `PCCV < 6` table → `product_type = "PCV"` (see the PCCV < 6 subsection above)
- `MSV` table → `product_type = "MISC-D"` (see the MSV subsection below)

The `PCCV > 6` table is **body-dependent** — it is NOT wholesale Buses. Route each row by its
body, all with seating band **ABOVE 6** (`conditions["seating_capacity"] = "ABOVE 6"` — write
it exactly like that, qualifier before the number):

- `1_SCHOOL_BUS` → `product_type = "Buses"`, `conditions["vehicle_sub_class"] = "school bus"`
- `2_STAFF_BUS` → `product_type = "Buses"`, `conditions["vehicle_sub_class"] = "staff bus"`
- `3_BIG_TAXIS` → `product_type = "PCV"`, `conditions["vehicle_sub_class"] = "taxi"`
- `4_MAXI_CAB` → `product_type = "PCV"`, `conditions["vehicle_sub_class"] = "maxi cab"`

**DO fold big-taxi / maxi-cab rows into PCV** (they are taxis, not buses). Only the school-bus
and staff-bus rows are Buses. This corrects the earlier blanket "PCCV > 6 → Buses" mapping.

### Chola — MSV (misc-D) rows

The `MSV` table publishes one distinct product row per body — emit **one rule per row**, all
`product_type = "MISC-D"`, `product_name = "Commercial Vehicle"`. Their identity lives in
`conditions["vehicle_sub_class"]` (+ `business_type`), so they survive as separate deals:

- `1_TRAC[RENEWAL]` → `conditions["vehicle_sub_class"] = "tractor"`, `customer_type_text = "renewal"`.
  Strip `[RENEWAL]` from the label like `[Electric]` before sub-class extraction.
- `2_EXCAVATOR` → BOTH the MAKE and MODEL cells repeat the same body enumeration (Loader,
  Excavators/Earth Movers, Crane, Bulldozers/Bullgraders, Road Rollers, Fork Lift Trucks). Put
  that enumeration into `conditions["vehicle_sub_class"]` (a list) ONLY — leave BOTH
  `conditions["vehicle_make"]` AND `conditions["vehicle_model"]` UNSET. These bodies are vehicle
  sub-classes, not a make/model split, so the generic CV `row[4]→make` / `row[5]→model` rule does
  NOT apply here — do not copy the enumeration into make or model. (A deterministic transformer
  guard also drops any CV rule whose make list is identical to its model list, but do not rely on
  it — emit make/model unset.)
- `3_HARVESTOR` → `conditions["vehicle_sub_class"] = "harvester"`.
- `4_OTHERS` → emit a distinct rule with `conditions["vehicle_sub_class"] = "others"` and the
  row's (empty / blank) rates. Do **NOT** skip this row — it is an explicit zero-payout matching
  row so the segment is never silently dropped. Do NOT set `deal_type`.
