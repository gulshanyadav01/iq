# Chola (Cholamandalam MS) July grid — all-net + CPA-on-OD mismatch

Observed on document `4e8bdc78-c513-4c0c-99ca-72a4b29caf5d` (July Grid.eml,
1650 rules: Private Car 197, Commercial Vehicle 592, Two Wheeler 861).

## Facts

- **Every rule has `conditions.deal_parameter == "net"`** (all 1650). No OD/TP
  split rows in this grid. percentage_value range 1.0–52.5, mean ~24.7.
- All 197 Private Car rules carry the `conditional_deduction` clause
  `when.cpa == "without-cpa"`, `effect.deduction.applied_on == "OD Pay-out"`,
  value 1.5 (percentage). CV and TW rules do not.
- Only ~5 distinct qualifying-clause payloads across the whole doc (heavily
  templated): applied_on_override, conditional_deduction (the CPA one),
  mandatory_requirement, grid_alias.

## The trap (load-bearing for the CPA-split export feature)

The CPA-deduction clause says `applied_on: "OD Pay-out"`, but the grid's
commission column is **net**, not OD. So an export that only deducts from the
OD column when `applied_on == "OD Pay-out"` (the "safety guard") applies the
deduction to an EMPTY OD column → the Without-CPA row equals the With-CPA row,
i.e. a silent no-op, while the clause is also removed from Global Rules. The
1.5pt deduction is then lost entirely.

**Where the split lives:** materialized at TRANSFORM time, not export.
`cins.processors.core.cpa_split.materialize_cpa_split` runs in all three
`pipeline.py` output paths, so the persisted `cics_doc` carries both With/
Without-CPA rows (Chola July doc: 1650 -> 1847 rules) and the Excel export is a
pure renderer (one row per rule). Existing docs were backfilled by applying
`materialize_cpa_split` to the stored `CICSResult.cics_doc` once (idempotent).
If stored rule count != exported, the split leaked back into export — it must
stay in transform.

**Column derivation (Option B):** the commission column is NOT
`deal_parameter` (always net here). It derives from `applied_on`:

- `applied_on_override` (comprehensive -> "OD Cover Premium") => OD column.
- `applied_on_override` (third-party -> "TP/PA Premium") => TP column.
- else `outcome.applied_on` ("Net Premium") => net column.
  The CPA deduction (`applied_on = "OD Pay-out"`) then lands ONLY on the OD
  column: comprehensive PC rows get x -> x-1.5; third-party PC rows route to TP
  so the OD-basis deduction is a correct no-op (liability has no OD pay-out).
  See `derive_applied_on` / `applied_on_to_column` in
  `commission_intake/views/commission_document.py`.
