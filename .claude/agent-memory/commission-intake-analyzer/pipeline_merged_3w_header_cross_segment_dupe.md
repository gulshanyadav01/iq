# Merged GCCV-3W header leaks into all CV sub-products (cross-segment duplicate)

**Symptom:** After a Chola July-grid extraction, the SAME block of ~64 "GCCV 3W" rows
appears in EVERY commercial sub-product — GCV, PCV, Buses, MISC-D — inflating rule counts
(~192 phantom rows). In the export they read as duplicate deals. Observed on doc
`dbe5b4ea-ec97-4236-a08c-b553f9a68a21` (CICSResult 5c04971e): GCV/PCV/Buses/MISC-D each
carried 64 rules whose `rule_description` starts "GCCV 3W"; the MISC-D copies were
byte-identical (conditions+outcome) to the GCV copies, PCV/Buses copies same rows with
seating injected.

**Root cause:** Pass-3 CODEGEN adherence failure (non-deterministic). Chola's tables reuse
one copy-pasted header template, so the trailing `1_GCCV_3W` merged-header values also sit in
the PCCV<6 / PCCV>6 / MSV headers as STALE duplicates. `chola_pass3.md:154-158` instructs the
generated parser to recover the merged 3W header ONLY for the GCCV table
(`product_type=="GCV"`) and skip it for non-GCCV tables. This run's generated code ignored
that guard and emitted the GCCV-3W values into all four tables. See
[[carrier_chola_cv_subproduct_split]] and [[pipeline_codegen_nondeterminism]].

**NOT caused by** the transformer make/model-exclusion + taxi-routing + seating-recovery
changes (branch `ravipiplani/intake-segment-rules`). Those worked (48 `_XMODEL`/`_XMAKE`
buckets fanned). Segmentation is by `_cv_segment_label(rule_text)`; the transformer only
classifies what codegen emits. `source_section` was empty on ALL rules (codegen never
populates it — segment came from `rule_text` blob).

**Fixes (in order):** (1) re-run extraction — non-determinism may not repeat the leak;
(2) strengthen the `chola_pass3.md` "only recover merged 3W header for GCCV table" guard;
(3) optional deterministic transformer/validator guard: a GCCV-3W-signatured rule appearing
in a non-GCV segment is a stale-header dup — drop or flag. The row-coverage validator could
be extended to flag "same row block present in >1 segment".

**Separate pre-existing issue seen same doc:** same-conditions/different-% pairs across ALL
products incl. untouched Private Car (e.g. `MH/GA ACT` vs `MH/GA PACK` both tagged
`policy_type=comprehensive` → collide). ACT should map third-party. ~154 such extra rows.
Not the make/model change either.

**FIX SHIPPED (branch ravipiplani/intake-segment-rules):** deterministic guard in
`RawToCICSTransformer._transform_products` — an entry whose `rule_text` starts "gccv"
(case-insensitive) but segments into PCV/Buses/MISC-D is dropped BEFORE grouping (Chola-gated).
Entry-level so an all-leak segment never forms an empty Product (Product requires >=1 rule —
a real crash risk). Warns with dropped count. Tests in `test_cv_subproduct_and_electric.py`
(StaleGccvLeakGuardTests). Only affects FUTURE extractions — restart celery + re-run to clean
existing docs (bind mount doesn't hot-reload, see [[pipeline_runserver_stale_code]]).
