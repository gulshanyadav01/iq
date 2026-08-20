# Carrier-specific prompt overlays (Pass 3 codegen)

Carrier-specific extraction rules are kept OUT of the shared Pass-3 codegen base prompt to
avoid prompt bloat (large prompts degrade instruction-following / hallucinate). Instead:

- **Base prompt** `prompts/pass3_rules_codegen_prompt.md` is carrier-agnostic. Its
  `## CARRIER-SPECIFIC RULES` section is just a `{carrier_overlay}` placeholder.
- **Per-provider overlays** live in `prompts/carrier_overlays/<code>_pass3.md` where
  `<code>` is the lowercase canonical short code from `cins.schema.carriers.normalize_carrier`
  (e.g. `chola_pass3.md`).
- **Runtime injection** in `cins/processors/core/llm_extract.py`:
  `get_carrier_overlay(provider_name, pass_id)` loads the matching file (or `""`), and
  `_inject_carrier_overlay(prompt, provider_name)` substitutes the placeholder. Called in
  `_extract_pass3_via_codegen` with `metadata.provider_name`. Unknown/empty provider →
  placeholder replaced with `""` (clean, carrier-free prompt).

To add a carrier rule: create/extend `carrier_overlays/<code>_pass3.md` — no base-prompt
edits, no cross-carrier bleed. Prompt loaders are `@lru_cache`'d → restart
`celery_commission_intake` after editing any prompt or overlay (see
[[pipeline_codegen_nondeterminism]]).

Gotcha: the overlay only fires when Pass-1 resolves `provider_name`. If provider is empty
(common — Pass-1 LLM may not extract it), the overlay is dormant and only the generic base
patterns apply. Keep genuinely-portable structural hints (e.g. the "value-in-cell" detector)
in the base, carrier-specific _semantics_ in the overlay.
