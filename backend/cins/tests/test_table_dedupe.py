"""
Tests for `_dedupe_overlapping_table_ranges` in `cins.processors.content.content_cleaning`.

The Pass 0C "Shared Header Rule" makes the LLM emit cumulative `end_row`
values for stacked sub-tables that share one column-header block, which
explodes downstream rule counts (a single Two-Wheeler grid produced
24,006 rules instead of ~3,000 before this fix).

These tests exercise the dedupe helper directly so we don't need to hit
the LLM. Run from the backend container:

    python manage.py test cins.tests.test_table_dedupe -v 2
"""

from unittest import TestCase

from cins.processors.content.content_cleaning import (
    _dedupe_overlapping_table_ranges,
    _split_side_by_side_tables,
)


def _rec(
    idx: int,
    name: str,
    start_row: int,
    end_row: int,
    table_type: str = "commission",
    start_col: int | None = None,
    end_col: int | None = None,
) -> dict:
    return {
        "table_index": idx,
        "table_name": name,
        "table_type": table_type,
        "description": "",
        "confidence": 1.0,
        "detection_notes": "",
        "start_row": start_row,
        "end_row": end_row,
        "start_row_original": start_row,
        "end_row_original": end_row,
        "start_col": start_col,
        "end_col": end_col,
        "data_ranges": [[start_row, end_row]],
    }


class CumulativeSiblingTests(TestCase):
    """Case A: tables share start_row, monotonic end_row (Two-Wheeler bug)."""

    def test_cumulative_chain_reshapes_to_header_plus_own_data(self):
        # Shared column header on rows 4-6 (3 rows). Three state grids
        # stacked: AP rows 7-11, TS rows 12-16, TN rows 17-21. LLM emits
        # cumulative end_rows.
        records = [
            _rec(1, "AP OEM", 4, 11),
            _rec(2, "TS OEM", 4, 16),
            _rec(3, "TN OEM", 4, 21),
        ]

        note = _dedupe_overlapping_table_ranges(records, header_row_count=3)

        self.assertIn("cumulative", note)
        # First table untouched: header + AP data.
        self.assertEqual(records[0]["data_ranges"], [[4, 11]])
        # Second table: header rows 4-6 + own data 12-16. AP rows 7-11 dropped.
        self.assertEqual(records[1]["data_ranges"], [[4, 6], [12, 16]])
        # Third table: header + own data 17-21. AP+TS dropped.
        self.assertEqual(records[2]["data_ranges"], [[4, 6], [17, 21]])

    def test_no_header_count_disables_case_a(self):
        # Without `header_row_count` we can't reconstruct the shared header,
        # so cumulative reshape is skipped. Clip pass still runs but
        # cumulative siblings share start_row so it's also a no-op.
        records = [
            _rec(1, "AP OEM", 4, 11),
            _rec(2, "TS OEM", 4, 16),
        ]

        note = _dedupe_overlapping_table_ranges(records, header_row_count=0)

        self.assertEqual(note, "")
        self.assertEqual(records[0]["data_ranges"], [[4, 11]])
        self.assertEqual(records[1]["data_ranges"], [[4, 16]])

    def test_endrow_order_is_normalised_by_sort(self):
        # Even when the LLM emits cumulative siblings in any order, the
        # reshape sorts by end_row ascending: smallest range becomes the
        # anchor, larger ranges get their prefix replaced with header + own
        # data only.
        records = [
            _rec(1, "AP-larger", 4, 20),
            _rec(2, "TS-smaller", 4, 15),
        ]

        _dedupe_overlapping_table_ranges(records, header_row_count=3)

        # Smaller range (end_row=15) acts as anchor — unchanged.
        self.assertEqual(records[1]["data_ranges"], [[4, 15]])
        # Larger range loses the anchor's rows; keeps header + own data.
        self.assertEqual(records[0]["data_ranges"], [[4, 6], [16, 20]])


class SideBySideTablesTests(TestCase):
    """Case A guard: tables sharing start_row but disjoint column bands must
    NOT clip each other (e.g. AP OEM grid and AP Electric grid placed
    side-by-side under one column-header row)."""

    def test_side_by_side_tables_preserved(self):
        # Two tables, same row range, disjoint columns.
        records = [
            _rec(1, "AP OEM", 4, 11, start_col=1, end_col=20),
            _rec(2, "AP Electric", 4, 11, start_col=21, end_col=40),
        ]

        note = _dedupe_overlapping_table_ranges(records, header_row_count=3)

        # No reshape: disjoint column bands.
        self.assertEqual(note, "")
        self.assertEqual(records[0]["data_ranges"], [[4, 11]])
        self.assertEqual(records[1]["data_ranges"], [[4, 11]])


class PlainOverlapClipTests(TestCase):
    """Case B: classic Option-B clip — overlapping ranges with distinct
    start_rows. Used when tables are emitted with proper start_rows but the
    LLM still over-extends end_row past the next table's start."""

    def test_clip_end_row_to_next_start_minus_one(self):
        records = [
            _rec(1, "GCCV", 4, 50),  # over-extended past next table
            _rec(2, "PCCV", 30, 60),
        ]

        note = _dedupe_overlapping_table_ranges(records, header_row_count=0)

        self.assertIn("clip", note)
        self.assertEqual(records[0]["end_row"], 29)
        self.assertEqual(records[0]["data_ranges"], [[4, 29]])
        # Second table untouched.
        self.assertEqual(records[1]["end_row"], 60)


class TypeGuardTests(TestCase):
    """`master` and `notes` tables must never be reshaped — they hold
    lookup data and qualifying disclaimers respectively."""

    def test_notes_table_preserved(self):
        records = [
            _rec(1, "Commission", 4, 30),
            _rec(2, "Notes", 31, 50, table_type="notes"),
        ]

        _dedupe_overlapping_table_ranges(records, header_row_count=3)

        self.assertEqual(records[1]["data_ranges"], [[31, 50]])

    def test_notes_overlap_not_clipped(self):
        # Even if a notes table overlaps the prior commission table, leave it.
        records = [
            _rec(1, "Commission", 4, 50),
            _rec(2, "Notes", 30, 60, table_type="notes"),
        ]

        _dedupe_overlapping_table_ranges(records, header_row_count=0)

        # Commission also not clipped because the overlapping neighbour is
        # a notes table (only commission-vs-commission overlaps are clipped).
        self.assertEqual(records[0]["end_row"], 50)
        self.assertEqual(records[1]["end_row"], 60)


class CombinedShapeTests(TestCase):
    """End-to-end shape mirroring the observed Two-Wheeler bug at small
    scale: shared-header cumulative chain plus a trailing notes table."""

    def test_cumulative_chain_with_trailing_notes(self):
        records = [
            _rec(1, "AP OEM", 4, 11),
            _rec(2, "TS OEM", 4, 16),
            _rec(3, "TN OEM", 4, 21),
            _rec(4, "Payout Notes", 22, 35, table_type="notes"),
        ]

        note = _dedupe_overlapping_table_ranges(records, header_row_count=3)

        self.assertIn("cumulative", note)
        # Tables 2 & 3 reshaped to header + own data; notes table untouched.
        self.assertEqual(records[1]["data_ranges"], [[4, 6], [12, 16]])
        self.assertEqual(records[2]["data_ranges"], [[4, 6], [17, 21]])
        self.assertEqual(records[3]["data_ranges"], [[22, 35]])


class SideBySideTableSplitTests(TestCase):
    """`_split_side_by_side_tables`: one detected table holding two grids that
    sit in the SAME rows but different column bands.

    Chola's Two-Wheeler sheet is 57 columns wide: columns 2-41 are one grid and
    42-56 another, each with its own State/Type key columns. Pass 0C reports one
    table, so the second grid's make-group headers (blank label cells) read as
    continuations of the first — every rule from them loses vehicle_make, and
    distinct columns collapse onto identical conditions with different payouts
    (54 CRITICAL duplicates on doc 733711bd).
    """

    def _lines(self, header: str, rows: int = 3) -> list:
        return [header] + [",".join(["x"] * 60) for _ in range(rows)]

    def test_repeated_key_signature_splits_into_two_blocks(self):
        header = (
            ",,State (RTO) > AP,Type > NEW,Hero OEM > 150cc > 0.425,"
            "Hero OEM > SCOOTER > 0.425,,"
            "State > AP,Type > NEW,All Electric Make > 7 KW Bike > 0.17"
        )
        records = [_rec(1, "Commission Grid", 1, 4)]

        note = _split_side_by_side_tables(records, self._lines(header))

        self.assertIn("side-by-side", note)
        self.assertEqual(len(records), 2)
        self.assertEqual((records[0]["start_col"], records[0]["end_col"]), (3, 7))
        self.assertEqual(records[1]["start_col"], 8)
        self.assertEqual(records[0]["table_name"], "Commission Grid (block 1)")
        self.assertEqual(records[1]["table_name"], "Commission Grid (block 2)")

    def test_block_indices_stay_unique(self):
        header = ",State,Type,A,,,State,Type,B"
        records = [_rec(1, "G1", 1, 4), _rec(7, "G2", 5, 8, table_type="notes")]

        _split_side_by_side_tables(records, self._lines(header))

        indices = [r["table_index"] for r in records]
        self.assertEqual(len(indices), len(set(indices)), indices)

    def test_row_ranges_are_untouched(self):
        header = ",State,Type,A,,,State,Type,B"
        records = [_rec(1, "G", 4, 20)]
        records[0]["data_ranges"] = [[4, 6], [12, 20]]

        _split_side_by_side_tables(records, ["", "", "", header] + ["x"] * 20)

        for rec in records:
            self.assertEqual(rec["data_ranges"], [[4, 6], [12, 20]])

    def test_single_grid_is_not_split(self):
        header = ",State,Type,Hero OEM > 150cc,Hero OEM > SCOOTER"
        records = [_rec(1, "G", 1, 4)]

        note = _split_side_by_side_tables(records, self._lines(header))

        self.assertEqual(note, "")
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]["start_col"])

    def test_adjacent_repeat_does_not_split(self):
        # A legitimately doubled label pair must not be read as a new block.
        header = ",State,Type,State,Type,A,B"
        records = [_rec(1, "G", 1, 4)]

        note = _split_side_by_side_tables(records, self._lines(header))

        self.assertEqual(note, "")
        self.assertEqual(len(records), 1)

    def test_non_commission_tables_are_never_split(self):
        header = ",State,Type,A,,,State,Type,B"
        for ttype in ("master", "notes"):
            with self.subTest(table_type=ttype):
                records = [_rec(1, "T", 1, 4, table_type=ttype)]
                note = _split_side_by_side_tables(records, self._lines(header))
                self.assertEqual(note, "")
                self.assertEqual(len(records), 1)

    def test_three_blocks_split_cleanly(self):
        header = ",State,Type,A,,,State,Type,B,,,State,Type,C"
        records = [_rec(1, "G", 1, 4)]

        _split_side_by_side_tables(records, self._lines(header))

        self.assertEqual(len(records), 3)
        starts = [r["start_col"] for r in records]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(len(set(starts)), 3)

    def test_malformed_header_is_left_alone(self):
        records = [_rec(1, "G", 1, 4)]
        note = _split_side_by_side_tables(records, ['"unterminated,,,'])
        self.assertEqual(note, "")
        self.assertEqual(len(records), 1)
