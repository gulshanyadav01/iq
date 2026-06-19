# Pass-3 codegen non-determinism + prompt cache

**Pass 2/3 run as codegen** (LLM writes a fresh Python extractor each run). The generated
code — and therefore the rule count — varies run-to-run for the same source. Reprocessing
a document can change counts with NO code change. When a count moves after a reprocess,
first suspect codegen variance, not the transformer (the transformer's collapse/dedupe/
bifurcation passes don't delete rules; recovery/bifurcation only add).

**Prompt templates are lru_cached.** `cins/processors/core/llm_extract.py` loads each
`prompts/*.md` via `@functools.lru_cache(maxsize=None)` — read once per process. After
editing a prompt you MUST restart `celery_commission_intake` (clears the cache) or the
reprocess silently uses the stale template. Symptom: prompt edit has zero effect on
regenerated code; the `codegen_code` in `commission_intake_extractionpass` is byte-identical
across runs.

**Chola Two-Wheeler flattened header.** The TW grid encodes columns as
`Make > CC-band > value` (e.g. `Hero OEM > 150cc > 0.425`, `Suzuki OEM > SCOOTER > 0.45`,
`All Electric Make > 7 KW Bike > 0.17`), with row-leading cells `State (RTO) > AP` and
`Type > NEW(100-500 NOP)`. The generic `State > Coverage` codegen pattern mis-reads this
(make->state, ccband->coverage) and drops make/cc/state → ~149 dimensionless rules. The
base prompt keeps only a generic "Value-in-cell (self-describing) headers" note (triggers
on a numeric-last `>`-part); the Chola make/cc/state mapping lives in the carrier overlay
`prompts/carrier_overlays/chola_pass3.md`. See [[pipeline_carrier_overlay_architecture]].

NOTE: for doc 4e8bdc78 the generic note alone produced correct TW output (~1275 rules, 94%
make) because `provider_name` was EMPTY (Pass-1 didn't resolve it) → the Chola overlay was
dormant. The overlay only sharpens extraction once provider resolves to Chola.
See [[pipeline_codegen_subclass_key_drop]], [[pipeline_runserver_stale_code]].
