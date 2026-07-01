# TODOS

## policy_extract

- [ ] **Declarative add-on registry** — replace the ~6-site-per-add-on pattern
  (schema, `AMOUNT_FIELDS`, `no_space_fields`, `default_zero_fields`,
  `ADDON_PAIRS`, and the stage1/stage2 prompt tables) with a single `ADDONS`
  table that derives them.
  **Priority:** P3
  **Context:** Surfaced during the addon-fields change (CEO review, branch
  `gulshanyadav01/check-addon-fields`). Each new add-on premium currently touches
  6+ locations kept in sync by hand — e.g. `ADDON_TOTAL_FIELDS` must mirror
  `default_zero_fields`. A declarative registry would make add-ons single-source
  and remove the drift hazard. Effort: human ~1 day / CC ~2h. Not blocking.

## Completed

- [x] **Extract CPA bifurcation into a pure transform over `cics_doc`** (eng F4).
  The row-split + percentage-deduction logic moved out of `export_excel` into
  `cins/processors/core/cpa_split.py` (`materialize_cpa_split`), wired into the
  transform pipeline so the canonical `cics_doc` carries the split and every
  consumer (export, API, validators, rule counts) sees it. Export is now a pure
  renderer. **Completed:** branch `ravipiplani/cpa-export-row-split` (2026-06-19).
