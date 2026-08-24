"""
A merged banner row spanning a whole table must not prefix every column label.

``expand_merged_cells`` copies a banner merged across the full table width into
every cell of that row, and ``_build_compound_headers`` only suppresses a label
repeated VERTICALLY. So Chola's ``PROP TW Apr-26`` sheet indexed as

    TW Make Grid for the month of Apr 26 > Hero OEM > 150cc

on all 39 columns, while the same workbook's ``TW Apr-26`` sheet — which has no
banner row — indexed as the plain ``Hero OEM > 150cc``.

The carrier overlay reads ``part[0]`` as the vehicle make and ``part[1]`` as the
CC band, so the banner shifted every real segment one position right and NO
column parsed as a make. Pass-3 codegen dropped the entire make grid and emitted
only the ``TW Electric`` block: 272 rules, every one of them ``vehicle_make=None``
/ ``cc=(0, 99999)`` / ``fuel_type=electric``, against ~1400 make x CC-band rules
on the unbannered sheets. The ``Electric`` was never fabricated — it was the only
table that survived.

Run from the backend container:
    python manage.py test cins.tests.test_shared_header_banner -v 2
"""

from unittest import TestCase

from cins.processors.content.csv_to_markdown import (
    _build_compound_headers,
    _strip_shared_header_banner,
    csv_to_markdown,
)
from cins.processors.core.codegen_coverage import count_rate_bearing_rows_markdown

BANNER = "TW Make Grid for the month of Apr 26"


class SharedHeaderBannerStripTests(TestCase):
    def test_banner_is_stripped_from_every_column(self):
        labels = [
            f"{BANNER} > State (RTO)",
            f"{BANNER} > Type",
            f"{BANNER} > Hero OEM > 150cc",
            f"{BANNER} > Hero OEM > SCOOTER",
            f"{BANNER} > TVS OEM > 150_350cc",
        ]
        stripped, banners = _strip_shared_header_banner(labels)
        self.assertEqual(
            stripped,
            [
                "State (RTO)",
                "Type",
                "Hero OEM > 150cc",
                "Hero OEM > SCOOTER",
                "TVS OEM > 150_350cc",
            ],
        )
        self.assertEqual(banners, [BANNER])

    def test_spacer_column_carrying_only_the_banner_becomes_empty(self):
        # The blank separator columns between make groups hold the banner and
        # nothing else; they must end up label-less, not keep a bogus label.
        labels = [
            f"{BANNER} > Hero OEM > 150cc",
            f"{BANNER} > Hero OEM > SCOOTER",
            BANNER,
            f"{BANNER} > TVS OEM > 150cc",
            f"{BANNER} > TVS OEM > SCOOTER",
        ]
        stripped, _ = _strip_shared_header_banner(labels)
        self.assertEqual(
            stripped,
            [
                "Hero OEM > 150cc",
                "Hero OEM > SCOOTER",
                "",
                "TVS OEM > 150cc",
                "TVS OEM > SCOOTER",
            ],
        )

    def test_single_leaked_column_does_not_block_the_strip(self):
        # The real block-1 shape: 39 columns of the make grid plus ONE column
        # that leaked in from the neighbouring electric grid. Requiring EVERY
        # column to share the banner made the whole fix a no-op here.
        labels = [f"{BANNER} > Hero OEM > 150cc" for _ in range(9)]
        labels.append("TW Electric")
        stripped, banners = _strip_shared_header_banner(labels)
        self.assertEqual(banners, [BANNER])
        self.assertEqual(stripped[:9], ["Hero OEM > 150cc"] * 9)
        # The dissenting column keeps its own label — there the segment is real.
        self.assertEqual(stripped[9], "TW Electric")

    def test_electric_block_banner_is_stripped_too(self):
        # Block 2 of the same sheet, whose banner is "TW Electric". The make
        # bucket still carries the electric signal, so nothing is lost.
        labels = [
            "TW Electric > State",
            "TW Electric > Type",
            "TW Electric > All Electric Make > 7 KW Bike",
            "TW Electric > All Electric Make > > 16 KW",
        ]
        stripped, banners = _strip_shared_header_banner(labels)
        self.assertEqual(
            stripped,
            [
                "State",
                "Type",
                "All Electric Make > 7 KW Bike",
                "All Electric Make > > 16 KW",
            ],
        )
        self.assertEqual(banners, ["TW Electric"])

    def test_only_one_segment_is_ever_stripped(self):
        # Stacked banners are NOT collapsed. Once the outer one is gone, a table
        # publishing a single make group is indistinguishable from a bannered
        # one, and a second pass would eat the make row.
        labels = [
            "Chola > Apr 26 > Hero OEM > 150cc",
            "Chola > Apr 26 > Hero OEM > SCOOTER",
            "Chola > Apr 26 > TVS OEM > 150cc",
            "Chola > Apr 26 > TVS OEM > SCOOTER",
        ]
        stripped, banners = _strip_shared_header_banner(labels)
        self.assertEqual(
            stripped,
            [
                "Apr 26 > Hero OEM > 150cc",
                "Apr 26 > Hero OEM > SCOOTER",
                "Apr 26 > TVS OEM > 150cc",
                "Apr 26 > TVS OEM > SCOOTER",
            ],
        )
        self.assertEqual(banners, ["Chola"])

    def test_a_single_make_table_keeps_its_make(self):
        # A table publishing ONE make heads every column with it, so dominance
        # alone would strip it and leave the band in part[0] — the exact defect
        # being fixed. Stripping must leave a real hierarchy behind, and here it
        # would not: every column drops to a single segment.
        labels = [
            "Hero OEM > 150cc",
            "Hero OEM > SCOOTER",
            "Hero OEM > 150_350cc",
            "Hero OEM > 350cc",
        ]
        stripped, banners = _strip_shared_header_banner(labels)
        self.assertEqual(stripped, labels)
        self.assertEqual(banners, [])

    # ── No-op guards ─────────────────────────────────────────────────────────

    def test_unbannered_headers_are_untouched(self):
        # The healthy `TW Apr-26` sheet. No shared first segment, so no strip.
        labels = [
            "State (RTO)",
            "Type",
            "Hero OEM > 150cc",
            "Hero OEM > SCOOTER",
            "TVS OEM > 150cc",
        ]
        stripped, banners = _strip_shared_header_banner(labels)
        self.assertEqual(stripped, labels)
        self.assertEqual(banners, [])

    def test_flat_single_segment_header_is_not_emptied(self):
        # Every column shares its ONLY segment. Stripping would erase the header
        # outright, so the "two columns must have a second segment" guard holds.
        labels = ["Rate", "Rate", "Rate", "Rate", "Rate"]
        stripped, banners = _strip_shared_header_banner(labels)
        self.assertEqual(stripped, labels)
        self.assertEqual(banners, [])

    def test_a_real_shared_parent_is_not_dominant_enough(self):
        # Two genuine dimension groups in one table. Neither reaches the 80%
        # dominance floor, so neither is mistaken for a banner.
        labels = [
            "GVW > UPTO 3.5T",
            "GVW > 3.5T TO 7.5T",
            "GVW > ABOVE 7.5T",
            "Seating > UPTO 5",
            "Seating > ABOVE 6",
        ]
        stripped, banners = _strip_shared_header_banner(labels)
        self.assertEqual(stripped, labels)
        self.assertEqual(banners, [])

    def test_make_group_is_never_mistaken_for_a_banner(self):
        # The largest make group heads 4 of 40 columns on the real sheet; here
        # it heads 4 of 8. Well under the dominance floor either way.
        labels = [
            "Hero OEM > 150cc",
            "Hero OEM > SCOOTER",
            "Hero OEM > 150_350cc",
            "Hero OEM > 350cc",
            "TVS OEM > 150cc",
            "TVS OEM > SCOOTER",
            "Honda > 150cc",
            "Bajaj > 150cc",
        ]
        stripped, banners = _strip_shared_header_banner(labels)
        self.assertEqual(stripped, labels)
        self.assertEqual(banners, [])

    def test_banner_spanning_too_few_columns_is_left_alone(self):
        # Three columns under one parent is a group, not a table banner.
        labels = ["Pack > ACT", "Pack > OD", "Pack > TP"]
        stripped, banners = _strip_shared_header_banner(labels)
        self.assertEqual(stripped, labels)
        self.assertEqual(banners, [])

    def test_prefix_match_is_not_enough_only_whole_segments_strip(self):
        labels = [
            "TW Make Grid > Hero OEM > 150cc",
            "TW Make Grid > Hero OEM > SCOOTER",
            "TW Make Grid Extra > TVS OEM > 150cc",
            "TW Make Grid Extra > TVS OEM > SCOOTER",
        ]
        stripped, banners = _strip_shared_header_banner(labels)
        self.assertEqual(stripped, labels)
        self.assertEqual(banners, [])

    def test_only_one_populated_column_is_left_alone(self):
        labels = ["Chola > Hero OEM > 150cc", ""]
        stripped, banners = _strip_shared_header_banner(labels)
        self.assertEqual(stripped, labels)
        self.assertEqual(banners, [])


class BanneredCsvToMarkdownTests(TestCase):
    """End-to-end through csv_to_markdown, the shape the indexer actually writes."""

    # Banner row merged across the table, then the make row, then the band row.
    CSV = (
        f"{BANNER},{BANNER},{BANNER},{BANNER}\n"
        "State (RTO),Type,Hero OEM,Hero OEM\n"
        ",,150cc,SCOOTER\n"
        "AP,NEW(100-500 NOP),0.425,0.425\n"
        "TS,ACT,0,0.3\n"
    )

    def test_header_row_is_banner_free(self):
        md = csv_to_markdown(self.CSV, header_row_count=3)
        header = [line for line in md.splitlines() if line.startswith("|")][0]
        self.assertNotIn(BANNER, header)
        self.assertIn("Hero OEM > 150cc", header)
        self.assertIn("Hero OEM > SCOOTER", header)

    def test_banner_survives_as_a_caption(self):
        # Some banners are load-bearing, so the text must stay in the pass-3
        # content even though it leaves the per-column labels.
        md = csv_to_markdown(self.CSV, header_row_count=3)
        self.assertTrue(md.startswith(f"_Section: {BANNER}_"))

    def test_caption_does_not_inflate_the_coverage_gate(self):
        # count_rate_bearing_rows_markdown splits a pipe-less line on commas, so
        # a caption ending in a bare number would read as a rate-bearing row.
        with_banner = csv_to_markdown(self.CSV, header_row_count=3)
        without_banner = csv_to_markdown(
            "\n".join(self.CSV.splitlines()[1:]) + "\n", header_row_count=2
        )
        self.assertEqual(
            count_rate_bearing_rows_markdown(with_banner),
            count_rate_bearing_rows_markdown(without_banner),
        )

    def test_comma_bearing_banner_cannot_read_as_a_rate_row(self):
        # Quoted, so the comma is part of the banner text rather than a field
        # separator — and the banner ends in a bare number, the risky shape.
        csv_content = (
            '"Grid for Apr, 26","Grid for Apr, 26","Grid for Apr, 26","Grid for Apr, 26"\n'
            "Hero OEM,Hero OEM,TVS OEM,TVS OEM\n"
            "150cc,SCOOTER,150cc,SCOOTER\n"
            "0.425,0.5,0.3,0.4\n"
        )
        md = csv_to_markdown(csv_content, header_row_count=3)
        caption = md.splitlines()[0]
        self.assertTrue(caption.startswith("_Section: "))
        self.assertNotIn(",", caption)
        # One data row, and the caption must not be counted alongside it.
        self.assertEqual(count_rate_bearing_rows_markdown(md), 1)

    def test_unbannered_sheet_markdown_is_unchanged(self):
        csv_content = (
            "State (RTO),Type,Hero OEM,Hero OEM\n"
            ",,150cc,SCOOTER\n"
            "AP,NEW(100-500 NOP),0.425,0.425\n"
        )
        md = csv_to_markdown(csv_content, header_row_count=2)
        self.assertFalse(md.startswith("_Section:"))
        self.assertTrue(md.startswith("| State (RTO) | Type | Hero OEM > 150cc |"))

    def test_data_rows_are_untouched_by_the_strip(self):
        md = csv_to_markdown(self.CSV, header_row_count=3)
        self.assertIn("| AP | NEW(100-500 NOP) | 0.425 | 0.425 |", md)
        self.assertIn("| TS | ACT | 0 | 0.3 |", md)


class BuildThenStripIntegrationTests(TestCase):
    """The two helpers compose: build first, strip second."""

    def test_real_prop_tw_header_band(self):
        rows = [
            [BANNER] * 7,
            ["State (RTO)", "Type", "Hero OEM", "Hero OEM", "", "TVS OEM", "TVS OEM"],
            ["", "", "150cc", "SCOOTER", "", "150cc", "SCOOTER"],
        ]
        stripped, banners = _strip_shared_header_banner(_build_compound_headers(rows))
        self.assertEqual(banners, [BANNER])
        self.assertEqual(
            stripped,
            [
                "State (RTO)",
                "Type",
                "Hero OEM > 150cc",
                "Hero OEM > SCOOTER",
                "",
                "TVS OEM > 150cc",
                "TVS OEM > SCOOTER",
            ],
        )
