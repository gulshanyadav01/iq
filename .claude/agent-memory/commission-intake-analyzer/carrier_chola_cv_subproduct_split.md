# Chola CV sub-product split, electric fuel, EV-TW kW→cc (doc dbe5b4ea)

Chola "July Grid.eml" reprocessed as doc `dbe5b4ea-ec97-4236-a08c-b553f9a68a21`
(attachment `July 1st Grid.xlsx`, sheets `TW july` / `July CV grid` / `July pvt car`).
Five bifurcation fixes landed on the pipeline.

## Round-2 fixes (post-QA)

- **Electric folded into CV non-electric base (export showed `Petrol,Diesel,CNG,LPG,Electric`).**
  Two causes: (1) export renders `fuel_type=None` as the all-5-fuels default; (2) Chola
  `_expand_fuel_group` mapped `petrol → +electric`. That electric-in-petrol-grid rule is
  **Private-Car-only** (PC grid note: "EV = Petrol grid"); CV/TW have a SEPARATE electric
  column. Fixes: `_expand_fuel_group(fuel_type, product_name)` adds electric only for
  `product_name=="Private Car"`; and `_transform_product` pins commercial fuel-None siblings to
  `[petrol,diesel,cng,lpg]` whenever the product has any electric row → Electric vs Non-Electric
  are clean separate deals.
- **TW `vehicle_make="150_350cc"` + `cc_start=1`.** Codegen splits flattened
  `<make> > <band> > <value>` header cells positionally; when a sub-column omits the make, the
  CC band lands in `vehicle_make` and a rate in `cc`. Deterministic guard in
  `_transform_conditions`: a `vehicle_make` item matching a CC-band shape (`'cc' in it` or
  `\d+([_-]\d+)?`) is moved back to `cc` (normalise `_`→`-`) and dropped from make. Overlay also
  got a "make-not-repeated" note.
- **Validated**: GCV Make/Model appears ONLY on `gvw=(0,3500)` up-to-3.5T rows (74 rows), 0
  above 3.5T, 0 on PCV/Buses — rule holds.

## Load-bearing facts

- **CV segment lives in `source_section`, NOT product_type.** Codegen leaves per-entry
  `product_type`/`product_name` = None; the segment is the table name it stamps on each rule:
  `"Table 1: GCCV Commission"` → GCV, `"Table 2: PCCV < 6"` → PCV,
  `"Table 3: PCCV > 6"` → Buses (school/staff/big-taxi), `"Table 4: MSV"` → MISC-D.
  Canonical subproduct vocab (PRODUCT_FIELD_VALUES.md): **GCV, PCV, Buses, MISC-D**.
- Export `SubProduct` = `product.sub_category`; `DealId` = "" (assigned downstream). "Separate
  deals for Electric/Non-Electric" = make them separate ROWS via `fuel_type`, not mint IDs.
- Source marks EV variants with a trailing `[Electric]` on the SUBCLASS label
  (`2_UPTO_3.5T[Electric]`, `1_GCCV_3W[Electric]`). EV two-wheelers are **kW-rated**
  (`All Electric Make > 7 KW Bike`, `7 KW - 16 KW`, `> 16 KW`) — NOT engine CC.

## Fixes (all in this repo)

- **Segment split** (`raw_to_cics_transformer._transform_products` + `_cv_segment_label` /
  `_prod_token_segment` / `_car_segment_label`): commercial groups split by segment →
  4 products all named "Commercial Vehicle" with sub_category GCV/PCV/Buses/MISC-D;
  `_transform_product(segment=...)` pins product_subproduct + unique product_code. Car splits
  Private Car / Taxi. Verified: GCV 998 / PCV 274 / Buses 404 / MISC-D 464.
- **CV [Electric] → fuel_type** (`_transform_conditions` backstop + chola overlay): tags
  `fuel_type=electric` when `[electric]` in subclass/rule_text and no fuel parsed. Verified
  GCV 100 + PCV 96 electric.
- **EV-TW cc bug** (`code_gen_executor.parse_cc_range` rejects any `KW` token + overlay +
  `_transform_conditions` backstop resetting electric-TW cc to open sentinel). Was cc=(7,7)
  for all 164; now (0,99999). Test contract updated in `test_sandbox_helpers.py`.
- Tests: `cins/tests/test_cv_subproduct_and_electric.py`.

## Codegen Make/Model + 3W — fixed by PRESCRIPTIVE overlay (prose alone failed)

Descriptive prose was ignored; codegen emitted naive code (`subclass=row[3]` only,
`vals.extend(trailing)` per row) → no make/model + rule-count inflation (3714→5080). What
worked was giving the overlay a CONCRETE column-index recipe + explicit code shape:

- **Column layout** (Chola CV grid is fixed): `row[2]=PROD`, `row[3]=SUBCLASS`, `row[4]=MAKE`,
  `row[5]=MODEL`, `row[6:]=rates`. Telling codegen to read `row[4]`/`row[5]` made make/model
  land (GCV 2_UPTO_3.5T: make=`TATA/Maruti/Mahindra` + model list; MISC-D make too).
- **Merged 3W header**: emit trailing header values ONCE as separate `number_of_wheels=3W`
  rules, NEVER `vals.extend(trailing)` per data row. AND scope recovery to the GCCV table only
  (`product_type=="GCV"`) — the header template is copy-pasted into PCCV/MSV tables, so their
  trailing 3W values are stale duplicates that otherwise leak goods-3W into Buses/MISC-D.

Final verified (doc dbe5b4ea): GCV 452 (make 108, 3w 62/4w 390) · PCV 100 · Buses 160 ·
MISC-D 164 (make 54) · TW EV cc all (0,99999) · overlaps 0. Lesson: for structural codegen
misses, a column-index recipe + "generated-code shape" beats descriptive prose. See
[[pipeline_codegen_nondeterminism]] and [[pipeline_codegen_subclass_key_drop]].

## Round-3 fix — MISC-D body enumeration leaked into make/model (doc dbe5b4ea)

- **Symptom:** MISC-D `2_EXCAVATOR` (54 rules) had `vehicle_make == vehicle_model == ['Loader','Excavators/Earth Movers','Crane','Bulldozers/Bullgraders','Road Rollers', 'Fork Lift Trucks']`. Those are vehicle SUB-CLASSES, not a make/model split.
- **Root cause (source-verified via `IndexedContent.sheet_contents['July CV grid']` line 44):**
  the MSV `2_EXCAVATOR` row LITERALLY repeats the body enumeration in BOTH col[4]=MAKE and
  col[5]=MODEL. Codegen applied the generic CV column rule (row[4]→make, row[5]→model) so the
  bodies landed in make+model; the MSV override only moved them into `vehicle_sub_class`.
- **Fix (deterministic, Chola-gated, in `_transform_conditions`):** a CV rule whose
  `vehicle_make == vehicle_model` (identical non-empty lists) is never a genuine make×model
  grading — drop both. Exempt the `All Other Make/Models` sentinel (make==model by design) via
  `_SEGMENT... _SENTINEL_MAKE_MODEL_RE` so `_bifurcate_make_buckets` can still fan it out. Overlay
  `2_EXCAVATOR` note tightened to "make AND model UNSET". Tests: `MsvDistinctRulesTests`
  (`test_excavator_body_enumeration_not_leaked_into_make_model`, `test_real_make_model_split_preserved`).
- Verified post-fix: excavator make/model → None, sub_class intact; GCV 2_UPTO_3.5T fan-out
  unchanged (a=74 / \_XMODEL=48 / \_XMAKE=48 / plain=86).

## Round-4 — client override: blank "All Other Make/Models" rate → 0% synthesized bucket

Client (Rohit) decided a BLANK all-other rate should be a **0% deal**, not omitted. Fix in
`_bifurcate_make_buckets`: after the sentinel fan-out, a second pass synthesizes the missing
all-other pair at 0% for any explicit make/model group with NO source sentinel AND no existing
exclusion bucket (structural gate, Chola-commercial only — self-limits to graded cells, never
touches MISC-D since its make/model are nulled). Reuses `_clone_make_bucket` (new optional
`outcome` arg) + `_zero_outcome`. Idempotent via `covered_keys` (skip match-keys already carrying
`vehicle_make_exclude`/`vehicle_model_exclude`). Verified doc dbe5b4ea: all 36 non-electric ≤3.5T
(state×coverage) cells now `[a,b,c]`; +24 synthesized 0% buckets (12 pairs).

- **CRITICAL gotcha:** `_zero_outcome` MUST strip `qualifying_clauses` AND `other_conditions`
  (and deductions), keeping only `applied_on`. `materialize_cpa_split._split_rule` synthesizes the
  CPA-deduction from the free-text `other_conditions` footnote (not just structured clauses), so a
  preserved footnote makes the without-cpa variant `0% − 1.5% = −1.5%` → schema "Negative
  commission" CRITICAL. Stripped → 0% bucket stays single, `cpa=None` (applies to both).
- Duplicate-priority HIGH issues on the synth buckets are **pre-existing/expected** — the shipped
  sentinel fan-out already shares priority (`priority=rule.priority`); the doc has 1937 dup-priority
  issues total (540 GCV rules / 234 priorities), only 96 from make-buckets. Not a regression.
  Tests: `ZeroAllOtherSynthesisTests` in `test_make_model_exclusion.py`. Transformer-only → verify via
  `rerun_transform` (deterministic; applies cpa_split — see [[pipeline_rerun_transform_drops_cpa_split]]).

## Issue #2 verdict (ORIGINAL, now overridden by Round-4) — GCCV 4W (≤3.5T) was SOURCE-FAITHFUL

The `_bifurcate_make_buckets` fan-out works: per state×coverage cell → (a) TMM+models,
(b) `_XMODEL` (make=TMM, model_exclude=list), (c) `_XMAKE` (make_exclude=TMM). 24 cells carry
ONLY bucket (a) — those are exactly the cells where source line 5 (`All Other Make/Models`) has a
BLANK ACT rate (mostly third-party: TN/KA/WB/CG/NE/DL/HP/JK/UP/UK). Overlay skips blank/0% cells,
so no sentinel → no b/c bifurcation. This is the source grid's reality (TMM graded, all-other ACT
= no payout), NOT a pipeline defect. Reconciliation-baseline caution applies: confirm business
intent (blank = zero) before "fixing". See [[chola-reconciliation-baseline]] and
[[pipeline_rerun_transform_drops_cpa_split]].
