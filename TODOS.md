# TODOS

## policy_extract

- [ ] **Declarative add-on registry** — replace the ~6-site-per-add-on pattern
      (schema, `AMOUNT_FIELDS`, `no_space_fields`, `default_zero_fields`,
      `ADDON_PAIRS`, and the stage1/stage2 prompt tables) with a single `ADDONS`
      table that derives them.
      **Priority:** P3
      **Context:** Surfaced during the addon-fields change (CEO review, branch
      `gulshanyadav01/check-addon-fields`). Each new add-on premium currently touches
      6+ locations kept in sync by hand — e.g. `ADDON_TOTAL_FIELDS` must mirror
      `default_zero_fields`. A declarative registry would make add-ons single-source
      and remove the drift hazard. Effort: human ~1 day / CC ~2h. Not blocking.

## cins

- [ ] **Declarative `RuleConditions` field registry** — replace the ten
      hand-synced enumerations of the condition fields with one table that
      derives them.
      **Priority:** P2
      **Context:** Surfaced by the NCB% eng review (branch
      `ravipiplani/ncb-percent-rule-condition`). Adding ONE condition dimension
      required edits in nine independent places: `cics_model.py:26`,
      `commission_schema_v1.json` `$defs.RuleConditions`,
      `raw_to_cics_transformer.py` `_has_conditions:1680` and
      `_DEDUPE_LIST_FIELDS`/`_DEDUPE_SCALAR_FIELDS`/`_DEDUPE_RANGES:2469-2501`,
      `rule_overlap_checker.py` `single_fields:242` + `range_pairs:255` +
      `_RANGE_KEY_TO_LABEL:41`, `schema_validator.py:303-316,597`,
      `commission_document.py` header list `:759` AND positional cell list
      `:1009`, `useCommissionIntakeDetail.ts` `CONDITION_LABELS`, and
      `cpa_split.py` `_MIRROR_DIM_KEYS:199`. The reviews caught three that a
      careful reading had already missed — `_DEDUPE_SCALAR_FIELDS` (whose
      omission silently fused three-tier ladders), the export, whose
      header/cell lists had no length guard at all, and `_MIRROR_DIM_KEYS`,
      whose consumer DELETES rules. Same
      failure shape as the logged `cins-five-sheet-set-derivations` learning:
      "any change must touch all five or it half-works silently."
      **Depends on:** the NCB% branch landing first. Effort: human ~3d / CC ~4h.
      **Risk:** touches the highest-traffic files in `cins`; the export's
      positional header/cell pairing is genuinely awkward to derive. Do NOT ride
      this along with a feature change.

- [ ] **Generic notice dedupe/cap between the transformer and the UI** —
      stop per-rule notices burying the ones that matter.
      **Priority:** P3
      **Context:** Surfaced by the NCB% eng review. `raw_to_cics_transformer.py`
      has 14 `self.notices.append` sites, several inside per-rule loops, and
      `commission_intake/tasks.py:1151` copies `list(transformer.notices)`
      straight to the DB with no dedupe or cap — so a large grid emits hundreds
      of identical lines on the detail page. That is how people stop reading the
      notices panel, which is the primary channel for "the transformer did
      something surprising". The NCB branch aggregated its own two notices
      (count inside the loop, one summary line after, with the distinct band
      list); generalise that helper to the other 12 sites.
      **Depends on:** the NCB% aggregation landing first — it is the pattern to
      generalise. Effort: human ~1d / CC ~1h.

- [ ] **Third `SheetType.IGNORE` label + pre-LLM emptiness heuristic** — stop
      blank/instruction sheets injecting fabricated commission rules.
      **Priority:** P2
      **Context:** Surfaced by the hidden-sheets review (branch
      `ravipiplani/skip-hidden-excel-sheets`). `SheetType` has only
      `commission_data` / `master_data` (`sheet_classifier.py:59-62`),
      `_heuristic_classify` is hard-disabled, and five paths default to
      `COMMISSION_DATA` (`:228, :240, :296, :311, :320`) — as does the prompt itself
      (`:97`). A blank scratch tab or an instructions tab, **visible or hidden**,
      can therefore inject bogus rules. Hidden-ness is only a weak proxy for "not a
      grid"; this is the actual root cause. A non-LLM emptiness check (no numeric
      cells or <2 rows → skip) costs zero LLM calls. Effort: human ~2d / CC ~2h.

- [ ] **Persist `indexing_status` / `indexing_errors` to `IndexedContent`** —
      revive a dead partial-failure channel.
      **Priority:** P2
      **Context:** `indexing_status` is computed at `indexer.py:403-452` and then
      discarded: `commission_intake/tasks.py` copies only `classifications`,
      `table_splits` and `source_metadata` into `sheet_metadata`. A document that
      half-indexed looks identical to one that fully indexed. Depends on nothing.
      Effort: human ~2h / CC ~20min.

- [ ] **Retrospective hidden-sheet measurement script** — turn the premise
      behind the hidden-sheets change into evidence.
      **Priority:** P3
      **Context:** Read-only pass over retained `CommissionDocument.file` workbooks:
      count hidden sheets and how many were classified `commission_data`. Sizes the
      `SheetType.IGNORE` work above and tells us how often this actually happened.
      Effort: human ~2h / CC ~20min.

- [ ] **Per-sheet "include in extraction" override** — human-in-the-loop escape
      hatch for a deliberately-excluded sheet.
      **Priority:** P3
      **Context:** Deliberately deferred from the hidden-sheets branch: a one-click
      "process it anyway" invites laundering junk into production commission rules,
      so it needs a captured reason and a document-level audit entry. Build only if
      ops asks after living with the coverage strip. Blocked by: no `ExtractionPass`
      rows exist for skipped sheets, so `triggerRerunPass` needs a create path (and
      the `rerun-pass` endpoint now 400s for non-routed sheet names by design).
      Effort: human ~2d / CC ~3h.

- [ ] **`master_data` app enumerates `wb.sheetnames` unfiltered** — same hidden
      sheet hazard, different upload path.
      **Priority:** P3
      **Context:** `master_data/services/loader.py:111-113` and
      `master_data/views/upload.py:63` iterate every sheet including hidden ones.
      Out of scope for the commission-intake hidden-sheets change; noted so it does
      not drift.

- [ ] **Fuel-dimension overlap from asymmetric `_expand_fuel_group` tagging** —
      a fuel-less GCV/TP band exports as all five fuels while its logical sibling
      exports as four, so the same rows overlap on the fuel dimension.
      **Priority:** P2
      **Context:** Surfaced by the Chola April defect-8 fix (branch fixing
      overlapping GVW bands). Change B made the fuel-less 40000-43000 band bump to
      40001 so it no longer overlaps on GVW, but if those rules are the same logical
      ladder as their fuel-tagged (`petrol,diesel,cng,lpg`) siblings, the fuel-less
      ones still render as all five fuels (`commission_document.py:930-932`) — a
      fuel-dimension overlap in the same rows, caused by asymmetric tagging in
      `_expand_fuel_group` (`raw_to_cics_transformer.py:2141`). Deliberately
      left out of Change B.

- [ ] **`RuleOverlapChecker._sets_overlap` treats `None` fuel as disjoint** — a
      wildcard (unset) list should overlap every concrete list, but the checker
      returns `False` when either side is not a list.
      **Priority:** P2
      **Context:** `rule_overlap_checker.py:318-322` returns `False` when either
      operand is not a list, so a `None` (all-values) `fuel_type` is treated as
      disjoint from `['petrol']` — which is why nothing flagged the 7 fuel-less
      40000-43000 rules overlapping the fuel-tagged ladder. Change B fixed the
      boundary-normalization evidence with the same wildcard insight
      (`_boundary_keys_compatible`); the overlap checker still needs the analogous
      fix. Out of scope for Change B; recorded so it does not drift.

- [ ] **Backfill Chola RTO-zone master beyond MH/TN/UP-East/NCR** — the
      carrier-scoped zone master (`CarrierRTOZoneMaster`) only covers the
      Mumbai/Pune/Central MH/Chennai/UP-East/NCR zones present in the source
      `chola_rto_master_source.xlsx`.
      **Priority:** P3
      **Context:** Surfaced during the Chola RTO + product-hierarchy masters
      change (`ravipiplani/chola-masters-intake`). A Chola grid rule naming a
      zone outside this set (any other state's UW zone) surfaces the new
      "Chola zone label not found in master" warning instead of resolving —
      correctly visible, not silently dropped, but still unresolved. Needs the
      carrier's full RTO-zone data; extend `chola_rto_zone.csv` (or re-run the
      upload API against a fuller source workbook — both paths share
      `chola_parsers.py`) once available.

- [ ] **Lock down `MasterDataUploadViewSet` (the Master Mapper upload) behind
      authentication** — it is still `permission_classes = [AllowAny]`
      (`master_data/views/upload.py:37`), so an unauthenticated POST can rewrite
      the shared master tables (insurer, RTO, subclass, ...).
      **Priority:** P1
      **Context:** Surfaced during the carrier-masters review
      (`ravipiplani/chola-masters-intake`). The NEW carrier-scoped endpoint
      (`CarrierMasterUploadViewSet`) was set to `IsAuthenticated` on that branch;
      its older sibling was left as-is to keep the change scoped. Both should end
      up on the same posture — flip the sibling and confirm nothing internal
      depends on anonymous upload.

## Completed

- [x] **Extract CPA bifurcation into a pure transform over `cics_doc`** (eng F4).
      The row-split + percentage-deduction logic moved out of `export_excel` into
      `cins/processors/core/cpa_split.py` (`materialize_cpa_split`), wired into the
      transform pipeline so the canonical `cics_doc` carries the split and every
      consumer (export, API, validators, rule counts) sees it. Export is now a pure
      renderer. **Completed:** branch `ravipiplani/cpa-export-row-split` (2026-06-19).

- [x] **UP-East / UP-West collapse to one `uttar-pradesh` slug.** Closed via
      Chola RTO-zone expansion: `_expand_rto_codes` / `_expand_state_names` now
      resolve a UW-zone label (e.g. `UP- East`) to its own RTO codes via the
      seeded `CarrierRTOZoneMaster`, so the zone-tagged column gets an explicit
      `conditions.rto` and no longer collides on the bare `uttar-pradesh`
      condition key with the plain `UP` column. The export complement fill
      (`commission_document.py::rto_cell`) renders the plain `UP` column as
      "all UP RTOs minus the zone-tagged column's explicit codes" (UP-East's
      codes for `UP- East`, NCR's UP14/UP16 for `NCR`) instead of overlapping
      them, so no `up-west` slug needs to exist. **Completed:** branch
      `ravipiplani/chola-masters-intake` (2026-08-06).
