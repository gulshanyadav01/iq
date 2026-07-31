# Commission period silently defaults to today (ingestion date)

**Symptom:** a doc's Period shows the processing month/year instead of the real
period (e.g. doc `bf78a7d7` = ICICI SCV HR1 email, shown "Jul 2026" but actually
Jul 2025). Tell: `commission_period.effective_from` equals the doc `created_at`
date exactly, `period_label` empty.

**Root cause (two layers):**

1. `raw_to_cics_transformer.py::_transform_period` (~line 653-656): when
   `_parse_date(raw.start_date)` returns None, it fabricates
   `effective_from = datetime.utcnow()`. `effective_from` is a REQUIRED field on
   `CommissionPeriod` (`cics_model.py:428`), so the transformer must supply
   something — and "today" is the worst choice: always wrong, looks
   authoritative, and now surfaces in the runs-list **Period** column.
2. Pass 1 didn't extract a date at all (`start_date=None`, `period_label=None`).
   Single-email grids (an RTO list + rates, no "w.e.f."/"valid till" text) have
   no written period, so the LLM left it empty even though the email `Date:`
   header (`Wed, 02 Jul 2025`) was in `primary_content`.

**Fixes applied:**
- `prompts/pass1_metadata_prompt.md` §3: added explicit fallback — when no
  period is stated in an email body, use the email's own `Date:` header as
  `start_date`; never use today/processing date. (lru_cached → **restart celery**
  to activate; see [[pipeline_codegen_nondeterminism]].)
- Patched the stored doc's `commission_period` to `2025-07-02` / `Jul 2025` from
  the email Date ground truth (was `2026-07-31`).

**Transformer fallback — DONE.** `_transform_period` now takes a `document_date`
and uses it before `datetime.utcnow()`. Order: extracted `start_date` →
`document_date` → today (last resort, warned). `document_date` comes from
`parse_document_date(content)` (module fn in `raw_to_cics_transformer.py`) which
reads the email `Date:` header (RFC 2822 → ISO). Computed in `extract_multi_pass`
from the Pass-1 content and passed to both `transform()` calls there.
- **Gap:** `rerun_pass3` (pipeline.py) re-transform path passes no `document_date`
  (email header not persisted in pass1/2/3 files) → still relies on Pass 1's own
  `start_date`, else today. Acceptable for that LLM-free re-run.
- Non-email docs (PDF/excel) have no `Date:` header → `document_date=None` →
  today last resort unchanged.
- Tests: `cins/tests/test_period_document_date_fallback.py`.
See [[architecture_email_primary_content]].
