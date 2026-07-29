# Client prod deal-dump: how to read it (and how it differs from our export)

Learned from `Chola_Dump_AMJJ.xlsx` (Apr-Jul 2026, 2237 deals) while reconciling
intake `917552e6-e15a-4773-a278-d7a9bf7ee3b8` (`PO Grid-Apr 26.eml`).

## The State column is decorative — RTO is the real key

Every CV row lists **all 35 states** in `State` and discriminates by the `RTO`
list. Deal 850725 (Goa RTOs) = 47%, 850727 (Tamil Nadu RTOs) = 30%, 850728
(Karnataka RTOs) = 25% — same 35-state `State` cell on all three. Comparing on
`State` therefore produces garbage; derive the effective state set by parsing
`XX01-City(StateName)(Group Y)` out of `RTO`.

Our `export-excel` is the mirror image: `State` is the real dimension and `RTO`
is blank. Any comparison has to normalise one onto the other.

## NCB Yes/No encodes the grid's NCB tier

Chola PC grids ship two parallel tables — base and "NCB GT 25%". The client
models this as the `NCB` column: **`NCB=Yes` ⇔ the NCB GT 25% grid**, `NCB=No` ⇔
base. Verified 6/6 on Jharkhand April (grid base 30/25/30, gt25 32.5/27.5/32.5;
dump deals 851152/851150/851154 and 851172/851170/851173 match exactly).

## The dump is a _delta_ load, not a full grid

Filter April as `DealStartDate <= 2026-04-30 AND (DealEndDate is null OR >= 2026-04-01)`
→ 298 rows. Only the `851xxx` block actually starts 2026-04-01; `795xxx`/`805xxx`
are carryovers from the March grid. April CAR is just **17 deals** against a grid
with ~562 populated cells, so "auto-only" slices are mostly the client not having
loaded that month rather than over-emission. **There is no Two Wheeler anywhere in
the dump** (all 2237 rows are Car/Taxi or Commercial Vehicle) — TW cannot be
reconciled against it at all.

## The dump is internally consistent; ours is not

With RTO in the key, the 298 April rows produce **298 distinct condition keys —
zero collisions**. Our April export produces 2159/3353 rows (64%) that share a
condition key with a differently-priced row. See
[[carrier_chola_april_pc_column_misalignment]] and
[[carrier_chola_april_tw_make_header_collapse]].

## Harness

`.context/cmp/` — `load.py` (both sides), `slices.py` (atomic expansion +
state canonicalisation), `pc_compare.py` / `cv_compare.py` (scorecards),
`state_fingerprint.py` (which grid column did each state's numbers come from),
`ambiguity.py` / `dump_check.py` (collision check + client-side control).
