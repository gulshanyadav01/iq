# `rerun_transform` dropped the CPA split (pipeline parity bug)

**Symptom:** After calling `commission_intake.tasks.rerun_transform(document_id)` to re-run only
the deterministic transformer (no LLM), the rebuilt CICSResult had EVERY product exactly halved
(GCV 516→258, PCV 154→77, Buses 126→63, MISC-D 164→82, Private Car 374→187, TW 2444→1222) and
`cpa == None` on all rules. The original full-extraction doc had `with-cpa` + `without-cpa`
variants (2× rules).

**Root cause:** the full pipeline (`cins/processors/core/pipeline.py`) runs
`materialize_cpa_split(cics_json)` IMMEDIATELY after `RawToCICSTransformer.transform()` (lines
~677/804/1192). CPA bifurcation is a POST-transform step, NOT part of the transformer. But
`rerun_transform` in `commission_intake/tasks.py` called only `.transform()` and saved the result
— silently omitting the CPA split. So the "re-transform" recovery action produced a degraded doc
missing the entire CPA dimension.

**Fix:** import `materialize_cpa_split` in `rerun_transform` and apply it to `cics_json` right
after `.transform()`, before `CICSResult.objects.update_or_create`. Verified: counts restored to
original, cpa distribution `with-cpa 1889 / without-cpa 1889` = 3778.

**Gotcha for analysis:** `rerun_transform` is the cheap/deterministic way to re-verify a
transformer-only fix (reuses stored Pass1/2/3 codegen from `ExtractionPass`, no LLM cost, no
codegen non-determinism) — BUT before this fix it did NOT reproduce the full pipeline. Running it
OVERWRITES the stored production CICSResult. If you use it to verify a fix, make sure it replays
every post-transform step the real pipeline does (currently just `materialize_cpa_split`), else
you ship a degraded doc. Restart celery + backend after code edits (bind mount, see
[[pipeline_runserver_stale_code]]). See [[carrier_chola_cv_subproduct_split]] and
[[carrier_chola_net_grid_cpa]].
