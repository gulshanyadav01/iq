"""Self-declared indicative sheets — the scan, the override, and the routing.

The defect: Chola's `Hero Cells -June 26` one-pager carries real rates and says
in its own text that it is not the payout grid. It shipped 96 phantom
`commercial-gcv` rules. See `.claude/rules/cins.md`.

The risk this suite exists to hold down is the FALSE POSITIVE. A hit deletes a
whole sheet's rules silently, so most of `IndicativeMarkerScanTests` is
negative cases.
"""

from django.test import TestCase, override_settings

from cins.processors.core.sheet_visibility import (
    derive_sheet_sets,
    indicative_sheet_names,
    nothing_routable_error,
    routable_sheet_names,
)
from cins.processors.extractors.excel_to_csv import _apply_indicative_override
from cins.processors.extractors.indicative_markers import (
    scan_for_indicative_marker,
)

#: The live text, verbatim from `cicsresult` row 180edf61 — including the
#: missing space in `payout,this`, which is exactly what the normalizer has to
#: survive.
LIVE_DISCLAIMER = (
    "Please check the payout Grid for actual payout,this sheet is just to "
    "identify the potential products"
)


def _cls(sheet_type, visibility="visible"):
    return {"sheet_type": sheet_type, "visibility": visibility}


class IndicativeMarkerScanTests(TestCase):
    def test_fires_on_the_live_disclaimer(self):
        match = scan_for_indicative_marker(LIVE_DISCLAIMER)
        self.assertIsNotNone(match)
        self.assertEqual(match.self_ref, "this sheet is")
        self.assertEqual(match.deferral, "identify the potential products")
        self.assertEqual(match.line_number, 1)

    def test_fires_on_the_live_disclaimer_as_a_quoted_csv_field(self):
        """The production shape: one quoted field padded with empty columns.

        This is the actual line 20 of `Hero_Cells_-June_26_hero_cells.csv`.
        """
        csv_text = "\n".join(
            [
                "State,UPTO 3.5T,3.5T TO 7.5T",
                "Tamil Nadu,55,42",
                "Karnataka,40,35",
                f'"{LIVE_DISCLAIMER}",,,,,,,,,,,,,,,,,',
            ]
        )
        match = scan_for_indicative_marker(csv_text)
        self.assertIsNotNone(match)
        self.assertEqual(match.line_number, 4)
        self.assertIn("potential products", match.excerpt)

    def test_does_not_fire_on_an_ordinary_cross_reference(self):
        """The single most important negative case.

        Nearly every real payout grid mentions the words "payout grid". A
        footnote directing the reader elsewhere for a DIFFERENT kind of
        information is not a disclaimer about this sheet's own rates.
        """
        for text in (
            "Please refer to the payout grid for RTO code groupings",
            "See the payout Grid for the list of states covered by this rate card",
            "Note: the payout grid is updated monthly by the underwriting team",
        ):
            with self.subTest(text=text):
                self.assertIsNone(scan_for_indicative_marker(text))

    def test_does_not_fire_on_short_header_or_label_cells(self):
        """A `Payout Grid` header or a `This Sheet` column label is not prose."""
        for text in (
            "Payout Grid",
            "This Sheet",
            "State,Payout Grid,This Sheet",
            "this sheet is current",
        ):
            with self.subTest(text=text):
                self.assertIsNone(scan_for_indicative_marker(text))

    def test_does_not_fire_when_the_two_halves_are_on_different_lines(self):
        """Pins the same-line rule.

        A disclaimer lives in ONE spreadsheet cell. Allowing the halves to fuse
        across lines would let a header saying "payout grid" combine with an
        unrelated footnote hundreds of rows below.
        """
        csv_text = "\n".join(
            [
                "this sheet is the consolidated commercial vehicle rate card,,,",
                "State,UPTO 3.5T,3.5T TO 7.5T",
                "Tamil Nadu,55,42",
                "Kindly check the payout grid for actual payout of Private Car,,,",
            ]
        )
        self.assertIsNone(scan_for_indicative_marker(csv_text))

    def test_does_not_fire_below_the_length_floor(self):
        """Both halves present, but too short to be a sentence."""
        short = "this sheet is not a payout grid"
        self.assertLess(len(short), 60)
        self.assertIsNone(scan_for_indicative_marker(short))

    def test_first_hit_wins_and_reports_its_line(self):
        csv_text = "\n".join(["header,1,2", "", LIVE_DISCLAIMER, LIVE_DISCLAIMER])
        match = scan_for_indicative_marker(csv_text)
        self.assertEqual(match.line_number, 3)

    def test_empty_input_is_not_a_hit(self):
        self.assertIsNone(scan_for_indicative_marker(""))
        self.assertIsNone(scan_for_indicative_marker(None))

    @override_settings(CINS_INDICATIVE_SHEETS=False)
    def test_flag_off_disables_the_scan(self):
        self.assertIsNone(scan_for_indicative_marker(LIVE_DISCLAIMER))


class IndicativeOverrideTests(TestCase):
    """The prior verdict is evidence and must survive the override."""

    def setUp(self):
        self.match = scan_for_indicative_marker(LIVE_DISCLAIMER)
        self.assertIsNotNone(self.match)

    def test_overrides_commission_data_and_preserves_the_prior_verdict(self):
        prior = {
            "sheet_name": "hero cells",
            "sheet_type": "commission_data",
            "confidence": 1.0,
            "indicators": ["has percentage columns"],
        }
        out = _apply_indicative_override(prior, "hero cells", self.match)

        self.assertEqual(out["sheet_type"], "indicative_data")
        self.assertEqual(out["confidence"], 1.0)
        self.assertEqual(out["override"]["overridden_sheet_type"], "commission_data")
        self.assertEqual(out["override"]["overridden_confidence"], 1.0)
        self.assertIn("potential products", out["override"]["excerpt"])
        # The prior indicators are kept, not discarded.
        self.assertIn("has percentage columns", out["indicators"])

    def test_overrides_master_data_too(self):
        """Uniform override, deliberately.

        A self-declared non-authoritative rate grid labelled master_data still
        has its full content concatenated into `master_context` and injected
        into every sibling sheet's Pass 1 and Pass 2 prompt.
        """
        out = _apply_indicative_override(
            {"sheet_type": "master_data", "confidence": 0.8}, "hero cells", self.match
        )
        self.assertEqual(out["sheet_type"], "indicative_data")
        self.assertEqual(out["override"]["overridden_sheet_type"], "master_data")

    def test_synthesises_an_entry_when_classification_is_absent(self):
        """A sheet whose classify call threw is the one that most needs a label.

        With `sheet_type: None` it lands in no bucket at all, and the
        empty-commission fallback in `derive_pipeline_routing` resurrects it.
        """
        out = _apply_indicative_override(None, "hero cells", self.match)
        self.assertEqual(out["sheet_type"], "indicative_data")
        self.assertEqual(out["sheet_name"], "hero cells")
        self.assertIsNone(out["override"]["overridden_sheet_type"])


class IndicativeRoutingTests(TestCase):
    def test_lands_in_the_indicative_bucket_and_no_other(self):
        sets = derive_sheet_sets(
            {
                "Grid": _cls("commission_data"),
                "Legend": _cls("master_data"),
                "hero cells": _cls("indicative_data"),
            }
        )
        self.assertEqual(sets.commission, ["Grid"])
        self.assertEqual(sets.master, ["Legend"])
        self.assertEqual(sets.indicative, ["hero cells"])
        self.assertNotIn("hero cells", sets.commission)
        self.assertNotIn("hero cells", sets.master)

    def test_hidden_still_wins_over_indicative(self):
        sets = derive_sheet_sets({"hero cells": _cls("indicative_data", "hidden")})
        self.assertEqual(sets.hidden, ["hero cells"])
        self.assertEqual(sets.indicative, [])

    def test_master_context_cannot_be_polluted(self):
        """The whole reason indicative does not route to master_data."""
        sets = derive_sheet_sets({"hero cells": _cls("indicative_data")})
        self.assertEqual(sets.master, [])

    def test_routable_excludes_indicative_as_well_as_hidden(self):
        classifications = {
            "Grid": _cls("commission_data"),
            "Scratch": _cls("commission_data", "hidden"),
            "hero cells": _cls("indicative_data"),
        }
        names = ["Grid", "Scratch", "hero cells"]
        self.assertEqual(routable_sheet_names(names, classifications), ["Grid"])

    def test_the_fallback_does_not_resurrect_a_lone_indicative_sheet(self):
        """The inversion this guard exists to prevent.

        With no commission sheet, `derive_pipeline_routing` falls back to "every
        visible sheet". Filtering only the commission set would route the
        one-pager anyway.
        """
        from cins.processors.core.pipeline import derive_pipeline_routing

        classifications = {"hero cells": _cls("indicative_data")}
        sheets_to_process, _attachment = derive_pipeline_routing(
            source_type="excel",
            sheet_classifications=classifications,
            sheet_names=["hero cells"],
            excel_attachments=[],
        )
        self.assertEqual(sheets_to_process, [])

    def test_indicative_sheet_names_helper(self):
        classifications = {
            "Grid": _cls("commission_data"),
            "hero cells": _cls("indicative_data"),
        }
        self.assertEqual(indicative_sheet_names(classifications), ["hero cells"])


class NothingRoutableErrorTests(TestCase):
    def test_all_indicative_does_not_claim_the_sheets_are_hidden(self):
        """Telling the operator to unhide a tab that is not hidden is a bug."""
        msg = nothing_routable_error(1, [], ["hero cells"])
        self.assertIn("hero cells", msg)
        self.assertIn("not the payout grid", msg)
        self.assertNotIn("hidden", msg)

    def test_hidden_only_keeps_its_original_wording(self):
        msg = nothing_routable_error(2, ["Grid", "Legend"], [])
        self.assertIn("all 2", msg)
        self.assertIn("hidden", msg)

    def test_both_causes_are_named(self):
        msg = nothing_routable_error(2, ["Scratch"], ["hero cells"])
        self.assertIn("Scratch", msg)
        self.assertIn("hero cells", msg)
        self.assertIn("hidden", msg)
