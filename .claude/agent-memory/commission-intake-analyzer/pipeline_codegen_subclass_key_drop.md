# Codegen subclass key-drop (silent dimension loss)

Pass 3 **codegen** path: the generated-code example in `pass3_rules_codegen_prompt.md`
(~line 241) stores grid dimensions under `conditions["subclass"]`, `["make"]`, `["model"]`.
But `raw_to_cics_transformer._transform_conditions` reads only
`entry_conditions.get("vehicle_sub_class")` (line 1139) and `get("vehicle_make")` (1164).
There is NO key remap (`RawCommissionEntry.conditions` is a passthrough `Dict[str,str]`).

Result: any dimension the codegen places under the short key is **silently dropped at the
key level** — not even normalized-to-null. Observed on "July Grid.eml" (doc `4e8bdc78`):
GCCV weight bands encoded as SUBCLASS row-label slugs `2_UPTO_3.5T` … `10_ABOVE_47T` were
lost → all 592 Commercial Vehicle rules collapsed to `gvw=(0,0)` sentinel, graded only by
`number_of_wheels`. The LLM path normalizes to null (a different failure); the codegen path
drops earlier.

Detection: source carries tonnage/CC tokens but rules carry the sentinel range
(`gvw=(0,0)`, `cc=(0,99999)`). `rule_overlap_checker` likely already flagged the resulting
duplicate rules as overlaps — signal present, ignored.

Fixes: (1) deterministic transformer GVW recovery that scans BOTH `subclass` and
`vehicle_sub_class` keys, gated on the `(0,0)` sentinel + `parse_gvw_range` shape-guard;
(2) fix the prompt example to use the canonical `vehicle_sub_class` key; (3) a
dimension-collapse warning in `schema_validator.py`. See [[pipeline_runserver_stale_code]].
