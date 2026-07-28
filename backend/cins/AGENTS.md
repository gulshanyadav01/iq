# AGENTS.md — `backend/cins/`

CINS (Commission Intelligence Normalization Service) is a shared Python library — **not a Django app**. It has no `apps.py`, `models.py`, or `urls.py`. Django apps (`commission_intake`, `policy_extract`) import from it via `from cins.…`.

## What it does

Multi-pass LLM pipeline that converts heterogeneous insurance commission documents (Excel, PDF, email) into a normalized **CICS v1.0 JSON** (Commission Intelligence Canonical Schema).

## Data flow

```
Raw file → ContentIndexer → multi-pass LLM extraction → RawExtraction → RawToCICSTransformer → CICSDocument
```

- `ContentIndexer` extracts text from source files and caches indexed content
- LLM extraction runs **per-pass** (see schemas below), each with its own prompt + Pydantic schema
- `RawToCICSTransformer` does all deterministic work: ID generation, enum normalization, priority computation, state/RTO expansion, source trace generation

## Directory layout

| Path                                         | Purpose                                                                                                                                                                                                                             |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `processors/core/pipeline.py`                | Top-level `Pipeline` orchestrator. Entry point: `Pipeline(llm_provider="gemini").process("file.xlsx")`                                                                                                                              |
| `processors/core/llm_extract.py`             | LLM extraction logic per pass — builds prompts, calls LLM, parses structured output                                                                                                                                                 |
| `processors/core/raw_to_cics_transformer.py` | Deterministic RawExtraction → CICSDocument conversion (~1800 lines of mapping tables + normalization logic)                                                                                                                         |
| `processors/core/code_gen_executor.py`       | Sandboxed exec of LLM-generated Python as fallback for large docs. AST-validated, restricted builtins, threading timeout                                                                                                            |
| `processors/core/sheet_visibility.py`        | Single source of truth for which worksheets may take part in extraction. `derive_sheet_sets()` → `(commission, master, hidden)`; also `is_sheet_visible`, `hidden_sheet_names`, `visible_sheet_names`, `skip_hidden_sheets_enabled` |
| `processors/content/indexer.py`              | Stage 1: extracts and caches file content per sheet/attachment                                                                                                                                                                      |
| `processors/content/prepare_prompt.py`       | Loads prompt template + document content for LLM calls                                                                                                                                                                              |
| `processors/content/content_cleaning.py`     | Pass 0A/0B/0C preprocessing (email cleaning, master data cleaning, table detection)                                                                                                                                                 |
| `processors/content/csv_to_markdown.py`      | Converts CSV sheets to markdown for LLM context                                                                                                                                                                                     |
| `processors/extractors/`                     | Format-specific extractors: `excel_to_csv.py`, `extract_pdf.py`, `extract_eml.py`, `sheet_classifier.py`                                                                                                                            |
| `processors/llm_services/`                   | LLM provider abstraction. `factory.py` → `get_llm_service()` returns `GeminiService` or `OpenAIService`. Singleton pattern via `LLMServiceMixin`                                                                                    |
| `schema/extraction_passes.py`                | Pydantic models for each pass + `merge_passes()` that combines results into RawExtraction                                                                                                                                           |
| `schema/raw_extraction.py`                   | Flat Pydantic models for LLM output (`RawExtraction`, `RawCommissionEntry`). Free-text fields — transformer normalizes to enums                                                                                                     |
| `schema/cics_model.py`                       | Full CICS Pydantic models (`CICSDocument`, `CommissionRule`, `RuleConditions`, etc.) with strict enums                                                                                                                              |
| `schema/commission_schema_v1.json`           | JSON Schema (draft-07) for CICS v1.0 — used by validators                                                                                                                                                                           |
| `validators/`                                | `SchemaValidator` (6-tier validation), `RuleOverlapChecker`, `RawExtractionValidator`, CLI `validate.py`                                                                                                                            |
| `prompts/`                                   | Markdown prompt templates, one per extraction pass (`pass0a_…`, `pass1_…`, `pass2_…`, `pass3_…`)                                                                                                                                    |
| `PRODUCT_FIELD_MAPPING.md`                   | Which fields apply to which product type (Car / TW / CV)                                                                                                                                                                            |
| `PRODUCT_FIELD_VALUES.md`                    | Allowed enum values for all CICS fields (states, vehicle types, etc.)                                                                                                                                                               |

## Extraction passes

| Pass | Schema (in `extraction_passes.py`)                             | Purpose                                          |
| ---- | -------------------------------------------------------------- | ------------------------------------------------ |
| 0A   | `EmailCleaningExtraction`                                      | Clean email noise (optional, email sources only) |
| 0B   | `MasterDataCleaningExtraction`                                 | Normalize master data sections                   |
| 0C   | `CSVTableDetectionExtraction`                                  | Detect table boundaries in CSV sheets            |
| 1    | `MetadataExtraction`                                           | Provider, period, global settings                |
| 2    | `MasterDataExtraction` (aliased as `ProductCatalogExtraction`) | Reference data (RTO mappings, declined lists)    |
| 3    | `RulesExtraction`                                              | Products + commission rules                      |

Results from passes 1–3 are merged via `merge_passes()` into a `RawExtraction` dict.

## LLM provider selection

Controlled by `LLM_PROVIDER` env var (default: `"gemini"`). Options: `"gemini"`, `"openai"`. Always use the factory — `get_llm_service()` and `get_config_for_*()` from `processors/llm_services/factory.py`. Services are singletons (`LLMServiceMixin.get_instance()`).

## Key conventions

- **LLM schemas are intentionally flat and free-text** — `RawExtraction` uses string fields everywhere. The transformer converts to strict enums in `CICSDocument`. Never add enum constraints to `raw_extraction.py` schemas.
- **Prompts are markdown files** in `prompts/`, not embedded strings. If adding a new pass, create a `passN_*.md` file.
- **`ProductCatalogExtraction` is a backward-compat alias** for `MasterDataExtraction` (`extraction_passes.py:370`).
- **Large documents fall back to code-gen** — when input exceeds size thresholds (`llm_extract.py:30-36`), the pipeline asks the LLM to generate Python code instead of JSON, executed safely by `code_gen_executor.py`.
- **Hidden worksheets are indexed but never extracted** — `ExcelToCSVConverter` records each tab's `sheet_state`, the indexer stores it as `sheet_classifications[name]["visibility"]`, and every routing decision filters on it via `processors/core/sheet_visibility.py`. Hidden (`hidden` / `veryHidden`) tabs are still converted and classified so the UI can show what was skipped, but they produce no rules and never enter a Pass 1/2/3 prompt. Exclusion happens at the point of _use_, not at enumeration. Rollout switch: `CINS_SKIP_HIDDEN_SHEETS` (default `true`). Fails open — an entry with no `visibility` key (indexed before `INDEXER_VERSION` 1.4, or an `image::` pseudo-sheet) reads as visible.
- **Langfuse tracing** is integrated via `processors/llm_services/langfuse_client.py`. Controlled by `LANGFUSE_*` env vars.
- **Pass 0C output is not trusted verbatim** — the prompt's "Shared Header Rule" makes the LLM emit cumulative `end_row` values for stacked sub-tables under one shared column-header block. `detect_csv_tables` post-processes raw boundaries through `_dedupe_overlapping_table_ranges` to (a) reshape cumulative siblings into "header band + own data band" using `metadata.formatting.detected_header_rows`, and (b) clip classic overlaps. Only `table_type == 'commission'` is touched; `master` / `notes` are preserved. The dedupe pass is load-bearing: regressions silently inflate rule counts by 5–10× without surfacing any validation error. Contract is pinned by `cins/tests/test_table_dedupe.py`.

## Things an agent might get wrong

- `cins/` is not a Django app — do not add `apps.py`, register in `INSTALLED_APPS`, or add `models.py`.
- Do not edit `commission_schema_v1.json` without also updating `cics_model.py` — they must stay in sync.
- The transformer (`raw_to_cics_transformer.py`) contains ~1800 lines of mapping tables. When adding new enum values, update both the mapping dicts in the transformer **and** `PRODUCT_FIELD_VALUES.md`.
- The `raw_extraction.py` schemas deliberately avoid enums and nested objects. Constraints belong in `cics_model.py` only.
- Imports inside `cins/` use absolute paths (`from cins.processors.…`), not relative. Follow this pattern.
- Never inline a sheet-visibility check (`info.get("visibility") != "visible"` and friends). Import the helpers from `processors/core/sheet_visibility.py`. Two call sites computing the sheet set differently makes the pipeline route sheets it never created `PENDING` `ExtractionPass` rows for, and the UI hangs on passes that never complete.
- Bumping `INDEXER_VERSION` (`processors/content/indexer.py`) invalidates cached `IndexedContent` — documents are re-indexed on next access. Bump it whenever the shape of `sheet_metadata` changes, not just its contents.
