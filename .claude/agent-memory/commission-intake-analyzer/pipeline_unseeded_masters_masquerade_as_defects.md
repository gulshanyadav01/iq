# Unseeded RTO masters masquerade as document defects

Seen on intake `01f417c2-4997-4d5f-a931-3e7c7931f6b6` (Chola `PO Grid-Apr 26.eml`).

## The check to run FIRST, before analysing any State/RTO finding

```python
RTOMaster.objects.count()             # was 0
CarrierRTOZoneMaster.objects.count()  # was 0
```

Both empty in a freshly-reset local DB (`pnpm db:reset` does not seed them —
they are uploaded through the master-data upload endpoint). An empty master
produces findings that look exactly like extraction defects:

## What it fakes

1. **8 HIGH `State/RTO Presence` findings** — "RTO would export blank on N
   rule(s) for <product>: the rule's states carry no product-active RTOs in the
   master". With `RTOMaster` empty this fires for **every** product and every
   state, covering 100% of rules. Tell: `noRTO == total rules` on every single
   product, including ones with a well-resolved state. These are environment
   artifacts, not document defects.

2. **CRITICAL `complete_overlap` pairs whose only real difference is
   geography** — `TN-Chennai` vs `ROTN`, `UP- East` vs `UP`. The metro/remainder
   split is legal ONLY because the metro sibling carries an explicit
   `conditions.rto` seeded from `CarrierRTOZoneMaster` (see the "Rest of
   <state>" rule in `.claude/rules/cins.md`). With that table empty,
   `_carrier_zone_map` is built empty and stays inert, both rows collapse to the
   same state slug with `rto=None`, and the overlap checker correctly reports
   identical conditions at different rates. 18 of 70 conflicting pairs on this
   document were this.

## Why it is silent

`_expand_rto_codes` is the only place that warns
("Chola zone label not found in master") and it is **only called when the raw
entry has non-empty `rto_text`**. Chola puts `TN-Chennai` / `UP- East` in the
STATE column, so `_expand_state_names` handles them, `_expand_rto_codes` never
runs, and **zero zone warnings are emitted**. Do not read "no zone warnings" as
"zones resolved".

## Consequence for analysis

Never report a geography-flavoured overlap as an extraction defect without
checking the two counts above. Re-run the analysis on a seeded environment
before filing anything. Conversely, a geography overlap that survives a seeded
master IS real.

Related: [[reference_client_deal_dump_semantics]] (RTO is the real key in the
prod dump, State is decorative — which is why a blank RTO column is fatal
downstream), [[carrier_chola_april_pc_column_misalignment]].
