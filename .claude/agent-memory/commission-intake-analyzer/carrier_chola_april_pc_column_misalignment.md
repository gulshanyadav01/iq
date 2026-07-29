# Chola April PC — rates sourced from the wrong state column + NCB tier collapsed

Intake `917552e6-e15a-4773-a278-d7a9bf7ee3b8` (`PO Grid-Apr 26.eml`), CICSResult
`8cf37d63`, sheet `Private car Apr-26`. 467 exported Private Car rules.

## Ground truth is triangulated

The grid and the client dump agree exactly on Jharkhand (6/6 comprehensive
cells), so where the automation disagrees with **both**, it is the automation.

Grid layout: 21 state groups × `<GRP> > ACT | <GRP> > PACK`, 4 tables —
T1 PACK base, T2 SOD `[P]`/`[D]` base, T3 PACK "NCB GT 25%", T4 SOD "NCB GT 25%".
Expanded = **562 cells** (state × coverage × cc band × fuel × NCB tier).

## Scorecard vs the grid

| outcome                          | cells | share |
| -------------------------------- | ----- | ----- |
| reproduced with the right number | 183   | 32.6% |
| rule emitted, number wrong       | 123   | 21.9% |
| no rule emitted at all           | 256   | 45.6% |

By coverage: comprehensive 50/168 right, saod 123/312, third-party 10/82.

## Defect 1 — codegen reads the wrong column under the right state label

`state_fingerprint.py` scores each exported state's value-vector against every
grid column. 14 states match a _different_ column better than their own:

- `jharkhand` self 55% → matches **gujarat** 100%
- `chattisgarh` self 25% → matches **jharkhand** 100%
- `karnataka` self 57% → matches **tamil-nadu** 100%
- `uttarakhand` self 17% → **himachal-pradesh** 100%; `punjab` 33% → **west-bengal**;
  `uttar-pradesh` 47%; `orissa` 33%; `west-bengal` 62%; `delhi-ncr` 69%; `andaman` 70%

Concretely, JH 1000-1500 PACK: grid 25 / 27.5, client 25 / 27.5, export **30 / 32.5**.
The rule is even _labelled_ `"Private Car 1000 TO 1500 CC JH PACK"` — the label is
right, the cell read is wrong. Not a shift (offsets differ per state), so it is a
mis-built column map in Pass-3 codegen, not an off-by-one.

Four grid states get **no PC rules at all**: bihar, himachal-pradesh, rajasthan,
tamil-nadu (`BH` resolves to an empty `state` list on 30 rules).

## Defect 2 — the "NCB GT 25%" tier is not a dimension

The export has an `NCB` column but writes `Yes,No` on all 467 rows, so the base
and NCB>25% tiers collapse onto one key: **250 of 467 rows (54%) share a condition
key with a differently-priced row.** The client models exactly this with `NCB=Yes/No`
(see [[reference_client_deal_dump_semantics]]), so it is expressible — the loss is ours.

## Defect 3 — `[ NCB GT 25%]` label corrupts the CC band

`UPTO 1000 CC[ NCB GT 25%]` → the CC parser grabs `1000` and `25` and emits
`cc_start_range=1000, cc_end_range=25` — an inverted, unmatchable band, on **82
rules**. The other two NCB>25% bands parse to the _same_ band as their base twins
(`1001-1500`, `1501-99999`), which is what produces the Defect-2 collisions.
Any CC-band parse must strip bracketed qualifiers before reading numbers.

Related: [[carrier_chola_pvtcar_act_mislabeled]], [[pipeline_codegen_nondeterminism]].
