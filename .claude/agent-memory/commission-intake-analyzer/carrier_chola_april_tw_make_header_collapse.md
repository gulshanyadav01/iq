# Chola TW — sheet title consumed as VehicleMake, make×CC matrix destroyed

Intake `917552e6-e15a-4773-a278-d7a9bf7ee3b8`, sheet `PROP TW Apr-26`, 1839
exported Two Wheeler rules.

## What the source looks like

A merged title cell spans the whole grid, so the flattened header is
**three** segments, not two:

```
TW Make Grid for the month of Apr 26 > Hero OEM > 150cc
TW Make Grid for the month of Apr 26 > Hero OEM > SCOOTER
TW Make Grid for the month of Apr 26 > Hero OEM > 150_350cc
...
```

i.e. `<title> > <make> > <cc band>`. Rows are `| <state> | <type> | values… |`
where `<type>` is `NEW(100-500 NOP)` / `NEW(30-100 NOP)` / `NEW(UPTO 30 NOP)` /
`SOD` / `ANNUAL` / `ACT`.

## What the export contains

Pass-3 codegen took the **first** header segment as the make:

- `VehicleMake == "TW Make Grid for the month of Apr 26"` on **all 1839 rows**
- `CCStartRange=0, CCEndRange=99999` on all 1839 rows — the cc band segment is
  dropped entirely

So both discriminating dimensions of the grid are gone. 1839 rows collapse onto
**457 distinct condition keys**, and **1778 rows (96.7%)** share a key with a
differently-priced row — e.g. AP / NEW(100-500 NOP) / comprehensive / With CPA
carries six different rates (15, 17, 30, 34.5, 35, 40). The export is not
loadable: no importer can choose a rate.

The NOP slab _is_ captured (`PoliciesCountStartRange/EndRange`), and
`BusinessType=new` for the NEW(...) rows, so the row axis parses fine. The
failure is purely the column axis.

## Fix direction

Header parsing must take the **last two** `>` segments when the first is a merged
title spanning the sheet (a segment repeated on every column is a title, not a
dimension). Then `make` = segment −2, `cc band` = segment −1. Chola's
`prompts/carrier_overlays/chola_pass3.md` already documents the TW grid as
`<Make> > <band> > <value>` — it does not anticipate a title prefix.

Cannot be reconciled against the client dump: the prod dump has **zero** Two
Wheeler rows across Apr-Jul (see [[reference_client_deal_dump_semantics]]).

Related: [[pipeline_codegen_nondeterminism]] (TW `Make>CCband>value` flattening),
[[carrier_chola_pvtcar_act_mislabeled]] (TW ACT rows).
