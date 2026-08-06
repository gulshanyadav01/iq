"""
`ExcelToCSVConverter.expand_merged_cells` and hidden rows/columns.

A merged group header routinely starts in a hidden column while its siblings
stay visible. Chola's `TW july` sheet hides columns AX and BB — which are
exactly the top-left cells of::

    AX4:AZ4 -> 'Hero vs Bajaj'
    BB4:BE4 -> 'Bajaj'

The original guard skipped any merged range whose top-left cell was hidden, so
the make was dropped from the VISIBLE sibling columns (AY, AZ, BC, BD, BE) too.
Rules built from them carried no `vehicle_make`, and two different SCOOTER
columns collapsed onto byte-identical conditions with different payouts — the 54
CRITICAL duplicate findings on doc 733711bd (scooter/TS/NEW(100-500) at both 5%
and 37%).

A range that is entirely hidden still contributes nothing: there is no visible
cell to write to, so hidden scratch content cannot leak in.

Run from the backend container:
    python manage.py test cins.tests.test_merged_header_expansion -v 2
"""

from unittest import TestCase

import openpyxl

from cins.processors.extractors.excel_to_csv import ExcelToCSVConverter


def _sheet(values: dict, merges: list, hidden_cols=(), hidden_rows=()):
    wb = openpyxl.Workbook()
    ws = wb.active
    for (row, col), value in values.items():
        ws.cell(row, col, value)
    for rng in merges:
        ws.merge_cells(rng)
    for letter in hidden_cols:
        ws.column_dimensions[letter].hidden = True
    for row in hidden_rows:
        ws.row_dimensions[row].hidden = True
    return ws


class MergedHeaderExpansionTests(TestCase):
    def setUp(self):
        self.c = ExcelToCSVConverter()

    def _expand(self, ws):
        visible_rows, visible_cols = self.c.get_visible_ranges(ws)
        grid = self.c.expand_merged_cells(
            ws, self.c.detect_merged_ranges(ws), visible_rows, visible_cols
        )
        return grid, visible_cols

    def test_hidden_top_left_still_labels_visible_siblings(self):
        # A1:C1 = "Bajaj" with column A hidden — B and C must keep the make.
        ws = _sheet(
            {(1, 1): "Bajaj", (2, 1): "150cc", (2, 2): "SCOOTER", (2, 3): "350cc"},
            ["A1:C1"],
            hidden_cols=("A",),
        )

        grid, visible_cols = self._expand(ws)

        self.assertNotIn(1, visible_cols)
        self.assertEqual(grid.get((1, 2)), "Bajaj")
        self.assertEqual(grid.get((1, 3)), "Bajaj")

    def test_visible_top_left_unchanged(self):
        ws = _sheet({(1, 1): "Hero OEM"}, ["A1:D1"])

        grid, _ = self._expand(ws)

        for col in (1, 2, 3, 4):
            self.assertEqual(grid.get((1, col)), "Hero OEM")

    def test_fully_hidden_range_contributes_nothing(self):
        ws = _sheet(
            {(1, 1): "Scratch", (1, 3): "Real"},
            ["A1:B1"],
            hidden_cols=("A", "B"),
        )

        grid, visible_cols = self._expand(ws)

        self.assertEqual(visible_cols, [3])
        self.assertNotIn((1, 1), grid)
        self.assertNotIn((1, 2), grid)
        self.assertEqual(grid.get((1, 3)), "Real")

    def test_hidden_row_top_left_still_labels_visible_rows(self):
        ws = _sheet({(1, 1): "Group"}, ["A1:A3"], hidden_rows=(1,))

        grid, _ = self._expand(ws)

        self.assertEqual(grid.get((2, 1)), "Group")
        self.assertEqual(grid.get((3, 1)), "Group")
        self.assertNotIn((1, 1), grid)

    def test_hidden_cells_are_never_emitted(self):
        ws = _sheet({(1, 1): "Bajaj"}, ["A1:C1"], hidden_cols=("A",))

        grid, _ = self._expand(ws)

        self.assertNotIn((1, 1), grid)

    def test_two_adjacent_groups_stay_distinct(self):
        # The real shape: two make groups, each with a hidden leading column.
        ws = _sheet(
            {
                (1, 1): "Hero vs Bajaj",
                (1, 4): "Bajaj",
                (2, 2): "SCOOTER",
                (2, 5): "SCOOTER",
            },
            ["A1:C1", "D1:F1"],
            hidden_cols=("A", "D"),
        )

        grid, _ = self._expand(ws)

        self.assertEqual(grid.get((1, 2)), "Hero vs Bajaj")
        self.assertEqual(grid.get((1, 5)), "Bajaj")
        # Same band word, different make — this is what stops the two SCOOTER
        # columns collapsing into one rule.
        self.assertEqual(grid.get((2, 2)), grid.get((2, 5)))
        self.assertNotEqual(grid.get((1, 2)), grid.get((1, 5)))

    def test_unmerged_cells_read_through_normally(self):
        ws = _sheet({(1, 1): "A", (1, 2): "B"}, [])

        grid, _ = self._expand(ws)

        self.assertEqual(grid.get((1, 1)), "A")
        self.assertEqual(grid.get((1, 2)), "B")
