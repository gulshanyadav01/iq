# Chola composite make cells (`/`- and `vs`-joined)

Chola publishes SEVERAL makes in ONE grid MAKE cell:

- GCCV `2_UPTO_3.5T` → `"TATA/\nMaruti/\nMahindra"` (indexer flattens the newlines)
- TW sheet → a `"Hero vs Bajaj"` comparison column

`_parse_text_list` splits on commas only, so these landed in `vehicle_make` as one
opaque string (96 + 62 rules on doc `733711bd`). Any downstream match on `TATA` or
`Bajaj` missed the rule — the client reported it as "make and model distinctions still
remain" for GCCV 4W ≤3.5T.

**Fix.** `RawToCICSTransformer._split_composite_makes` splits `vehicle_make` and
`vehicle_make_exclude` on `/` and word-anchored `vs` / `vs.` / `v/s`. Two guards:

1. The `All Other Make/Models` sentinel keeps its slash — `_bifurcate_make_buckets`
   matches the intact phrase to fan the bucket into exclusion rules.
2. A split is rejected unless EVERY segment is non-empty and contains a letter, so a
   stray trailing `/` never yields a blank make.

**Ordering matters.** The split runs AFTER the Chola `make == model` body-row guard
(which relocates sub-class enumerations like `Loader, Excavators/Earth Movers, ...` into
`vehicle_sub_class`). That guard compares the two lists as parsed — splitting the make
side first breaks the equality and lets sub-class bodies survive as bogus makes.

Models are deliberately NOT split: a slash in a model cell is usually part of the
variant name. Contract in `cins/tests/test_composite_make_split.py`.

Related: [[carrier_chola_cv_subproduct_split]], [[carrier_chola_april_tw_make_header_collapse]].
