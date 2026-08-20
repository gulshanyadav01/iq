# Chola make/model has a SOURCE ceiling — most blanks are not defects

Measured on doc `01f417c2-4997-4d5f-a931-3e7c7931f6b6` (`PO Grid-Apr 26.eml`,
5520 rules): **make present on 56.5%, model on 2.5%**. Before treating either
as a pipeline defect, check which grid the rules came from — three of the four
sheets have no such column to extract.

## What each sheet actually carries (verified in `IndexedContent.sheet_contents`)

| Sheet                | Header shape                                         | Make?           | Model?           |
| -------------------- | ---------------------------------------------------- | --------------- | ---------------- |
| `Private car Apr-26` | `PROD \| SUBCLASS \| <state> > ACT/PACK`             | **no column**   | **no column**    |
| `PROP TW Apr-26`     | `... > <make> > <cc band>`                           | yes (in header) | **no dimension** |
| ` CVApr26`           | `PROD \| SUBCLASS \| MAKE \| MODEL \| <state> > ...` | yes             | yes              |

So the per-segment ceiling is: PC 0% / TW make-only / CV make+model but only on
the rows the grid actually grades (`2_UPTO_3.5T` — cf. the validated note in
[[carrier_chola_cv_subproduct_split]] that GCV make/model appears ONLY on
`gvw=(0,3500)`, 0 above 3.5T, 0 on PCV/Buses).

Observed per segment (make / model missing):

    Private Car  756/756  756/756     <- source-faithful, no columns
    Taxi          65/65    65/65      <- same PC sheet
    MISC-D       479/479  479/479     <- body enumeration nulled by design
    PCV          174/174  174/174     <- source-faithful
    Buses        126/126  126/126     <- source-faithful
    GCV          484/688  551/688     <- source-faithful (>3.5T not graded)
    Two Wheeler  315/3232 3232/3232   <- make gap is the ONLY real defect

## The one real defect: 315 TW rules

Split `cc=(151,350)` ×160 and open `(0,99999)` ×155 — the known `Bajaj` /
`Hero vs Bajaj` merged-header residual, see
[[carrier_chola_tw_vs_make_columns_residual]] (308 rules there, same cause).
TW model is 100% missing and always will be: the TW grid has no model axis.

## Why the mandatory check is MEDIUM and must stay MEDIUM

`CINS_MAKE_MODEL_MANDATORY` (default true) raises one MEDIUM per product per
field with counts. It CANNOT be HIGH: `cics_cache.should_cache` refuses to pin
any output carrying a CRITICAL/HIGH, so with 97.5% of rules lacking a model
every Chola document would be walled out of the cache and re-run full codegen
on every access, forever. Same trap documented for `StateRtoPresenceValidator`.

Separate flag from `CINS_CHOLA_MMV` on purpose: that one governs the MMV
master (canonicalize a spelling / flag an unknown one), this one an export
rule. Implemented in `cins/validators/chola_mmv_validator.py::_check_presence`,
which runs BEFORE the master-coverage guard because presence needs no seeded
data.

**Open product question, not a bug:** if the client's loader truly requires both
columns non-empty, the fix is export-side — synthesize a catch-all (`ALL`, or
the all-other bucket) for grids that do not grade on that axis. Extraction
cannot invent a dimension the grid does not have. Nobody has decided this yet.

## Bonus: unknown-value findings on the same doc are all legitimate

`Hero OEM`, `TVS OEM`, `Suzuki OEM`, `RE OEM`, `All Electric Make` — these are
OEM-programme labels in the TW header, not manufacturers, so they correctly
match nothing in `CarrierVehicleMMVMaster`. `Mahindra Jeeto` in the CV MODEL
cell is make-prefixed (master has `JEETO` under `MAHINDRA`). Neither is a
pipeline defect; both are worth a human glance, which is exactly what the
MEDIUM is for.
