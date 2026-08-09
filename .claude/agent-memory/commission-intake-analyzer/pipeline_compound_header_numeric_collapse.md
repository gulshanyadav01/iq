# Compound-header numeric collapse drops swallowed data rows

`csv_to_markdown._build_compound_headers` collapses multi-row headers by suppressing a
cell that repeats the cell above it (correct for merged parent labels: `AP/TS` spanning
ACT and PACK). When Pass 0C swallows the first DATA rows into the header band, those
rows' rates ride along as trailing `>` segments — and the suppression silently ate a
rate whenever two consecutive rows published the SAME value.

**Symptom (doc `733711bd`, Chola July 2025 grid).** Sheet rows 6/7 are
`1_GCCV_3W` and `1_GCCV_3W[Electric]`, both merged into the 5-row header. Header cells
came out as:

- `MH/GA > PACK > 0.47 > 0.45` — rates differ → both segments survive
- `AP/TS > ACT > 0.325` — rates equal → **one** segment

Pass-3 codegen aligns the i-th trailing value to the i-th SUBCLASS segment, so the
electric twin existed for only **2 of 20 state groups** (4 rules vs the base row's 50).
Client reported it as "GCV 3W Electric deals missing".

**Fix (INDEXER_VERSION 1.6 → 1.7).** Skip vertical-repeat suppression for cells matching
`^[+-]?\d+(\.\d+)?\s*%?$`. A bare number is a value, never a merged parent label;
labels that merely contain digits (`150cc`, `1_GCCV_3W`, `UPTO 3.5T`, `7 KW Bike`) are
unaffected. Backstopped in `chola_pass3.md`: emit one rule per SUBCLASS segment always,
reusing the last available value when `len(vals) < n`. Contract in
`cins/tests/test_compound_header_numeric_repeat.py`.

**Diagnostic.** The collapse is visible without re-running extraction — read
`IndexedContent.sheet_contents[<sheet>]` and compare the trailing-segment count per
header cell against `n` sub-class segments in the `SUBCLASS > ...` cell. Any cell with
fewer is a dropped deal.

Related: [[pipeline_merged_3w_header_cross_segment_dupe]],
[[carrier_chola_cv_subproduct_split]], [[pipeline_codegen_nondeterminism]].
