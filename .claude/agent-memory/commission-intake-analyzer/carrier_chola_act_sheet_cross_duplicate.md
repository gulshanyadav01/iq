# Chola — the ACT-only sheet re-prices deals the main CV sheet already carries

Intake `01f417c2-4997-4d5f-a931-3e7c7931f6b6`. Only visible AFTER seeding the
RTO masters — before that it was masked by the geography collapse (see
[[pipeline_unseeded_masters_masquerade_as_defects]]).

The email carries both `Act Only Policy-Changes` and ` CVApr26`. Both emit the
same GCV third-party deal under one `product_code`, with descriptions that give
the sheet away:

```
'Commercial Vehicle 40T_TO_43T BH Third Party'  (27.5)   <- Act Only sheet
'GCCV 8_40T_TO_43T BH ACT'                      (30.0)   <- CVApr26 sheet
```

18 unique overlap pairs on this document: **11 carry the SAME rate** (harmless
duplicate rows, still CRITICAL because conditions are identical) and **7 carry
DIFFERENT rates** (25.0 vs 27.5 / 30.0 — a real unloadable conflict).

The naming split is the reliable tell: the ACT-only sheet spells the product
`Commercial Vehicle <band> <state> Third Party`, the main sheet spells it
`GCCV <n>_<band> <state> ACT`.

## Which one wins?

Unresolved — needs the client. The ACT-only sheet is named
"Policy-**Changes**", which suggests it is an amendment/override of the main
grid rather than an independent grid, i.e. it should probably supersede. Do not
guess: a 2.5-point delta on third-party CV is real money.

If it is an override, `_merge_sheet_jsons` needs a precedence rule rather than
the plain concatenation it does today; if the sheets are meant to be disjoint,
the ACT rows should never have been emitted into the same product.
