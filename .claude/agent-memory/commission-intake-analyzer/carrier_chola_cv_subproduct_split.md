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
