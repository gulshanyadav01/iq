"""
Tests for hidden-worksheet exclusion.

A worksheet whose Excel tab is hidden ('hidden') or hidden-from-the-UI
('veryHidden') is still indexed and classified — the UI shows what was skipped
— but must never produce commission rules and must never appear in a Pass 1/2/3
prompt. The predicate lives in `cins.processors.core.sheet_visibility` and is
shared by five derivation sites; these tests pin each one, plus the two ways
the feature can fail *silently*:

  * the "no commission sheets → process everything" fallback resurrecting the
    hidden sheets it was meant to exclude, and
  * an absent `visibility` key (every document indexed before INDEXER_VERSION
    1.4) being read as hidden rather than visible.

Pure helpers are exercised directly so no LLM call is made. Run from the
backend container:

    python manage.py test cins.tests.test_hidden_sheets -v 2
"""

import builtins
import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase

import openpyxl
from django.test import TestCase as DjangoTestCase, override_settings

from cins.processors.content.indexer import IndexedDocument
from cins.processors.core.pipeline import (
    _extractable_sheet_contents,
    derive_pipeline_routing,
)
from cins.processors.core.sheet_visibility import (
    all_hidden_error,
    derive_sheet_sets,
    hidden_sheet_names,
    is_sheet_visible,
    visible_sheet_names,
)
from cins.processors.extractors.excel_to_csv import ExcelToCSVConverter


def _cls(sheet_type=None, visibility=None):
    """One `sheet_classifications` entry. `visibility=None` omits the key,
    which is exactly what a pre-1.4 indexed document looks like."""
    entry = {"sheet_type": sheet_type, "confidence": 0.9}
    if visibility is not None:
        entry["visibility"] = visibility
    return entry


def _indexed_doc(classifications, sheet_contents, source_type="excel"):
    """An IndexedDocument with content pre-loaded — no disk reads."""
    return IndexedDocument(
        document_id="doc.xlsx",
        provider="TestCarrier",
        source_file=Path("doc.xlsx"),
        source_type=source_type,
        indexed_dir=Path("."),
        metadata={
            "content_files": {"sheets": list(sheet_contents)},
            "source_metadata": {"sheet_names": list(sheet_contents)},
            "sheet_classifications": classifications,
        },
        _sheet_contents=dict(sheet_contents),
    )


def _route(classifications, sheet_names, source_type="excel"):
    sheets, _attachment = derive_pipeline_routing(
        source_type=source_type,
        sheet_classifications=classifications,
        sheet_names=sheet_names,
        excel_attachments=[],
    )
    return sheets


class SheetVisibilityPredicateTests(TestCase):
    """The predicate itself: whitelist semantics and the fail-open default."""

    def test_absent_visibility_key_reads_as_visible(self):
        # F6: the silent one. `not info.get("visible")` would make every legacy
        # document filter to zero sheets.
        self.assertTrue(is_sheet_visible({"sheet_type": "commission_data"}))
        self.assertTrue(is_sheet_visible({}))
        self.assertTrue(is_sheet_visible(None))

    def test_hidden_and_very_hidden_are_excluded(self):
        self.assertFalse(is_sheet_visible(_cls("commission_data", "hidden")))
        self.assertFalse(is_sheet_visible(_cls("commission_data", "veryHidden")))

    def test_unknown_state_is_excluded_not_admitted(self):
        # Whitelist, not blacklist — a state openpyxl invents later must not
        # silently route.
        self.assertFalse(is_sheet_visible(_cls("commission_data", "someFutureState")))

    def test_derive_sheet_sets_partitions_by_visibility_then_type(self):
        classifications = {
            "Visible CD": _cls("commission_data", "visible"),
            "Visible MD": _cls("master_data", "visible"),
            "Hidden CD": _cls("commission_data", "hidden"),
            "Hidden MD": _cls("master_data", "veryHidden"),
            "Unclassified": _cls(None, "visible"),
        }
        commission, master, hidden = derive_sheet_sets(classifications)
        self.assertEqual(commission, ["Visible CD"])
        self.assertEqual(master, ["Visible MD"])
        self.assertEqual(sorted(hidden), ["Hidden CD", "Hidden MD"])

    def test_visible_sheet_names_preserves_order(self):
        classifications = {"A": _cls(None, "visible"), "B": _cls(None, "hidden")}
        self.assertEqual(
            visible_sheet_names(["A", "B", "C"], classifications), ["A", "C"]
        )


class RoutingTests(TestCase):
    """Site 1 — `derive_pipeline_routing`."""

    def test_hidden_commission_sheet_is_not_routed(self):
        classifications = {
            "Grid": _cls("commission_data", "visible"),
            "Scratch": _cls("commission_data", "hidden"),
        }
        self.assertEqual(_route(classifications, ["Grid", "Scratch"]), ["Grid"])

    def test_fallback_does_not_resurrect_hidden_sheets(self):
        # The highest-value case: no sheet is classified commission_data, so
        # routing falls back to "process everything". Filtering only the
        # commission list would bring every hidden sheet straight back.
        classifications = {
            "Notes": _cls("master_data", "visible"),
            "Scratch": _cls("master_data", "hidden"),
            "Template": _cls(None, "veryHidden"),
        }
        routed = _route(classifications, ["Notes", "Scratch", "Template"])
        self.assertEqual(routed, ["Notes"])

    def test_hiding_the_only_commission_sheet_does_not_route_it_via_fallback(self):
        classifications = {
            "Grid": _cls("commission_data", "hidden"),
            "Legend": _cls("master_data", "visible"),
        }
        routed = _route(classifications, ["Grid", "Legend"])
        self.assertNotIn("Grid", routed)
        self.assertEqual(routed, ["Legend"])

    def test_all_hidden_routes_to_nothing(self):
        classifications = {
            "Grid": _cls("commission_data", "hidden"),
            "Legend": _cls("master_data", "veryHidden"),
        }
        self.assertEqual(_route(classifications, ["Grid", "Legend"]), [])

    def test_legacy_classifications_route_exactly_as_before(self):
        # REGRESSION: a document indexed before this change has no visibility
        # keys anywhere. Routing must be byte-identical to the old behaviour.
        classifications = {
            "Grid": {"sheet_type": "commission_data", "confidence": 0.9},
            "Legend": {"sheet_type": "master_data", "confidence": 0.8},
        }
        self.assertEqual(_route(classifications, ["Grid", "Legend"]), ["Grid"])

    def test_zero_hidden_workbook_routes_exactly_as_before(self):
        # REGRESSION: visibility recorded, nothing hidden -> no behaviour change.
        classifications = {
            "Grid A": _cls("commission_data", "visible"),
            "Grid B": _cls("commission_data", "visible"),
            "Legend": _cls("master_data", "visible"),
        }
        self.assertEqual(
            _route(classifications, ["Grid A", "Grid B", "Legend"]),
            ["Grid A", "Grid B"],
        )

    def test_image_pseudo_sheets_without_classification_still_route(self):
        # `image::<stem>` sheets have no workbook behind them and therefore no
        # visibility — the fail-open default must keep them routable.
        classifications = {"image::grid": {"sheet_type": "commission_data"}}
        self.assertEqual(_route(classifications, ["image::grid"]), ["image::grid"])

    def test_prefixed_email_attachment_keys_are_respected(self):
        # Multi-attachment .eml keys classifications by "<prefix>__<sheet>";
        # visibility must be looked up under the same key.
        classifications = {
            "book_a__Grid": _cls("commission_data", "visible"),
            "book_b__Grid": _cls("commission_data", "hidden"),
        }
        routed = _route(
            classifications, ["book_a__Grid", "book_b__Grid"], source_type="email"
        )
        self.assertEqual(routed, ["book_a__Grid"])


class MasterContextTests(TestCase):
    """Sites 2 and 3 — the sets `_process_sheets` builds `master_context` from.

    This is the requirement routing alone does not satisfy: `master_context` is
    appended to the Pass 1 prompt and *is* the entire Pass 2 prompt, and it is
    built from `get_master_sheets()`, which never touches the routing helper.
    """

    def test_get_master_sheets_excludes_hidden(self):
        doc = _indexed_doc(
            {
                "Legend": _cls("master_data", "visible"),
                "Old Legend": _cls("master_data", "hidden"),
            },
            {"Legend": "visible legend", "Old Legend": "stale legend"},
        )
        self.assertEqual(doc.get_master_sheets(), ["Legend"])

    def test_get_commission_sheets_excludes_hidden(self):
        doc = _indexed_doc(
            {
                "Grid": _cls("commission_data", "visible"),
                "Scratch": _cls("commission_data", "veryHidden"),
            },
            {"Grid": "g", "Scratch": "s"},
        )
        self.assertEqual(doc.get_commission_sheets(), ["Grid"])

    def test_hidden_master_content_never_reaches_a_prompt(self):
        doc = _indexed_doc(
            {
                "Grid": _cls("commission_data", "visible"),
                "Secret Legend": _cls("master_data", "hidden"),
            },
            {"Grid": "grid rows", "Secret Legend": "SECRET_RTO_CODE_TABLE"},
        )
        contents = doc.get_all_sheet_contents()
        master_context = "".join(
            contents.get(name, "") for name in doc.get_master_sheets()
        )
        self.assertNotIn("SECRET_RTO_CODE_TABLE", master_context)

    def test_hidden_sheets_remain_available_for_the_ui(self):
        # Approach C: excluded from extraction, NOT dropped from the index.
        # The sheet list, the "show content" peek and the coverage denominator
        # all collapse if this stops being true.
        doc = _indexed_doc(
            {
                "Grid": _cls("commission_data", "visible"),
                "Scratch": _cls("commission_data", "hidden"),
            },
            {"Grid": "g", "Scratch": "s"},
        )
        self.assertIn("Scratch", doc.get_all_sheet_contents())
        self.assertEqual(doc.get_hidden_sheets(), ["Scratch"])


class ValidatorScopeTests(TestCase):
    """Site 5 — coverage validators must not score hidden sheets.

    Without this, every data row on an excluded sheet becomes an "uncovered
    row" and the change reports itself as a wall of CRITICAL errors.
    """

    def test_hidden_sheets_are_dropped_before_coverage_checks(self):
        doc = _indexed_doc(
            {
                "Grid": _cls("commission_data", "visible"),
                "Scratch": _cls("commission_data", "hidden"),
            },
            {"Grid": "grid rows", "Scratch": "rows nobody extracted"},
        )
        self.assertEqual(_extractable_sheet_contents(doc), {"Grid": "grid rows"})

    def test_legacy_doc_passes_all_sheets_through_unchanged(self):
        doc = _indexed_doc(
            {"Grid": {"sheet_type": "commission_data"}},
            {"Grid": "grid rows", "Legend": "legend rows"},
        )
        self.assertEqual(
            _extractable_sheet_contents(doc),
            {"Grid": "grid rows", "Legend": "legend rows"},
        )

    def test_object_without_classifications_is_tolerated(self):
        class _Stub:
            def get_all_sheet_contents(self):
                return {"Grid": "rows"}

        self.assertEqual(_extractable_sheet_contents(_Stub()), {"Grid": "rows"})


class RerunParityTests(TestCase):
    """Site 4 — `rerun_single_pass` must derive the same sets as the pipeline.

    The task builds its own `master_context`; if it derived sheets differently
    the same document would produce two different Pass 2 prompts depending on
    whether you ran it or re-ran it.
    """

    def test_rerun_derivation_matches_pipeline_derivation(self):
        classifications = {
            "Grid": _cls("commission_data", "visible"),
            "Scratch": _cls("commission_data", "hidden"),
            "Legend": _cls("master_data", "visible"),
            "Old Legend": _cls("master_data", "veryHidden"),
        }
        commission, master, _hidden = derive_sheet_sets(classifications)
        doc = _indexed_doc(classifications, {k: "x" for k in classifications})
        self.assertEqual(commission, doc.get_commission_sheets())
        self.assertEqual(master, doc.get_master_sheets())
        self.assertEqual(
            _route(classifications, list(classifications)), doc.get_commission_sheets()
        )


class PreCreateLockstepTests(TestCase):
    """`run_extraction` pre-creates PENDING ExtractionPass rows from its own
    routing call; `_process_sheets` routes again when it runs. If those two ever
    disagree, the pre-created rows for sheets the pipeline skips sit PENDING
    forever and the UI shows a pass set that never completes.

    Parity holds by construction (both call `derive_pipeline_routing`), but the
    spec called this invariant out by name, so it gets pinned rather than
    assumed."""

    def _both_derivations(self, classifications, sheet_contents):
        # What commission_intake.tasks.run_extraction does with the DB row.
        pre_create, pre_attachment = derive_pipeline_routing(
            source_type="excel",
            sheet_classifications=classifications,
            sheet_names=list(sheet_contents.keys()),
            excel_attachments=[],
        )
        # What pipeline._process_sheets does with the reconstructed document.
        doc = _indexed_doc(classifications, sheet_contents)
        pipeline_side, pipeline_attachment = derive_pipeline_routing(
            source_type=doc.source_type,
            sheet_classifications=doc.get_sheet_classifications(),
            sheet_names=list(doc.get_all_sheet_contents().keys()),
            excel_attachments=[],
        )
        return (pre_create, pre_attachment), (pipeline_side, pipeline_attachment)

    def test_routing_matches_with_hidden_sheets_present(self):
        classifications = {
            "Grid": _cls("commission_data", "visible"),
            "Old Grid": _cls("commission_data", "hidden"),
            "Legend": _cls("master_data", "veryHidden"),
        }
        contents = {n: "rows" for n in classifications}
        pre, pipeline = self._both_derivations(classifications, contents)
        self.assertEqual(pre, pipeline)
        self.assertEqual(pre[0], ["Grid"])

    def test_routing_matches_on_the_fallback_path(self):
        # The fallback is where the two could most plausibly drift.
        classifications = {
            "Legend": _cls("master_data", "visible"),
            "Scratch": _cls(None, "hidden"),
        }
        contents = {n: "rows" for n in classifications}
        pre, pipeline = self._both_derivations(classifications, contents)
        self.assertEqual(pre, pipeline)
        self.assertEqual(pre[0], ["Legend"])

    def test_routing_matches_for_a_legacy_document(self):
        classifications = {"Grid": {"sheet_type": "commission_data"}}
        pre, pipeline = self._both_derivations(classifications, {"Grid": "rows"})
        self.assertEqual(pre, pipeline)


class Pass3InputSourceTests(TestCase):
    """`email_is_primary_commission` flips Pass 3's input source when there are
    no commission sheets — hiding the only commission sheet in an email+xlsx
    document therefore silently changes what Pass 3 reads. Pinned so the flip
    is a decision, not a surprise."""

    def test_hiding_the_only_commission_sheet_moves_pass3_to_the_email_body(self):
        from cins.processors.core.pipeline import build_pass_contents

        classifications = {"Grid": _cls("commission_data", "hidden")}
        commission, _master, _hidden = derive_sheet_sets(classifications)

        _p1, _p2, pass3, email_is_primary = build_pass_contents(
            sheet_name="Grid",
            sheet_content="hidden grid rows",
            master_context="",
            email_body="| Product | Payout |\n| --- | --- |\n| CAR | 20% |",
            commission_sheets=commission,
        )
        self.assertTrue(email_is_primary)
        self.assertIn("Email Commission Data", pass3)
        self.assertNotIn("hidden grid rows", pass3)

    def test_visible_commission_sheet_keeps_pass3_on_the_sheet(self):
        from cins.processors.core.pipeline import build_pass_contents

        classifications = {"Grid": _cls("commission_data", "visible")}
        commission, _master, _hidden = derive_sheet_sets(classifications)

        _p1, _p2, pass3, email_is_primary = build_pass_contents(
            sheet_name="Grid",
            sheet_content="grid rows",
            master_context="",
            email_body="some email body",
            commission_sheets=commission,
        )
        self.assertFalse(email_is_primary)
        self.assertIn("grid rows", pass3)


class AllHiddenTests(TestCase):
    def test_error_message_names_every_hidden_sheet(self):
        classifications = {
            "Grid": _cls("commission_data", "hidden"),
            "Legend": _cls("master_data", "veryHidden"),
        }
        msg = all_hidden_error(2, hidden_sheet_names(classifications))
        self.assertIn("Grid", msg)
        self.assertIn("Legend", msg)
        self.assertIn("all 2", msg)


class WorkbookVisibilityCaptureTests(TestCase):
    """`process_sheet` reads `sheet_state` off the live worksheet — the one
    fact the whole feature rests on."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out_dir = Path(cls._tmp.name) / "out"
        cls.path = str(Path(cls._tmp.name) / "book.xlsx")

        wb = openpyxl.Workbook()
        visible = wb.active
        visible.title = "Commission"
        visible["A1"] = "Policy"
        visible["B1"] = "Rate"
        visible["A2"] = "CAR"
        visible["B2"] = "20%"

        scratch = wb.create_sheet("Scratch")
        scratch["A1"] = "junk"
        scratch.sheet_state = "hidden"

        internal = wb.create_sheet("Internal")
        internal["A1"] = "vba only"
        internal.sheet_state = "veryHidden"

        wb.save(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _converter(self):
        # enable_classification=False keeps this offline — no LLM call.
        return ExcelToCSVConverter(
            output_dir=self.out_dir, fill_empty="", enable_classification=False
        )

    def test_process_sheet_reports_each_tab_state(self):
        converter = self._converter()
        states = {
            name: converter.process_sheet(self.path, name, self.out_dir)["sheet_state"]
            for name in ("Commission", "Scratch", "Internal")
        }
        self.assertEqual(
            states,
            {
                "Commission": "visible",
                "Scratch": "hidden",
                "Internal": "veryHidden",
            },
        )

    def test_process_workbook_reports_state_for_every_sheet(self):
        results = self._converter().process_workbook(self.path, self.out_dir)
        by_name = {r["sheet_name"]: r["sheet_state"] for r in results["sheets"]}
        # Hidden sheets are still converted — Approach C indexes everything.
        self.assertEqual(len(by_name), 3)
        self.assertEqual(by_name["Scratch"], "hidden")

    def test_read_only_mode_still_exposes_sheet_state(self):
        # openpyxl-upgrade guard: ReadOnlyWorksheet must keep carrying
        # sheet_state, or visibility silently becomes "visible" everywhere.
        wb = openpyxl.load_workbook(self.path, data_only=True, read_only=True)
        try:
            states = {ws.title: ws.sheet_state for ws in wb.worksheets}
        finally:
            wb.close()
        self.assertEqual(states["Scratch"], "hidden")
        self.assertEqual(states["Internal"], "veryHidden")

    def test_end_to_end_capture_to_routing(self):
        # The full local chain: workbook -> process_workbook -> the
        # classifications shape the indexer persists -> routing.
        results = self._converter().process_workbook(self.path, self.out_dir)
        classifications = {
            r["sheet_name"]: {
                "sheet_type": "commission_data",
                "confidence": None,
                "visibility": r["sheet_state"],
            }
            for r in results["sheets"]
        }
        self.assertEqual(_route(classifications, list(classifications)), ["Commission"])


class ExplicitSheetNamesTests(TestCase):
    """`process_workbook(sheet_names=[...])` is the mechanism, not the policy —
    an explicit request is still honoured, and it still reports the tab state."""

    def test_explicit_hidden_sheet_is_still_converted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "book.xlsx")
            wb = openpyxl.Workbook()
            wb.active.title = "Visible"
            wb.active["A1"] = "x"
            hidden = wb.create_sheet("Hidden")
            hidden["A1"] = "y"
            hidden.sheet_state = "hidden"
            wb.save(path)

            converter = ExcelToCSVConverter(
                output_dir=Path(tmp) / "out",
                fill_empty="",
                enable_classification=False,
            )
            results = converter.process_workbook(
                path, Path(tmp) / "out", sheet_names=["Hidden"]
            )
            self.assertEqual(results["num_sheets"], 1)
            self.assertEqual(results["sheets"][0]["sheet_state"], "hidden")


class MetadataShapeTests(TestCase):
    """The persisted `sheet_classifications` entry must exist for every sheet,
    classified or not — an unclassified sheet with no entry has nowhere to hang
    its visibility and reads as visible."""

    def test_unclassified_sheet_still_carries_visibility(self):
        classifications = {
            "Grid": {"sheet_type": None, "confidence": None, "visibility": "hidden"}
        }
        self.assertEqual(hidden_sheet_names(classifications), ["Grid"])
        self.assertEqual(_route(classifications, ["Grid"]), [])

    def test_entry_shape_is_json_serialisable(self):
        # It is persisted verbatim into IndexedContent.sheet_metadata (JSONField).
        entry = {"sheet_type": None, "confidence": 0.87, "visibility": "veryHidden"}
        self.assertEqual(json.loads(json.dumps(entry)), entry)


class RolloutFlagTests(DjangoTestCase):
    """`CINS_SKIP_HIDDEN_SHEETS=false` restores pre-change behaviour without a
    redeploy.

    Exercises the real `skip_hidden_sheets_enabled` body — both the Django
    settings branch and the environment fallback. Stubbing the function itself
    would leave the branch that actually ships untested.
    """

    @override_settings(CINS_SKIP_HIDDEN_SHEETS=False)
    def test_flag_off_routes_hidden_sheets_again(self):
        classifications = {
            "Scratch": _cls("commission_data", "hidden"),
            "Legend": _cls("master_data", "veryHidden"),
        }
        self.assertTrue(is_sheet_visible(classifications["Scratch"]))
        self.assertEqual(hidden_sheet_names(classifications), [])
        self.assertEqual(_route(classifications, ["Scratch", "Legend"]), ["Scratch"])

    @override_settings(CINS_SKIP_HIDDEN_SHEETS=True)
    def test_flag_on_is_the_default_behaviour(self):
        classifications = {"Scratch": _cls("commission_data", "hidden")}
        self.assertEqual(hidden_sheet_names(classifications), ["Scratch"])
        self.assertEqual(_route(classifications, ["Scratch"]), [])

    def test_settings_default_is_on(self):
        from django.conf import settings

        self.assertTrue(getattr(settings, "CINS_SKIP_HIDDEN_SHEETS"))

    def test_env_fallback_parsing_when_django_is_unavailable(self):
        # The CLI path runs outside a configured Django. Force the except branch
        # by making the settings import raise, then check each accepted spelling.
        from cins.processors.core import sheet_visibility

        def _boom(*_a, **_k):
            raise ImportError("no django here")

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "django.conf":
                _boom()
            return real_import(name, *args, **kwargs)

        original_env = os.environ.get("CINS_SKIP_HIDDEN_SHEETS")
        builtins.__import__ = _fake_import
        try:
            for raw, expected in [
                ("false", False),
                ("FALSE", False),
                ("0", False),
                ("no", False),
                ("off", False),
                ("true", True),
                ("1", True),
                ("", True),
                ("anything", True),
            ]:
                os.environ["CINS_SKIP_HIDDEN_SHEETS"] = raw
                self.assertEqual(
                    sheet_visibility.skip_hidden_sheets_enabled(),
                    expected,
                    f"env value {raw!r} parsed wrong",
                )
            os.environ.pop("CINS_SKIP_HIDDEN_SHEETS", None)
            self.assertTrue(sheet_visibility.skip_hidden_sheets_enabled())
        finally:
            builtins.__import__ = real_import
            if original_env is None:
                os.environ.pop("CINS_SKIP_HIDDEN_SHEETS", None)
            else:
                os.environ["CINS_SKIP_HIDDEN_SHEETS"] = original_env


class XlsbVisibilityLoggingTests(TestCase):
    """`.xlsb` visibility could not be verified up front — LibreOffice reads the
    format but cannot write it, so no fixture can be built. The conversion logs
    the tab states it actually produced, which is what will settle the question
    the first time a real `.xlsb` with a hidden tab comes through."""

    def _log_for(self, states):
        from cins.processors.extractors.convert_xlsb import (
            _log_converted_sheet_visibility,
        )

        with tempfile.TemporaryDirectory() as tmp:
            wb = openpyxl.Workbook()
            wb.active.title = "Grid"
            wb.active["A1"] = "x"
            for name, state in states.items():
                ws = wb.create_sheet(name)
                ws["A1"] = "y"
                ws.sheet_state = state
            path = Path(tmp) / "converted.xlsx"
            wb.save(path)
            with self.assertLogs("cins.processors.extractors.convert_xlsb") as captured:
                _log_converted_sheet_visibility(path)
        return " ".join(captured.output)

    def test_preserved_visibility_is_reported_as_evidence(self):
        message = self._log_for({"Scratch": "hidden"})
        self.assertIn("preserved sheet visibility", message)
        self.assertIn("Scratch (hidden)", message)

    def test_all_visible_names_the_ambiguity_instead_of_claiming_success(self):
        message = self._log_for({})
        self.assertIn("all reported visible", message)
        self.assertIn("LibreOffice dropped", message)

    def test_unreadable_file_never_breaks_the_conversion(self):
        from cins.processors.extractors.convert_xlsb import (
            _log_converted_sheet_visibility,
        )

        with tempfile.TemporaryDirectory() as tmp:
            bogus = Path(tmp) / "not-a-workbook.xlsx"
            bogus.write_text("definitely not a zip")
            _log_converted_sheet_visibility(bogus)  # must not raise


class IndexerProducerTests(TestCase):
    """`_index_excel` is where visibility is written. Nothing downstream can be
    right if the producer is wrong, and it is the only place the Pass 0B/0C
    LLM-call savings can be observed."""

    def _fixture(self, tmp):
        wb = openpyxl.Workbook()
        grid = wb.active
        grid.title = "Grid"
        grid.append(["Product", "Payout"])
        grid.append(["CAR", "20%"])
        old = wb.create_sheet("Old Grid")
        old.append(["Product", "Payout"])
        old.append(["CAR", "99%"])
        old.sheet_state = "hidden"
        legend = wb.create_sheet("Legend")
        legend.append(["Code", "Meaning"])
        legend.append(["RTO1", "SECRET_LEGEND"])
        legend.sheet_state = "veryHidden"
        path = Path(tmp) / "book.xlsx"
        wb.save(path)
        return str(path)

    def _index(self, tmp, path, prefix="", enable_cleaning=False):
        """Run the real _index_excel with only the LLM classifier stubbed."""
        import cins.processors.extractors.excel_to_csv as e2c
        from cins.processors.content.indexer import ContentIndexer

        calls = {"classify": [], "clean": [], "tables": []}

        class _FakeClassification:
            def __init__(self, sheet_type):
                self.sheet_type = sheet_type

            def to_dict(self):
                return {"sheet_type": self.sheet_type, "confidence": 0.9}

        class _FakeClassifier:
            def classify_sheet(self, sheet_name, csv_preview, metadata):
                calls["classify"].append(sheet_name)
                if "Legend" in sheet_name:
                    return _FakeClassification("master_data")
                return _FakeClassification("commission_data")

        real_classifier, real_available = e2c.SheetClassifier, e2c.CLASSIFIER_AVAILABLE
        e2c.SheetClassifier, e2c.CLASSIFIER_AVAILABLE = _FakeClassifier, True

        indexer = ContentIndexer(
            indexed_dir=str(Path(tmp) / "idx"), enable_cleaning=enable_cleaning
        )
        # Pass 0B/0C must never fire for a hidden sheet. Record any attempt.
        indexer._get_llm_service = lambda: object()
        import cins.processors.content.indexer as idx_mod

        real_clean, real_detect = (
            idx_mod.clean_master_data_content,
            idx_mod.detect_csv_tables,
        )
        idx_mod.clean_master_data_content = lambda c, s: (
            calls["clean"].append(c) or (c, {})
        )
        idx_mod.detect_csv_tables = lambda c, name, s, header_row_count=0: (
            calls["tables"].append(name) or ([], {"fallback": True})
        )
        out = Path(tmp) / f"out{prefix or 'x'}"
        out.mkdir()
        try:
            meta = indexer._index_excel(path, out, sheet_name_prefix=prefix)
        finally:
            e2c.SheetClassifier, e2c.CLASSIFIER_AVAILABLE = (
                real_classifier,
                real_available,
            )
            idx_mod.clean_master_data_content = real_clean
            idx_mod.detect_csv_tables = real_detect
        return meta, calls

    def test_every_sheet_gets_a_visibility_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta, _ = self._index(tmp, self._fixture(tmp))
            classifications = meta["sheet_classifications"]
            self.assertEqual(
                {n: c["visibility"] for n, c in classifications.items()},
                {"Grid": "visible", "Old Grid": "hidden", "Legend": "veryHidden"},
            )

    def test_hidden_sheets_are_still_indexed_and_classified(self):
        # Approach C: excluded from extraction, never dropped from the index.
        with tempfile.TemporaryDirectory() as tmp:
            meta, calls = self._index(tmp, self._fixture(tmp))
            self.assertEqual(sorted(calls["classify"]), ["Grid", "Legend", "Old Grid"])
            self.assertEqual(len(meta["content_files"]["sheets"]), 3)

    def test_pass_0b_0c_never_run_for_hidden_sheets(self):
        with tempfile.TemporaryDirectory() as tmp:
            _meta, calls = self._index(tmp, self._fixture(tmp), enable_cleaning=True)
            # Only the visible commission sheet may reach Pass 0C, and the
            # hidden master sheet must never reach Pass 0B.
            self.assertEqual(calls["tables"], ["Grid"])
            self.assertEqual(calls["clean"], [])

    def test_unclassified_sheet_still_carries_visibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._fixture(tmp)
            import cins.processors.extractors.excel_to_csv as e2c
            from cins.processors.content.indexer import ContentIndexer

            class _Failing:
                def classify_sheet(self, **_kw):
                    raise RuntimeError("classifier down")

                def __call__(self, *a, **k):
                    return self

            real_cls, real_avail = e2c.SheetClassifier, e2c.CLASSIFIER_AVAILABLE
            e2c.SheetClassifier, e2c.CLASSIFIER_AVAILABLE = _Failing, True
            out = Path(tmp) / "out_unclassified"
            out.mkdir()
            try:
                meta = ContentIndexer(
                    indexed_dir=str(Path(tmp) / "idx2"), enable_cleaning=False
                )._index_excel(path, out)
            finally:
                e2c.SheetClassifier, e2c.CLASSIFIER_AVAILABLE = real_cls, real_avail

            entry = meta["sheet_classifications"]["Old Grid"]
            self.assertIsNone(entry["sheet_type"])
            self.assertEqual(entry["visibility"], "hidden")
            # An unclassified hidden sheet must still be excluded from routing.
            self.assertEqual(
                _route(
                    meta["sheet_classifications"], list(meta["sheet_classifications"])
                ),
                ["Grid"],
            )

    def test_prefixed_keys_for_multi_attachment_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta, _ = self._index(tmp, self._fixture(tmp), prefix="book_a__")
            self.assertEqual(
                {n: c["visibility"] for n, c in meta["sheet_classifications"].items()},
                {
                    "book_a__Grid": "visible",
                    "book_a__Old Grid": "hidden",
                    "book_a__Legend": "veryHidden",
                },
            )

    def test_indexer_version_marks_visibility_support(self):
        # The frontend gates the whole coverage surface on this value. Visibility
        # support landed in 1.4; 1.5 adds per-table rate_cell_count for the codegen
        # completeness gate — both carry the visibility marker, so assert >= 1.4.
        from cins.processors.content.indexer import INDEXER_VERSION

        self.assertGreaterEqual(tuple(map(int, INDEXER_VERSION.split("."))), (1, 4))


class AllHiddenTerminalTests(TestCase):
    """The routed set coming back empty must fail loudly. A green zero-rule
    'success' is the one outcome this change exists to prevent."""

    def test_process_sheets_raises_when_everything_is_hidden(self):
        from cins.processors.core.pipeline import Pipeline

        classifications = {
            "Grid": _cls("commission_data", "hidden"),
            "Legend": _cls("master_data", "veryHidden"),
        }
        doc = _indexed_doc(classifications, {"Grid": "rows", "Legend": "rows"})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx:
                Pipeline(llm_provider="gemini", output_dir=None)._process_sheets(
                    doc, Path(tmp) / "cics.json"
                )
        message = str(ctx.exception)
        self.assertIn("all 2 sheet(s)", message)
        self.assertIn("Grid", message)
        self.assertIn("Legend", message)

    def test_process_sheets_does_not_raise_for_an_empty_document(self):
        # No sheets at all is a different condition — not "everything hidden".
        from cins.processors.core.pipeline import Pipeline

        doc = _indexed_doc({}, {})
        with tempfile.TemporaryDirectory() as tmp:
            result = Pipeline(llm_provider="gemini", output_dir=None)._process_sheets(
                doc, Path(tmp) / "cics.json"
            )
        self.assertEqual(result.sheet_results, [])
