"""Tests for the deterministic codegen completeness gate.

Pins the contract from the "Making CINS codegen predictable" plan (Layer 1):

- rate-bearing rows are counted host-side from the raw CSV slice at index time;
- a whole-sheet or whole-table drop fails the gate (→ retry, then requires_review);
- header-only / declined / tiny / legacy-shape cases fail OPEN (never a false hard-fail).
"""

import os
import unittest
from unittest import mock

from cins.processors.core.codegen_coverage import (
    count_rate_bearing_rows,
    count_rate_bearing_rows_markdown,
    table_coverage_report,
)


class _Rule:
    """Minimal stand-in for an emitted RawCommissionEntry (has source_section)."""

    def __init__(self, source_section=""):
        self.source_section = source_section


def _shape(*tables):
    return {"tables": list(tables)}


def _commission_table(index, name, rate_cell_count, **extra):
    d = {
        "table_index": index,
        "table_name": name,
        "table_type": "commission",
        "rate_cell_count": rate_cell_count,
    }
    d.update(extra)
    return d


class CountRateBearingRowsTests(unittest.TestCase):
    def test_counts_only_rate_rows_after_header(self):
        csv = "Product,ACT,TP\nSedan,12%,5\nSUV,,\nHatch,7.5,3"
        # header row skipped; Sedan + Hatch carry rates, SUV blank → 2
        self.assertEqual(count_rate_bearing_rows(csv, header_row_count=1), 2)

    def test_compound_header_rows_skipped(self):
        csv = "A,B\nsub,sub2\nrow1,10\nrow2,20"
        self.assertEqual(count_rate_bearing_rows(csv, header_row_count=2), 2)

    def test_empty_and_malformed(self):
        self.assertEqual(count_rate_bearing_rows("", 1), 0)
        self.assertEqual(count_rate_bearing_rows("only,header", 1), 0)


class GateFailOpenTests(unittest.TestCase):
    """Absent / legacy / disabled shape must PASS (fail-open) — Eng E5 / T5."""

    def test_none_shape_passes(self):
        r = table_coverage_report(None, [_Rule("x")])
        self.assertTrue(r.passed)

    def test_empty_tables_passes(self):
        r = table_coverage_report({"tables": []}, [])
        self.assertTrue(r.passed)

    def test_legacy_table_without_rate_cell_count_passes(self):
        legacy = {
            "tables": [
                {"table_index": 1, "table_name": "X", "table_type": "commission"}
            ]
        }
        r = table_coverage_report(legacy, [])
        self.assertTrue(r.passed)

    def test_env_disable_passes(self):
        shape = _shape(_commission_table(1, "MSV", 10))
        with mock.patch.dict(os.environ, {"CINS_CODEGEN_GATE": "false"}):
            r = table_coverage_report(shape, [])  # would otherwise fail
        self.assertTrue(r.passed)


class GateWholeSheetDropTests(unittest.TestCase):
    def test_zero_emitted_with_rate_rows_fails(self):
        shape = _shape(_commission_table(1, "MSV", 12))
        r = table_coverage_report(shape, [])
        self.assertTrue(r.failed)
        self.assertIn("Table 1", r.gap_message)

    def test_full_coverage_passes(self):
        shape = _shape(_commission_table(1, "MSV", 4))
        rules = [_Rule("Table 1 MSV") for _ in range(4)]
        r = table_coverage_report(shape, rules)
        self.assertTrue(r.passed)


class GateTwoTableDropTests(unittest.TestCase):
    """Synthetic 2-table sheet where a program drops table 2 → gate must fail."""

    def test_dropped_second_table_fails_via_attribution(self):
        shape = _shape(
            _commission_table(1, "Private Car", 6),
            _commission_table(2, "Two Wheeler", 6),
        )
        # All emitted rules attribute to table 1; table 2 got nothing.
        rules = [_Rule("Table 1 Private Car") for _ in range(6)]
        r = table_coverage_report(shape, rules)
        self.assertTrue(r.failed)
        self.assertIn("Table 2", r.gap_message)

    def test_both_tables_covered_passes(self):
        shape = _shape(
            _commission_table(1, "Private Car", 6),
            _commission_table(2, "Two Wheeler", 6),
        )
        rules = [_Rule("Table 1 Private Car") for _ in range(6)] + [
            _Rule("Table 2 Two Wheeler") for _ in range(6)
        ]
        r = table_coverage_report(shape, rules)
        self.assertTrue(r.passed)

    def test_sheet_shortfall_fails_without_attribution(self):
        # source_section labels don't map to any table (fuzzy/renamed), but the
        # sheet-wide count collapsed far below expected → per-sheet floor catches it.
        shape = _shape(
            _commission_table(1, "Private Car", 10),
            _commission_table(2, "Two Wheeler", 10),
        )
        rules = [_Rule("mystery label") for _ in range(3)]  # 3 << 20 expected
        r = table_coverage_report(shape, rules)
        self.assertTrue(r.failed)


class GateNoFalseFailTests(unittest.TestCase):
    """Legitimate cases that must NOT hard-fail — Eng E1/E2."""

    def test_header_only_declined_table_passes(self):
        # Table with no rate rows (rate_cell_count 0) — declined/blank — must pass.
        shape = _shape(_commission_table(1, "Declined", 0))
        r = table_coverage_report(shape, [])
        self.assertTrue(r.passed)

    def test_tiny_table_below_min_not_hard_failed_on_partial(self):
        # 2 expected rate rows (< min_expected 4): a per-table partial shortfall
        # must not hard-fail; only the unambiguous 0-emitted case would.
        shape = _shape(
            _commission_table(1, "Main", 20),
            _commission_table(2, "Tiny", 2),
        )
        rules = [_Rule("Table 1 Main") for _ in range(20)]
        r = table_coverage_report(shape, rules)
        # Tiny table (2 < 4) is exempt from the per-table drop check; sheet total
        # 20/22 is well above the floor → passes.
        self.assertTrue(r.passed)

    def test_source_section_variant_still_credited(self):
        # Renamed/cased label variant should still attribute via name token.
        shape = _shape(_commission_table(3, "Miscellaneous D", 5))
        rules = [_Rule("table 3 - miscellaneous-d segment") for _ in range(5)]
        r = table_coverage_report(shape, rules)
        self.assertTrue(r.passed)


MD_GRID = """## Table 1: Private Car [type: commission]

| Product | Comp | TP |
| --- | --- | --- |
| Sedan | 20% | 15 |
| SUV | 22% | 16 |
| Hatch | 18% | 14 |
| GCV-3W | 25% | 12 |
"""


class MarkdownFloorTests(unittest.TestCase):
    """Universal per-sheet floor from pass-3 markdown (single-grid sheets with no
    table_splits — the client-visible Chola email-attachment case)."""

    def test_counts_markdown_rate_rows(self):
        # 4 data rows carry rates; header + separator excluded.
        self.assertEqual(count_rate_bearing_rows_markdown(MD_GRID), 4)

    def test_whole_grid_drop_fails(self):
        r = table_coverage_report(None, [], markdown=MD_GRID)
        self.assertTrue(r.failed)
        self.assertIn("dropped the whole grid", r.gap_message)

    def test_full_coverage_passes(self):
        rules = [_Rule("Private Car") for _ in range(4)]
        r = table_coverage_report(None, rules, markdown=MD_GRID)
        self.assertTrue(r.passed)

    def test_segment_shortfall_fails(self):
        # Emitted collapses to 1 of 4 expected rows → below floor.
        r = table_coverage_report(None, [_Rule("x")], markdown=MD_GRID)
        self.assertTrue(r.failed)

    def test_no_markdown_and_no_shape_fails_open(self):
        r = table_coverage_report(None, [], markdown=None)
        self.assertTrue(r.passed)

    def test_text_only_markdown_passes(self):
        md = "## Notes\n\nsome prose, no rates here at all"
        r = table_coverage_report(None, [], markdown=md)
        self.assertTrue(r.passed)

    def test_shape_takes_precedence_over_markdown(self):
        # When split shape exists, per-table logic (not the md floor) drives.
        shape = _shape(_commission_table(1, "PC", 4))
        rules = [_Rule("Table 1 PC") for _ in range(4)]
        r = table_coverage_report(shape, rules, markdown=MD_GRID)
        self.assertTrue(r.passed)


class TableShapeLookupTests(unittest.TestCase):
    """Pipeline helper that maps a sheet to its persisted table shape."""

    def test_hit_by_safe_name(self):
        from cins.processors.core.pipeline import _table_shape_for_sheet

        splits = {"Grid": {"original_sheet_name": "Grid", "tables": [{"x": 1}]}}
        got = _table_shape_for_sheet(splits, "Grid", "Grid")
        self.assertIsNotNone(got)
        self.assertEqual(got["tables"], [{"x": 1}])

    def test_fallback_by_original_sheet_name(self):
        from cins.processors.core.pipeline import _table_shape_for_sheet

        # Long sheet name → indexer key (100-char sanitizer) differs from the
        # pipeline's 50-char safe_name; fall back to original_sheet_name match.
        long_name = "A" * 80
        indexer_key = long_name  # stored under the full sanitized name
        splits = {indexer_key: {"original_sheet_name": long_name, "tables": [{"y": 2}]}}
        safe_name = long_name[:50]
        got = _table_shape_for_sheet(splits, long_name, safe_name)
        self.assertIsNotNone(got)
        self.assertEqual(got["tables"], [{"y": 2}])

    def test_hit_by_indexer_key(self):
        """The exact match: table_splits keys are written by the INDEXER's sanitizer."""
        from cins.processors.content.indexer import sanitize_sheet_key
        from cins.processors.core.pipeline import (
            _table_shape_for_sheet,
            get_safe_sheet_name,
        )

        name = "Some_Workbook_Name_Long_Enough_To_Exceed_The_Pipeline_Prefix__CV June"
        splits = {
            sanitize_sheet_key(name): {
                "original_sheet_name": name,
                "tables": [{"z": 3}],
            }
        }
        got = _table_shape_for_sheet(splits, name, get_safe_sheet_name(name))
        self.assertIsNotNone(got, "indexer-keyed shape must resolve")
        self.assertEqual(got["tables"], [{"z": 3}])

    def test_attachment_prefixed_sheet_resolves(self):
        """Multi-attachment emails key on the PREFIXED name but store it BARE.

        The indexer builds the ``table_splits`` key from ``{prefix}{sheet_name}``
        (indexer.py:1199) while writing ``original_sheet_name`` as the bare sheet
        name (indexer.py:1309). The pipeline routes the prefixed name, so a
        fallback comparing only against the full name never matched and the gate
        lost its index-time witness — silently, because a None shape fails open.
        """
        from cins.processors.core.pipeline import (
            _table_shape_for_sheet,
            get_safe_sheet_name,
        )

        bare = " CV June 26"
        prefixed = f"Broker_june_26_Grid_for_Circulation_Retail_Broking_Final__{bare}"
        # Keyed by something neither sanitizer will reproduce, so only the
        # original_sheet_name fallback can resolve it.
        splits = {
            "some_other_key_shape": {
                "original_sheet_name": bare,
                "tables": [{"w": 4}],
            }
        }
        got = _table_shape_for_sheet(splits, prefixed, get_safe_sheet_name(prefixed))
        self.assertIsNotNone(
            got, "a prefixed sheet must still find its bare-stored shape"
        )
        self.assertEqual(got["tables"], [{"w": 4}])

    def test_ambiguous_original_sheet_name_fails_open(self):
        """Two attachments can each hold a sheet with the same bare name.

        First-match-wins would bind one workbook's shape to the other's sheet, and
        a WRONG shape is worse than none: it feeds the coverage gate and the Pass-3
        empty-result guard. Ambiguity must resolve to None.
        """
        from cins.processors.core.pipeline import (
            _table_shape_for_sheet,
            get_safe_sheet_name,
        )

        bare = "Commission Grid"
        prefixed = f"WorkbookA__{bare}"
        splits = {
            "key_a": {"original_sheet_name": bare, "tables": [{"from": "A"}]},
            "key_b": {"original_sheet_name": bare, "tables": [{"from": "B"}]},
        }
        got = _table_shape_for_sheet(splits, prefixed, get_safe_sheet_name(prefixed))
        self.assertIsNone(got, "ambiguous match must fail open, not pick one")

    def test_sheet_name_containing_double_underscore_recovers_correctly(self):
        """The indexer joins {stem}__{sheet}, so the bare name is after the FIRST '__'.

        A sheet legitimately named "Rate__Card" inside attachment "Book" is stored
        as "Book__Rate__Card"; recovering it means splitting once from the left.
        """
        from cins.processors.core.pipeline import (
            _table_shape_for_sheet,
            get_safe_sheet_name,
        )

        splits = {"k": {"original_sheet_name": "Rate__Card", "tables": [{"ok": True}]}}
        joined = "Book__Rate__Card"
        got = _table_shape_for_sheet(splits, joined, get_safe_sheet_name(joined))
        self.assertIsNotNone(got, "bare name must be recovered as 'Rate__Card'")
        self.assertEqual(got["tables"], [{"ok": True}])

    def test_miss_returns_none(self):
        from cins.processors.core.pipeline import _table_shape_for_sheet

        self.assertIsNone(_table_shape_for_sheet({}, "Grid", "Grid"))
        self.assertIsNone(_table_shape_for_sheet(None, "Grid", "Grid"))


if __name__ == "__main__":
    unittest.main()
