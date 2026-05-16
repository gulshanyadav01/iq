---
paths:
  - "backend/cins/**"
---

# CINS Rules

The `cins` app is a multi-pass LLM extraction pipeline for insurance commission documents (CINs/CICSs). It is the most complex app in the backend — understand the pipeline flow before making changes.

## Pipeline Overview

Extraction runs in sequential passes defined in `schema/extraction_passes.py`:

1. **Pass 0** — document cleaning & table detection (email, master data, table structure)
2. **Pass 1** — metadata extraction
3. **Pass 2** — master data + products extraction (LLM or codegen)
4. **Pass 3** — rules extraction (LLM or codegen)
5. **Validation** — schema validation + rule overlap checks

Entry point: `processors/core/pipeline.py`

## Structure

| Path | Purpose |
| --- | --- |
| `processors/core/` | Pipeline orchestration (`pipeline.py`), LLM extraction (`llm_extract.py`), codegen executor (`code_gen_executor.py`), raw→CICS transformer |
| `processors/extractors/` | File-type handlers: PDF, EML, Excel→CSV, sheet classifier |
| `processors/content/` | Content utilities: cleaning, CSV→Markdown, indexer, prompt preparation |
| `processors/llm_services/` | LLM client wrappers |
| `prompts/` | Markdown prompt templates — one file per pass |
| `schema/` | CICS data model (`cics_model.py`), JSON schema, extraction pass definitions, raw extraction model |
| `validators/` | Schema validator, rule overlap checker, raw extraction validator |

## Rules

- **Never skip passes** — each pass feeds the next; skipping corrupts downstream results
- **Prompts are versioned by filename** — do not rename prompt files without updating all references in `content/prepare_prompt.py` and `schema/extraction_passes.py`
- **Schema changes require migration**: updating `cics_model.py` or `commission_schema_v1.json` must be reflected in both the Pydantic models and the JSON schema file simultaneously
- **Codegen path vs LLM path** — passes 2 and 3 can run as LLM extraction or codegen; the executor in `code_gen_executor.py` sandboxes generated code — do not relax sandbox restrictions
- **Raw extraction model** (`schema/raw_extraction.py`) is an intermediate representation — do not persist it directly; always transform via `raw_to_cics_transformer.py`
- **Validators are non-optional** — always run `validators/validate.py` after transformation; never return unvalidated CICS output to callers
- **LLM calls are expensive** — add new passes only if strictly necessary; prefer extending an existing pass prompt over adding a new pass
- **Prompt templates use Jinja2** — keep logic minimal in templates; heavy pre-processing belongs in `content/prepare_prompt.py`
- **Sheet classification** (`extractors/sheet_classifier.py`) determines which sheets feed which passes — changes here affect the entire pipeline
- **Pass 0C table boundaries are not safe to trust verbatim** — the prompt's "Shared Header Rule" makes the LLM emit cumulative `end_row` values for stacked sub-tables under one shared column-header block (one Two-Wheeler grid produced 24,006 rules instead of ~3,000 before the fix). `detect_csv_tables` post-processes raw LLM boundaries through `_dedupe_overlapping_table_ranges`, which (a) reshapes cumulative siblings — same `(start_row, col band)`, monotonic `end_row` — to "shared header band + own data band" using `header_row_count` from `metadata.formatting.detected_header_rows`, and (b) clips classic overlaps where a later commission table's `start_row` falls inside the prior table's range. Only `table_type == 'commission'` is reshaped; `master` and `notes` are left intact. See `cins/tests/test_table_dedupe.py` for the contract.

## When changing Pass 0C

Before touching `pass0c_table_detection_prompt.md`, `content_cleaning.detect_csv_tables`, or `indexer.py`'s table-split block, read the rationale at the top of `_dedupe_overlapping_table_ranges`. Any change that

- alters the meaning of `start_row` / `end_row` in the response schema,
- removes the column-band guard (`start_col` / `end_col`),
- drops the `table_type` filter,

must come with a new fixture in `test_table_dedupe.py` and a documented rationale here. The dedupe pass is load-bearing: a regression silently inflates rule counts by 5-10x without surfacing any validation error.
