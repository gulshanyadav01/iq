# Chola TW — `Bajaj` and `Hero vs Bajaj` columns still lose vehicle_make

Intake `01f417c2-4997-4d5f-a931-3e7c7931f6b6`, sheet `PROP TW Apr-26`, 3225 TW
rules. This is the **residual** of
[[carrier_chola_april_tw_make_header_collapse]], which is otherwise fixed.

## Current state — mostly good, two columns bad

2917 of 3225 TW rules now carry a correct `vehicle_make` (Hero OEM 524, TVS OEM
488, Honda 475, Suzuki OEM 378, Bajaj 352, Yamaha OEM 237, Others 205, All
Electric Make 162, RE OEM 96). The old "title eaten as make on all rows" failure
is gone.

**308 rules still have `vehicle_make = None`**, and they are exactly two column
groups:

| description token | rules |
| ----------------- | ----- |
| `Bajaj`           | ~160  |
| `Hero vs Bajaj`   | ~145  |

Plus 3 rules (×2 CPA variants = the 6 in the HIGH state finding) that still eat
the merged title: ` TW Make Grid for the month of Apr 26 > Honda > 150cc` —
here the title lands in the STATE slot, so `conditions.state` is None.

## It is a Pass-3 codegen defect, not a transformer one

Verified against the per-sheet `CICSResult.sheet_results[].json_data` (codegen
output, pre-transform): `vehicle_make` is **already None there**, while
`rule_description` correctly reads `AP NEW(100-500 NOP) Hero vs Bajaj > SCOOTER`.
So codegen read the header, wrote it into the description, and never mapped it
onto `conditions.vehicle_make`. `_split_composite_makes` cannot help — it only
splits a make that arrived.

These are the two merged headers whose top-left cells sit in HIDDEN columns AX
and BB (see [[carrier_chola_composite_make_cells]]). `expand_merged_cells`
propagates them to the visible cells now, so the CSV has them — the loss is
downstream of the indexer.

## Blast radius

52 of 70 CRITICAL `complete_overlap` pairs on this document. The rate gaps are
large enough that no importer can guess: `Hero vs Bajaj > SCOOTER` = 5.5% vs
`Bajaj > SCOOTER` = 37.0% on the same state/NOP/coverage key.

## Fix direction

Pass-3 carrier overlay (`prompts/carrier_overlays/chola_pass3.md`, see
[[pipeline_carrier_overlay_architecture]]): when a flattened header is
`<title> > <make> > <segment>`, the MIDDLE segment is the make and the LAST is
the cc band / body — never the first. Worth asserting the description and
`vehicle_make` agree, since the description already carries the right token and
makes a cheap post-hoc reconciliation possible.
