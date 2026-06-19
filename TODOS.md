# TODOS

## Completed

- [x] **Extract CPA bifurcation into a pure transform over `cics_doc`** (eng F4).
  The row-split + percentage-deduction logic moved out of `export_excel` into
  `cins/processors/core/cpa_split.py` (`materialize_cpa_split`), wired into the
  transform pipeline so the canonical `cics_doc` carries the split and every
  consumer (export, API, validators, rule counts) sees it. Export is now a pure
  renderer. **Completed:** branch `ravipiplani/cpa-export-row-split` (2026-06-19).
