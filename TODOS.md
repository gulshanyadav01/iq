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

- [ ] **UP-East / UP-West collapse to one `uttar-pradesh` slug** — the only
      residual Private Car ambiguity after the Chola April fixes (12 conflicting
      condition keys, all uttar-pradesh).
      **Priority:** P2
      **Context:** Chola PC grids carry TWO uttar-pradesh columns (`UP- East > …`
      and the main `UP > …`) at different rates — genuine RTO sub-zones. Change D
      made the parser take the state segment verbatim (so `UP- East` no longer
      surfaces as the bogus token `East`), but both segments then expand to the same
      `uttar-pradesh` slug via `_expand_state_names`, so the two columns collide on
      one condition key with two rates. Needs an East/West RTO-zone dimension
      (`conditions["rto"]` cluster) to disambiguate, or a state-variant slug. The
      state-column-alignment validator correctly does NOT false-positive here (both
      columns are uttar-pradesh's own, so membership passes). Out of scope for the
      five-defect batch.

## Completed

- [x] **Extract CPA bifurcation into a pure transform over `cics_doc`** (eng F4).
      The row-split + percentage-deduction logic moved out of `export_excel` into
      `cins/processors/core/cpa_split.py` (`materialize_cpa_split`), wired into the
      transform pipeline so the canonical `cics_doc` carries the split and every
      consumer (export, API, validators, rule counts) sees it. Export is now a pure
      renderer. **Completed:** branch `ravipiplani/cpa-export-row-split` (2026-06-19).
