"""
Tests for the two API surfaces that hidden-sheet exclusion touches.

1. `rerun-pass` must refuse a sheet the pipeline deliberately excluded. The UI
   now lists skipped sheets by name, so without this an operator (or a script)
   can POST a hidden sheet name and get a full three-pass extraction of content
   the pipeline is designed never to read.

2. `export-excel` must carry the coverage summary. Reconciliation happens in
   Excel, away from the app, so the coverage strip on the detail page is
   invisible at exactly the moment someone asks "why is this row missing?".

Run from the backend container:
    python manage.py test commission_intake.tests.test_hidden_sheet_api -v 2
"""

import io

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook
from rest_framework.test import APIClient

from commission_intake.models import CICSResult, CommissionDocument, IndexedContent

SHEET_CONTENTS = {
    "Grid": "| Product | Payout |\n| --- | --- |\n| CAR | 20% |",
    "Old Grid": "| Product | Payout |\n| --- | --- |\n| CAR | 99% |",
    "Legend": "| Code | Meaning |\n| --- | --- |\n| RTO1 | Chennai |",
}

CLASSIFICATIONS = {
    "Grid": {
        "sheet_type": "commission_data",
        "confidence": 0.9,
        "visibility": "visible",
    },
    "Old Grid": {
        "sheet_type": "commission_data",
        "confidence": 0.9,
        "visibility": "hidden",
    },
    "Legend": {"sheet_type": "master_data", "confidence": 0.9, "visibility": "visible"},
}

# A visible sheet the classifier could not label. Routing skips it whenever any
# commission sheet exists, so the coverage sheet must NOT call it extracted.
UNCLASSIFIED_SHEET_CONTENTS = dict(
    SHEET_CONTENTS, Notes="| Note |\n| --- |\n| read me |"
)
UNCLASSIFIED_CLASSIFICATIONS = dict(
    CLASSIFICATIONS,
    Notes={"sheet_type": None, "confidence": None, "visibility": "visible"},
)

CICS_DOC = {
    "provider": {"provider_name": "Acme"},
    "products": [
        {
            "product_name": "Private Car",
            "sub_category": "Package",
            "commission_rules": [
                {
                    "rule_id": "r1",
                    "conditions": {"deal_parameter": "net"},
                    "outcome": {
                        "commission_type": "percentage",
                        "percentage_value": 20,
                        "applied_on": "Net Premium",
                    },
                }
            ],
        }
    ],
}


def _all_text(ws):
    return " ".join(
        str(c.value) for row in ws.iter_rows() for c in row if c.value is not None
    )


class HiddenSheetApiTests(DjangoTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="t@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.doc = CommissionDocument.objects.create(
            original_filename="grid.xlsx",
            status=CommissionDocument.Status.COMPLETED,
        )
        IndexedContent.objects.create(
            document=self.doc,
            source_type="excel",
            sheet_contents=SHEET_CONTENTS,
            sheet_metadata={"classifications": CLASSIFICATIONS, "source_metadata": {}},
            indexer_version="1.4",
        )

    def _rerun(self, sheet_name):
        return self.client.post(
            reverse("commission-documents-rerun-pass", args=[self.doc.id]),
            {"pass_number": 3, "sheet_name": sheet_name, "attachment_name": ""},
            format="json",
        )

    def test_rerun_rejects_a_hidden_sheet(self):
        resp = self._rerun("Old Grid")
        self.assertEqual(resp.status_code, 400, getattr(resp, "data", resp))
        self.assertIn("hidden", str(resp.data["detail"]).lower())

    def test_rerun_rejects_a_sheet_excluded_by_classification(self):
        # Master data is visible but not routed — the same guard covers it.
        resp = self._rerun("Legend")
        self.assertEqual(resp.status_code, 400)

    def test_rerun_accepts_a_routed_sheet(self):
        self.assertEqual(self._rerun("Grid").status_code, 200)

    def _coverage_sheet(self):
        CICSResult.objects.create(document=self.doc, cics_doc=CICS_DOC)
        resp = self.client.get(
            reverse("commission-documents-export-excel", args=[self.doc.id])
        )
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))
        wb = load_workbook(io.BytesIO(b"".join(resp.streaming_content)))
        self.assertIn("Sheet Coverage", wb.sheetnames)
        return wb["Sheet Coverage"]

    def test_export_names_every_sheet_and_its_fate(self):
        used_for = {
            row[0]: row[3]
            for row in self._coverage_sheet().iter_rows(min_row=2, values_only=True)
            if row[0]
        }
        self.assertEqual(used_for["Grid"], "Extracted")
        self.assertEqual(used_for["Old Grid"], "Skipped — hidden sheet")
        self.assertEqual(used_for["Legend"], "Reference only (master data)")

    def test_export_carries_a_coverage_summary_line(self):
        text = _all_text(self._coverage_sheet())
        self.assertIn("1 of 3 sheet(s) extracted", text)
        self.assertIn("1 hidden sheet(s) skipped", text)

    def test_export_records_the_raw_tab_state(self):
        states = {
            row[0]: row[2]
            for row in self._coverage_sheet().iter_rows(min_row=2, values_only=True)
            if row[0]
        }
        self.assertEqual(states["Old Grid"], "hidden")
        self.assertEqual(states["Grid"], "visible")

    def test_rerun_rejection_names_the_real_reason(self):
        # A visible master_data sheet is also outside the routed set. Telling
        # its owner "hidden sheets are excluded" sends them hunting for a hidden
        # tab that does not exist.
        detail = str(self._rerun("Legend").data["detail"])
        self.assertIn("master_data", detail)
        self.assertNotIn("hidden", detail.lower())

        hidden_detail = str(self._rerun("Old Grid").data["detail"])
        self.assertIn("hidden", hidden_detail.lower())

    def test_rerun_rejection_explains_an_indicative_sheet_in_its_own_terms(self):
        # "the pipeline only extracts commission sheets" is true here but
        # unhelpful, because this sheet visibly HAS rates. The useful fact is
        # that the sheet itself disclaims them.
        IndexedContent.objects.filter(document=self.doc).update(
            sheet_contents={**SHEET_CONTENTS, "hero cells": "rows"},
            sheet_metadata={
                "classifications": {
                    **CLASSIFICATIONS,
                    "hero cells": {
                        "sheet_type": "indicative_data",
                        "visibility": "visible",
                    },
                },
                "source_metadata": {},
            },
        )
        detail = str(self._rerun("hero cells").data["detail"])
        self.assertIn("not the authoritative payout grid", detail)
        self.assertNotIn("hidden", detail.lower())

    @override_settings(CINS_SKIP_HIDDEN_SHEETS=False)
    def test_rollback_flag_lifts_the_rerun_rejection(self):
        # The flag is documented as restoring pre-change behaviour without a
        # redeploy, so it has to lift the API rejection too.
        self.assertEqual(self._rerun("Old Grid").status_code, 200)

    @override_settings(CINS_SKIP_HIDDEN_SHEETS=False)
    def test_api_reports_the_flag_so_the_ui_cannot_diverge(self):
        resp = self.client.get(
            reverse("commission-documents-detail", args=[self.doc.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIs(resp.data["indexed_content"]["hidden_sheets_excluded"], False)

    def test_api_reports_the_flag_on_by_default(self):
        resp = self.client.get(
            reverse("commission-documents-detail", args=[self.doc.id])
        )
        self.assertIs(resp.data["indexed_content"]["hidden_sheets_excluded"], True)

    def test_export_neutralises_formula_injection_in_sheet_names(self):
        # Sheet names come from an uploaded workbook. openpyxl types a leading
        # "=" as a formula, which would run in the analyst's desktop Excel.
        doc = CommissionDocument.objects.create(
            original_filename="evil.xlsx", status=CommissionDocument.Status.COMPLETED
        )
        IndexedContent.objects.create(
            document=doc,
            source_type="excel",
            sheet_contents={"=1+1": "rows"},
            sheet_metadata={
                "classifications": {
                    "=1+1": {"sheet_type": "commission_data", "visibility": "visible"}
                },
                "source_metadata": {},
            },
            indexer_version="1.4",
        )
        CICSResult.objects.create(document=doc, cics_doc=CICS_DOC)
        resp = self.client.get(
            reverse("commission-documents-export-excel", args=[doc.id])
        )
        ws = load_workbook(io.BytesIO(b"".join(resp.streaming_content)))[
            "Sheet Coverage"
        ]
        cell = ws.cell(row=2, column=1)
        self.assertEqual(cell.value, "=1+1")
        self.assertEqual(cell.data_type, "s")  # string, not TYPE_FORMULA

    def test_rerun_allows_a_document_with_no_sheets(self):
        # PDF / single-content documents post an empty sheet_name. The routed-set
        # guard must not fire for them or every PDF rerun 400s.
        doc = CommissionDocument.objects.create(
            original_filename="statement.pdf",
            status=CommissionDocument.Status.COMPLETED,
        )
        IndexedContent.objects.create(
            document=doc,
            source_type="pdf",
            primary_content="some text",
            sheet_contents={},
            sheet_metadata={},
            indexer_version="1.4",
        )
        resp = self.client.post(
            reverse("commission-documents-rerun-pass", args=[doc.id]),
            {"pass_number": 3, "sheet_name": "", "attachment_name": ""},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))


class CoverageAccuracyTests(DjangoTestCase):
    """REGRESSION: the coverage sheet must report what routing actually did.

    `derive_pipeline_routing` processes ONLY commission sheets when any exist,
    so a visible unclassified sheet is not extracted. An earlier version of
    `_write_coverage_sheet` derived the fate from classification labels and
    counted that sheet as "Extracted (unclassified)", reporting "3 of 4
    extracted" for a document that extracted 1 — the exact false confidence the
    coverage sheet is supposed to prevent.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="cov@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _coverage(self, sheet_contents, classifications, source_type="excel"):
        doc = CommissionDocument.objects.create(
            original_filename="grid.xlsx",
            status=CommissionDocument.Status.COMPLETED,
        )
        IndexedContent.objects.create(
            document=doc,
            source_type=source_type,
            sheet_contents=sheet_contents,
            sheet_metadata={
                "classifications": classifications,
                "source_metadata": {},
            },
            indexer_version="1.4",
        )
        CICSResult.objects.create(document=doc, cics_doc=CICS_DOC)
        resp = self.client.get(
            reverse("commission-documents-export-excel", args=[doc.id])
        )
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))
        return load_workbook(io.BytesIO(b"".join(resp.streaming_content)))[
            "Sheet Coverage"
        ]

    @staticmethod
    def _fates(ws):
        return {
            row[0]: row[3]
            for row in ws.iter_rows(min_row=2, values_only=True)
            if row[0] and row[0] != "Summary"
        }

    def test_unclassified_sheet_is_not_reported_as_extracted(self):
        ws = self._coverage(UNCLASSIFIED_SHEET_CONTENTS, UNCLASSIFIED_CLASSIFICATIONS)
        fates = self._fates(ws)
        self.assertEqual(fates["Grid"], "Extracted")
        self.assertEqual(fates["Notes"], "Not extracted (unclassified)")
        self.assertEqual(fates["Legend"], "Reference only (master data)")
        self.assertEqual(fates["Old Grid"], "Skipped — hidden sheet")
        self.assertIn("1 of 4 sheet(s) extracted", _all_text(ws))

    def test_fallback_reports_master_sheets_as_extracted(self):
        # No commission sheet at all -> routing falls back to every VISIBLE
        # sheet, so the master sheet really is extracted. Calling it
        # "reference only" here would under-report just as badly.
        ws = self._coverage(
            {"Legend": "rows", "Old Grid": "rows"},
            {
                "Legend": {"sheet_type": "master_data", "visibility": "visible"},
                "Old Grid": {"sheet_type": "master_data", "visibility": "hidden"},
            },
        )
        fates = self._fates(ws)
        self.assertEqual(fates["Legend"], "Extracted")
        self.assertEqual(fates["Old Grid"], "Skipped — hidden sheet")
        self.assertIn("1 of 2 sheet(s) extracted", _all_text(ws))

    def test_indicative_sheet_is_labelled_as_such_not_as_unclassified(self):
        # "Not extracted (unclassified)" reads as a pipeline failure. This is a
        # deliberate skip and the cell has to say so.
        ws = self._coverage(
            {"Grid": "rows", "hero cells": "rows"},
            {
                "Grid": {"sheet_type": "commission_data", "visibility": "visible"},
                "hero cells": {
                    "sheet_type": "indicative_data",
                    "visibility": "visible",
                },
            },
        )
        fates = self._fates(ws)
        self.assertEqual(fates["Grid"], "Extracted")
        self.assertEqual(
            fates["hero cells"],
            "Not extracted — indicative grid (sheet states it is not the payout grid)",
        )
        self.assertIn("1 of 2 sheet(s) extracted", _all_text(ws))

    def test_fallback_does_not_report_an_indicative_sheet_as_extracted(self):
        # The mirror of test_fallback_reports_master_sheets_as_extracted, and
        # the opposite answer. With no commission sheet, routing falls back —
        # but `routable_sheet_names` subtracts indicative sheets, so this one is
        # genuinely NOT extracted. Reporting it as extracted would be the
        # inversion the routing guard exists to prevent.
        ws = self._coverage(
            {"hero cells": "rows", "Legend": "rows"},
            {
                "hero cells": {
                    "sheet_type": "indicative_data",
                    "visibility": "visible",
                },
                "Legend": {"sheet_type": "master_data", "visibility": "visible"},
            },
        )
        fates = self._fates(ws)
        self.assertEqual(fates["Legend"], "Extracted")
        self.assertNotEqual(fates["hero cells"], "Extracted")

    def test_export_omits_the_coverage_sheet_when_there_are_no_sheets(self):
        doc = CommissionDocument.objects.create(
            original_filename="statement.pdf",
            status=CommissionDocument.Status.COMPLETED,
        )
        IndexedContent.objects.create(
            document=doc,
            source_type="pdf",
            sheet_contents={},
            sheet_metadata={},
            indexer_version="1.4",
        )
        CICSResult.objects.create(document=doc, cics_doc=CICS_DOC)
        resp = self.client.get(
            reverse("commission-documents-export-excel", args=[doc.id])
        )
        self.assertEqual(resp.status_code, 200)
        wb = load_workbook(io.BytesIO(b"".join(resp.streaming_content)))
        self.assertNotIn("Sheet Coverage", wb.sheetnames)

    def test_export_succeeds_when_the_document_was_never_indexed(self):
        doc = CommissionDocument.objects.create(
            original_filename="orphan.xlsx",
            status=CommissionDocument.Status.COMPLETED,
        )
        CICSResult.objects.create(document=doc, cics_doc=CICS_DOC)
        resp = self.client.get(
            reverse("commission-documents-export-excel", args=[doc.id])
        )
        self.assertEqual(resp.status_code, 200)


class RerunSinglePassTests(DjangoTestCase):
    """`rerun_single_pass` builds its OWN master_context, bypassing the pipeline.

    A helper-level parity assertion cannot cover this: it would pass even if the
    task still used its old local comprehension. This executes the task body and
    inspects the prompt it actually hands to the LLM.
    """

    def _run(self, classifications, sheet_contents, pass_number=1, sheet="Grid"):
        from unittest.mock import patch

        from commission_intake.models import ExtractionPass
        from commission_intake import tasks as intake_tasks

        doc = CommissionDocument.objects.create(original_filename="grid.xlsx")
        IndexedContent.objects.create(
            document=doc,
            source_type="excel",
            sheet_contents=sheet_contents,
            sheet_metadata={"classifications": classifications, "source_metadata": {}},
            indexer_version="1.4",
        )
        ExtractionPass.objects.create(
            document=doc, pass_number=pass_number, sheet_name=sheet, attachment_name=""
        )

        captured = {}

        def _fake_build(**kwargs):
            captured.update(kwargs)
            return ("p1", "p2", "p3", False)

        with (
            patch(
                "cins.processors.core.pipeline.build_pass_contents",
                side_effect=_fake_build,
            ),
            patch.object(intake_tasks, "get_llm_service", create=True),
            patch("cins.processors.core.llm_extract._extract_pass1_metadata"),
        ):
            try:
                intake_tasks.rerun_single_pass(str(doc.id), pass_number, sheet, "")
            except Exception:
                # The LLM half is stubbed; we only care about the prompt inputs
                # the task assembled before it got there.
                pass
        return captured

    def test_hidden_master_sheet_never_reaches_the_rerun_prompt(self):
        captured = self._run(
            {
                "Grid": {"sheet_type": "commission_data", "visibility": "visible"},
                "Old Legend": {"sheet_type": "master_data", "visibility": "hidden"},
            },
            {"Grid": "grid rows", "Old Legend": "SECRET_RTO_TABLE"},
        )
        self.assertIn("master_context", captured)
        self.assertNotIn("SECRET_RTO_TABLE", captured["master_context"])

    def test_visible_master_sheet_still_reaches_the_rerun_prompt(self):
        captured = self._run(
            {
                "Grid": {"sheet_type": "commission_data", "visibility": "visible"},
                "Legend": {"sheet_type": "master_data", "visibility": "visible"},
            },
            {"Grid": "grid rows", "Legend": "VISIBLE_RTO_TABLE"},
        )
        self.assertIn("VISIBLE_RTO_TABLE", captured["master_context"])

    def test_hidden_commission_sheet_is_not_counted_as_a_commission_sheet(self):
        # commission_sheets drives email_is_primary_commission, which decides
        # whether Pass 3 reads the sheet or the email body.
        captured = self._run(
            {"Grid": {"sheet_type": "commission_data", "visibility": "hidden"}},
            {"Grid": "grid rows"},
        )
        self.assertEqual(captured.get("commission_sheets"), [])


class AllHiddenExtractionTests(DjangoTestCase):
    """An all-hidden workbook must fail the document, not complete it green
    with zero rules and zero ExtractionPass rows."""

    def test_run_extraction_fails_the_document(self):
        from commission_intake.models import ExtractionPass
        from commission_intake.tasks import run_extraction

        doc = CommissionDocument.objects.create(
            original_filename="allhidden.xlsx",
            status=CommissionDocument.Status.EXTRACTING,
        )
        IndexedContent.objects.create(
            document=doc,
            source_type="excel",
            sheet_contents={"Grid": "rows", "Legend": "rows"},
            sheet_metadata={
                "classifications": {
                    "Grid": {"sheet_type": "commission_data", "visibility": "hidden"},
                    "Legend": {"sheet_type": "master_data", "visibility": "veryHidden"},
                },
                "source_metadata": {},
            },
            indexer_version="1.4",
        )

        with self.assertRaises(Exception):
            run_extraction(str(doc.id))

        doc.refresh_from_db()
        self.assertEqual(doc.status, CommissionDocument.Status.FAILED)
        self.assertIn("hidden", (doc.error or "").lower())
        # No PENDING rows may be pre-created for sheets that will never run.
        self.assertEqual(ExtractionPass.objects.filter(document=doc).count(), 0)


class LegacyDocumentApiTests(DjangoTestCase):
    """A document indexed before visibility was recorded must behave exactly as
    it did before this change — absent data is not evidence of hiding."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="legacy@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.doc = CommissionDocument.objects.create(
            original_filename="legacy.xlsx",
            status=CommissionDocument.Status.COMPLETED,
        )
        IndexedContent.objects.create(
            document=self.doc,
            source_type="excel",
            sheet_contents={"Grid": "rows", "Legend": "rows"},
            sheet_metadata={
                "classifications": {
                    "Grid": {"sheet_type": "commission_data", "confidence": 0.9},
                    "Legend": {"sheet_type": "master_data", "confidence": 0.9},
                },
                "source_metadata": {},
            },
            indexer_version="1.3",
        )

    def test_rerun_still_accepts_the_routed_sheet(self):
        resp = self.client.post(
            reverse("commission-documents-rerun-pass", args=[self.doc.id]),
            {"pass_number": 3, "sheet_name": "Grid", "attachment_name": ""},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))

    def test_export_reports_no_hidden_sheets(self):
        CICSResult.objects.create(document=self.doc, cics_doc=CICS_DOC)
        resp = self.client.get(
            reverse("commission-documents-export-excel", args=[self.doc.id])
        )
        ws = load_workbook(io.BytesIO(b"".join(resp.streaming_content)))[
            "Sheet Coverage"
        ]
        self.assertNotIn("hidden sheet(s) skipped", _all_text(ws))
