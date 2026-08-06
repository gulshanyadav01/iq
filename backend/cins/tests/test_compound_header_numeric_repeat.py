"""
Compound header collapsing must keep a vertically REPEATED numeric cell.

Pass 0C sometimes swallows a grid's first data rows into the header band. Chola's
GCCV sheet does exactly that with ``1_GCCV_3W`` and ``1_GCCV_3W[Electric]``, so
their rates ride along as trailing ``>`` segments of each state x coverage header
cell. Pass-3 codegen aligns the i-th trailing value to the i-th SUBCLASS segment.

Vertical-repeat suppression exists to kill merged-cell repeats of a *label*
("AP/TS" spanning ACT and PACK). Applied to a *value* it is data loss: the base
and electric 3-wheeler rates are equal for most states, so
``AP/TS > ACT > 0.325 > 0.325`` collapsed to ``AP/TS > ACT > 0.325`` and the
electric twin vanished for 18 of the 20 state groups on the July 2025 grid.

Run from the backend container:
    python manage.py test cins.tests.test_compound_header_numeric_repeat -v 2
"""

from unittest import TestCase

from cins.processors.content.csv_to_markdown import (
    _build_compound_headers,
    csv_to_markdown,
    effective_header_row_count,
)
from cins.processors.core.codegen_coverage import count_rate_bearing_rows


class CompoundHeaderNumericRepeatTests(TestCase):
    def test_repeated_numeric_value_is_kept(self):
        # state / coverage / base-3W rate / electric-3W rate — last two equal.
        rows = [["AP/TS"], ["ACT"], ["0.325"], ["0.325"]]
        self.assertEqual(_build_compound_headers(rows), ["AP/TS > ACT > 0.325 > 0.325"])

    def test_differing_numeric_values_still_both_kept(self):
        rows = [["MH/GA"], ["PACK"], ["0.47"], ["0.45"]]
        self.assertEqual(_build_compound_headers(rows), ["MH/GA > PACK > 0.47 > 0.45"])

    def test_repeated_label_is_still_suppressed(self):
        # The merged-cell repeat this dedupe exists for.
        rows = [["AP/TS", "AP/TS"], ["ACT", "PACK"]]
        self.assertEqual(_build_compound_headers(rows), ["AP/TS > ACT", "AP/TS > PACK"])

    def test_labels_containing_digits_are_not_treated_as_values(self):
        # "150cc" / "1_GCCV_3W" / "UPTO 3.5T" are labels, so their repeats
        # must still collapse.
        for label in ("150cc", "1_GCCV_3W", "UPTO 3.5T", "7 KW Bike"):
            with self.subTest(label=label):
                self.assertEqual(
                    _build_compound_headers([["Hero OEM"], [label], [label]]),
                    [f"Hero OEM > {label}"],
                )

    def test_percent_and_signed_values_count_as_numeric(self):
        for val in ("30%", "0", "-0.5", "+2.5%"):
            with self.subTest(val=val):
                self.assertEqual(
                    _build_compound_headers([["GJ"], ["PACK"], [val], [val]]),
                    [f"GJ > PACK > {val} > {val}"],
                )

    def test_empty_cells_do_not_reset_the_group(self):
        rows = [["AP/TS", ""], ["ACT", "PACK"]]
        self.assertEqual(_build_compound_headers(rows), ["AP/TS > ACT", "PACK"])


_CHOLA_GCCV_CSV = (
    "PROD,SUBCLASS,AP/TS,AP/TS,MH/GA,MH/GA\n"
    ",,ACT,PACK,ACT,PACK\n"
    "GCCV,1_GCCV_3W,0.325,0.375,0.325,0.47\n"
    "GCCV,1_GCCV_3W[Electric],0.325,0.375,0.325,0.45\n"
    "GCCV,2_UPTO_3.5T,0.425,0.475,0.45,0.475\n"
)


class SwallowedDataRowTrimTests(TestCase):
    """Rows Excel/Pass-0C counted as header but that are really the grid's first
    DATA rows get pushed back into the data section, so codegen reads them
    positionally instead of needing a bespoke header-recovery loop."""

    def _rows(self, csv_content):
        import csv as _csv
        import io as _io

        rows = list(_csv.reader(_io.StringIO(csv_content)))
        width = max(len(r) for r in rows)
        return [r + [""] * (width - len(r)) for r in rows]

    def test_chola_gccv_band_trims_to_the_label_rows(self):
        # Pass 0C reports 4 header rows; rows 2 and 3 are the 3W data rows.
        self.assertEqual(effective_header_row_count(self._rows(_CHOLA_GCCV_CSV), 4), 2)

    def test_trimmed_header_is_plain_state_coverage(self):
        md = csv_to_markdown(_CHOLA_GCCV_CSV, header_row_count=4)
        lines = md.splitlines()
        self.assertIn("AP/TS > ACT", lines[0])
        self.assertIn("MH/GA > PACK", lines[0])
        self.assertNotIn("0.325", lines[0])
        # Both 3W rows now appear as ordinary data rows.
        self.assertIn("1_GCCV_3W", lines[2])
        self.assertIn("1_GCCV_3W[Electric]", lines[3])
        self.assertIn("2_UPTO_3.5T", lines[4])

    def test_coverage_gate_counts_the_recovered_rows(self):
        # 3 rate-bearing data rows once the band is trimmed, not 1.
        self.assertEqual(count_rate_bearing_rows(_CHOLA_GCCV_CSV, 4), 3)

    def test_label_only_header_band_is_untouched(self):
        csv_content = (
            "PROD,SUBCLASS,AP/TS,AP/TS\n,,ACT,PACK\nPC,UPTO 1000 CC,0.3,0.25\n"
        )
        self.assertEqual(effective_header_row_count(self._rows(csv_content), 2), 2)

    def test_numeric_labels_do_not_trigger_a_trim(self):
        # A cc-band header row of bare integers is labels, not rates.
        csv_content = (
            "Hero OEM,Hero OEM,TVS OEM,TVS OEM\n150,350,150,350\nAP,0.4,0.3,0.35\n"
        )
        self.assertEqual(effective_header_row_count(self._rows(csv_content), 2), 2)

    def test_at_least_one_header_row_is_always_kept(self):
        csv_content = "0.1,0.2,0.3,0.4\n0.5,0.6,0.7,0.8\n"
        self.assertEqual(effective_header_row_count(self._rows(csv_content), 2), 1)

    def test_single_and_zero_header_counts_pass_through(self):
        rows = self._rows(_CHOLA_GCCV_CSV)
        self.assertEqual(effective_header_row_count(rows, 0), 0)
        self.assertEqual(effective_header_row_count(rows, 1), 1)
