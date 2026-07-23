# Chola Private Car ACT column — MISLABELED as comprehensive (not dropped) + wrongly CPA-split

Doc `dbe5b4ea-ec97-4236-a08c-b553f9a68a21` ("July Grid.eml", Chola). Investigated
2026-07-23 against stored `CICSResult.bce511ed` (extraction 2026-07-15).

## Finding (definitive, from cics_doc)

Private Car has 374 `commission_rules`: policy_type `comprehensive` 152, `saod` 222,
`third-party` 0. But splitting `comprehensive` by the coverage token in
`rule_description` (the generated `rule_text = "{prod} {subclass} {state} {coverage}"`):

- **104 PACK + 48 ACT** — i.e. the ACT column cells ARE read and emitted, just tagged
  `policy_type=["comprehensive"]` instead of third-party. **ACT is NOT dropped; it is mislabeled.**

CPA split doubles OD/comprehensive rules (with-cpa + without-cpa):

- base pre-split: **PACK 52, ACT 24, SAOD 111** (each ×2 → 104 / 48 / 222).
- Source `sheet_contents['July pvt car']` Package grid = 21 states × `<state> > ACT | <state> > PACK`,
  3 plain CC bands (UPTO 1000 / 1000-1500 / ABOVE 1500). Non-blank ACT cells = **24** (= 24 base ACT rules).
  The `[P]`/`[D]` suffix rows are the SOD grids (their ACT sub-cols blank).

## Why the codegen does this

Generated parser builds col_map with the ACT coverage token (so rule_text says "ACT"),
but sets `coverage_type_text` to a comprehensive value for the whole Package grid — reinforced
by the overlay line `chola_pass3.md:62-63` "Only the Package grid is comprehensive". So
`POLICY_TYPE_MAPPINGS` never sees "ACT" (which would map act→third-party, line 89).

## Impact on the planned fix (chola_pass3.md ACT/PACK section)

Plan premise "ACT dropped entirely, comprehensive ≈152 unchanged" is **WRONG**. Correct
post-fix expectation (re-tag, not add):

- comprehensive **152 → 104** (drops by 48), third-party **0 → 24** (NOT 48 — TP/PA rules
  are never CPA-deducted, so they must drop out of the split), saod 222 unchanged.
- Total **374 → 350** (the 24 phantom without-cpa ACT dupes disappear — a correctness gain).
- Verification must assert comprehensive==104, third-party==24, saod==222, AND that no
  third-party rule carries a cpa split. Plan's "comprehensive unchanged / third-party>0"
  would false-flag a regression and miss the CPA double-count.

## Second bug: cpa_split doubles TP rules (fixed 2026-07-23)

After the prompt fix, re-run (CICSResult.b50c3633) emitted third-party 48 — all ACT-token,
but cpa dist {with-cpa:24, without-cpa:24}. `cpa_split._split_rule` splits ANY rule carrying a
conditional_deduction clause into with/without variants; for TP it correctly does NOT reduce the
value (rule_col is "tp", not od/net) but STILL emitted both variants → pure duplication (24→48).
Codegen keeps attaching the clause to ACT rows (prompt non-deterministic + `_cpa_clause_from_notes`
reads the footnote from `other_conditions`), so the prompt alone can't stop it. Deterministic fix:
guard at top of `_split_rule` — `policy_type` contains "third" → `return [rule]` (no split). TP/PA
is a statutory pass-through, never CPA-deducted. Existing test `test_third_party_splits_without_deduction`
encoded the BUG (asserted len==2) → corrected to `test_third_party_not_split` (len==1).
Note: `_run_validators` (pipeline.py:808) runs BEFORE `materialize_cpa_split` (817), so the
coverage-integrity Check B (TP-with-CPA) can't catch this in-pipeline — the deterministic cpa_split
guard is the real fix; Check B is post-split defense (CLI/manual).

**Corrected live expectation after BOTH fixes:** third-party = **24** (not split). comprehensive
base varies run-to-run (codegen non-determinism: saw 52 then 62 non-blank PACK cells) — do not
assert an exact comp count. saod 222.

## Backstop (row_coverage_validator can't catch this)

`row_coverage_validator` only checks `\d+_TOKEN` body-slug rows → skips CC-band PC rows;
and even a row→rule check wouldn't fire (the ACT rows DO produce rules, just mislabeled).
The right deterministic guard is a **coverage/column-integrity check**: "source grid has N
non-blank ACT column cells but 0 third-party rules → HIGH" (here N=24), plus "TP/third-party
rules must not be CPA-split". Both feasible against `sheet_contents`. See [[pipeline_rerun_transform_drops_cpa_split]].

Related: [[carrier_chola_net_grid_cpa]] (CPA-on-OD only bites OD-routed comprehensive),
[[pipeline_codegen_nondeterminism]], [[carrier_chola_cv_subproduct_split]].
