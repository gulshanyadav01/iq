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
