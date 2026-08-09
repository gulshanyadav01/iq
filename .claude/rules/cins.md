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

| Path                       | Purpose                                                                                                                                                                                         |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `processors/core/`         | Pipeline orchestration (`pipeline.py`), LLM extraction (`llm_extract.py`), codegen executor (`code_gen_executor.py`), raw→CICS transformer, sheet-visibility predicates (`sheet_visibility.py`) |
| `processors/extractors/`   | File-type handlers: PDF, EML, Excel→CSV, sheet classifier                                                                                                                                       |
| `processors/content/`      | Content utilities: cleaning, CSV→Markdown, indexer, prompt preparation                                                                                                                          |
| `processors/llm_services/` | LLM client wrappers                                                                                                                                                                             |
| `prompts/`                 | Markdown prompt templates — one file per pass                                                                                                                                                   |
| `schema/`                  | CICS data model (`cics_model.py`), JSON schema, extraction pass definitions, raw extraction model                                                                                               |
| `validators/`              | Schema validator, rule overlap checker, raw extraction validator                                                                                                                                |

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
- **Hidden worksheets never feed extraction** — Excel tabs whose `sheet_state` is `hidden` / `veryHidden` are scratch pads and stale grids. They are still indexed and classified (the UI has to show what was skipped, and a hidden sheet classified `commission_data` is the signal that the wrong tab was hidden), but they never produce rules and never enter a Pass 1/2/3 prompt. Every consumer must derive its sheet sets from `processors/core/sheet_visibility.py` (`derive_sheet_sets`, `is_sheet_visible`, `hidden_sheet_names`, `visible_sheet_names`) — **never inline the visibility comparison**. The moment two call sites compute it differently, the pipeline routes a different sheet set than the one it pre-created `PENDING` `ExtractionPass` rows for, and the UI shows passes that never complete. The filter fails open: a classification entry with no `visibility` key (anything indexed before `INDEXER_VERSION` 1.4, plus `image::` pseudo-sheets) reads as visible. `CINS_SKIP_HIDDEN_SHEETS=false` restores pre-change behaviour without a redeploy, and must keep lifting _every_ gate, including the `rerun-pass` API rejection — not just the routing filter. Contract is pinned by `cins/tests/test_hidden_sheets.py` and `commission_intake/tests/test_hidden_sheet_api.py`
- **Pass 0C table boundaries are not safe to trust verbatim** — the prompt's "Shared Header Rule" makes the LLM emit cumulative `end_row` values for stacked sub-tables under one shared column-header block (one Two-Wheeler grid produced 24,006 rules instead of ~3,000 before the fix). `detect_csv_tables` post-processes raw LLM boundaries through `_dedupe_overlapping_table_ranges`, which (a) reshapes cumulative siblings — same `(start_row, col band)`, monotonic `end_row` — to "shared header band + own data band" using `header_row_count` from `metadata.formatting.detected_header_rows`, and (b) clips classic overlaps where a later commission table's `start_row` falls inside the prior table's range. Only `table_type == 'commission'` is reshaped; `master` and `notes` are left intact. See `cins/tests/test_table_dedupe.py` for the contract.

- **A merged header labels its visible cells even when its top-left is hidden** — `excel_to_csv.expand_merged_cells` propagates a merged value to every VISIBLE cell of the range, not only ranges whose top-left cell is itself visible. Chola's `TW july` hides columns AX and BB, which are exactly the top-left cells of `AX4:AZ4 -> "Hero vs Bajaj"` and `BB4:BE4 -> "Bajaj"`; the old top-left guard dropped those makes from the visible sibling columns, so their rules carried no `vehicle_make` and two different SCOOTER columns collapsed onto byte-identical conditions with different payouts. A fully hidden range still contributes nothing — there is no visible cell to write to — so hidden scratch content cannot leak in. Contract in `cins/tests/test_merged_header_expansion.py`. Changing the indexed CSV shape means bumping `INDEXER_VERSION` (this was 1.6).
- **Side-by-side grids must be split on the column band** — a sheet can hold two independent grids in the SAME rows. Chola's Two-Wheeler sheet is 57 columns wide: columns 2-41 are one grid and 42-56 another, each with its own `State`/`Type` key columns and 125 data rows carrying both. Pass 0C reports it as ONE table, so the second grid's rows are attributed the first grid's state (`KL/LD` vs `KL` on the same row) and its make-group headers — which sit in blank label cells — read as continuations of the first. `detect_csv_tables` runs `_split_side_by_side_tables` after `_dedupe_overlapping_table_ranges` (row ranges settled first, so each block inherits corrected `data_ranges`). Detection is deliberately narrow: `table_type == 'commission'` only, the header row must repeat its **leading key signature** (the first two adjacent non-empty labels, compared on first word only — the real sheet spells the same column `State (RTO)` in one block and `State` in the other), and blocks must be separated by more than one column so a legitimately doubled label pair never triggers it. Only `start_col`/`end_col` are set; row ranges are never touched. Contract in `test_table_dedupe.py::SideBySideTableSplitTests`.

- **NCB is a numeric percentage band, and the boolean beside it is not vestigial** — `RuleConditions` carries `ncb_start_range`/`ncb_end_range` (0-100 inclusive) parsed from the grid's verbatim label by `_parse_ncb_range`, plus the legacy `ncb` boolean **derived** from that band. Four invariants, each of which has already bitten:

  - **Absent NCB is `(None, None)`, never the open `(0, 100)`** used by gvw/cc/seating. `rule_overlap_checker._is_range_constrained`/`_constrained_dims` and `_has_conditions` all key off null-ness, so an open sentinel makes `ncb` a constrained dimension on _every_ rule of _every_ document and shifts the `set(overlapping_dims) == all_dims` complete-overlap verdict globally.
  - **`ncb_start_range`/`ncb_end_range` belong in `_DEDUPE_SCALAR_FIELDS`, NOT `_DEDUPE_RANGES`.** They must join the de-overlap **group key** (or a three-tier ladder — bands `25-45` and `45+`, both `ncb=True` — shares one key and the CC boundary walk fuses two distinct tiers' ladders), but must never enter the walk itself, which would rewrite bands parsed verbatim from the grid. A two-tier ladder is separated by the boolean alone, which is why the boolean stays.
  - **The band and the boolean share the dimension label `"ncb"`** in `_RANGE_KEY_TO_LABEL`. `_constrained_dims` builds a set compared against `overlapping_dims`; a separate label lets a rule register two dims while only one is recorded, silently disabling the duplicate check.
  - **The bare-number parser arm requires a trailing `%` and declines on a trailing word.** The parser runs against `_segment_probe` for every rule of every carrier, and its failure mode is worse than a miss: `"NCB 25% AND ABOVE"` hitting the bare arm yields the point band `(25, 25)`, which is well-formed, in range, passes every validator, and matches almost no policy. Postfix operators are first-class arms for the same reason (cf. `_parse_range_field`, which needs the qualifier _before_ the number and silently sentinels `"6 AND ABOVE"`).

  - **`_MIRROR_DIM_KEYS` in `cpa_split.py` is the TENTH sync site, and the only one that deletes.** It enumerates a rule's coverage-independent grid slot and feeds `_mirror_signature` -> `drop_mirrored_third_party`, which removes rules outright. An NCB ladder's base tier and its marked twin are identical in every other dimension — that is what a tier ladder _is_ — so omitting the band collapses them onto one signature and lets a third-party rule mirror a comprehensive rule it does not duplicate. Measured on a real Chola document: 101 signature groups masked more than one distinct band. Adding a dimension here can only make the drop fire _less_ often, which is the conservative direction the function documents for itself.

  Contract pinned by `cins/tests/test_ncb_tier.py`, `test_overlap_exclusion.py::NcbBandOverlapTests`, `test_cpa_split.py::MirrorSignatureNcbTests` and `commission_intake/tests/test_export_column_alignment.py`. Changing any of it means bumping `CICS_PIPELINE_VERSION`.

- **Editing ANY file under `cins/prompts/` invalidates the whole cross-carrier CICS cache** — the cache key binds `prompt_version_hash`, a SHA256 of the entire prompt corpus (`commission_intake/cics_cache.py:8-22`), so a one-word prompt edit forces a full codegen re-roll of every cached document for every carrier, independently of `CICS_PIPELINE_VERSION`. That cache exists precisely so "a re-extraction does not re-roll the codegen dice (and cannot regress a good result into a segment-dropping one)". State the blast radius in the PR and confirm `CINS_REGRESSION_GUARD` is enabled before merging a prompt change.

## When changing Pass 0C

Before touching `pass0c_table_detection_prompt.md`, `content_cleaning.detect_csv_tables`, or `indexer.py`'s table-split block, read the rationale at the top of `_dedupe_overlapping_table_ranges` **and `_split_side_by_side_tables`**. Any change that

- alters the meaning of `start_row` / `end_row` in the response schema,
- removes the column-band guard (`start_col` / `end_col`),
- drops the `table_type` filter,
- loosens the side-by-side split's key-signature or separation guard,

must come with a new fixture in `test_table_dedupe.py` and a documented rationale here. The dedupe pass is load-bearing: a regression silently inflates rule counts by 5-10x without surfacing any validation error.
