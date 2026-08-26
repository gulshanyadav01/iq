"""
Tests for the CICS Excel export renderer (CPA rows + column placement).

The CPA-deduction split now happens at transform time
(cins.processors.core.cpa_split), so the export is a pure renderer: one row per
rule. These tests feed an already-split cics_doc and assert the workbook renders
the CPA labels and routes each rule's percentage to the column its applied_on
basis names.

Run from the backend container:
    python manage.py test commission_intake.tests.test_export_cpa_split -v 2
"""

import io

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from openpyxl import load_workbook
from rest_framework.test import APIClient

from commission_intake.models import CommissionDocument, CICSResult

OVERRIDE_OD = {
    "clause_type": "applied_on_override",
    "text": "Package pay-out is on OD Cover Premium",
    "when": {"coverage_type_text": "comprehensive"},
    "effect": {"applied_on": "OD Cover Premium"},
}
OVERRIDE_TP = {
    "clause_type": "applied_on_override",
    "text": "Liability pay-out is on TP/PA Premium",
    "when": {"coverage_type_text": "third party"},
    "effect": {"applied_on": "TP/PA Premium"},
}


def _rule(cpa, pct, clauses=None, policy_type=None):
    conditions = {"deal_parameter": "net", "cpa": cpa}
    if policy_type is not None:
        conditions["policy_type"] = policy_type
    outcome = {
        "commission_type": "percentage",
        "percentage_value": pct,
        "applied_on": "Net Premium",
    }
    if clauses is not None:
        outcome["qualifying_clauses"] = clauses
    return {"rule_id": "r", "conditions": conditions, "outcome": outcome}


def _doc(rules):
    return {
        "provider": {"provider_name": "Acme"},
        "products": [
            {
                "product_name": "Private Car",
                "sub_category": "Package",
                "commission_rules": rules,
            }
        ],
    }


class _ExportHelpers:
    """Auth + export + worksheet-reading helpers, shared by the classes below.

    A mixin rather than a base test class: subclassing a TestCase re-runs every
    one of its tests under the subclass's name.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="t@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _export(self, cics_doc):
        doc = CommissionDocument.objects.create(original_filename="grid.xlsx")
        CICSResult.objects.create(document=doc, cics_doc=cics_doc)
        url = reverse("commission-documents-export-excel", args=[doc.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))
        return load_workbook(io.BytesIO(b"".join(resp.streaming_content)))

    @staticmethod
    def _cols(ws):
        return {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}

    def _rows(self, ws):
        return list(ws.iter_rows(min_row=2, values_only=True))


class ExportRendererTests(_ExportHelpers, DjangoTestCase):
    def test_one_row_per_rule_no_export_side_split(self):
        # Two already-split rules + one plain rule => exactly 3 rows.
        wb = self._export(
            _doc(
                [
                    _rule("with-cpa", 30, [OVERRIDE_OD], ["comprehensive"]),
                    _rule("without-cpa", 28.5, [OVERRIDE_OD], ["comprehensive"]),
                    _rule(None, 10, None, ["comprehensive"]),
                ]
            )
        )
        ws = wb["Commission Rules"]
        self.assertEqual(len(self._rows(ws)), 3)

    def test_cpa_labels_rendered(self):
        wb = self._export(
            _doc(
                [
                    _rule("with-cpa", 30, [OVERRIDE_OD], ["comprehensive"]),
                    _rule("without-cpa", 28.5, [OVERRIDE_OD], ["comprehensive"]),
                    _rule(None, 10),
                ]
            )
        )
        ws = wb["Commission Rules"]
        cols = self._cols(ws)
        labels = [r[cols["CPA"] - 1] for r in self._rows(ws)]
        self.assertEqual(labels, ["With CPA", "Without CPA", "With CPA,Without CPA"])

    def test_comprehensive_routes_to_od_column(self):
        wb = self._export(
            _doc(
                [
                    _rule("with-cpa", 30, [OVERRIDE_OD], ["comprehensive"]),
                    _rule("without-cpa", 28.5, [OVERRIDE_OD], ["comprehensive"]),
                ]
            )
        )
        ws = wb["Commission Rules"]
        cols = self._cols(ws)
        od = {r[cols["CPA"] - 1]: r[cols["ODPercentage"] - 1] for r in self._rows(ws)}
        self.assertEqual(od["With CPA"], 30)
        self.assertEqual(od["Without CPA"], 28.5)
        # Net column is 0 for OD-based rows — the carrier's own deal records
        # carry 0 in every column a deal does not pay on, never a blank.
        net = [r[cols["NetPercentage"] - 1] for r in self._rows(ws)]
        self.assertTrue(all(v == 0 for v in net), net)

    def test_third_party_routes_to_tp_column(self):
        wb = self._export(
            _doc([_rule("without-cpa", 25, [OVERRIDE_TP], ["third-party"])])
        )
        ws = wb["Commission Rules"]
        cols = self._cols(ws)
        rows = self._rows(ws)
        self.assertEqual(rows[0][cols["TPPercentage"] - 1], 25)

    def test_third_party_split_pair_renders_two_rows_with_labels(self):
        # End-to-end proof of the reported bug's fix. A clause-carrying TP rule
        # used to reach the exporter cpa-unconstrained and render ONE row whose
        # CPA cell read the merged "With CPA,Without CPA" at the undeducted
        # rate. cpa_split now hands the exporter the two rules below.
        wb = self._export(
            _doc(
                [
                    _rule("with-cpa", 25, [OVERRIDE_TP], ["third-party"]),
                    _rule("without-cpa", 23.5, [OVERRIDE_TP], ["third-party"]),
                ]
            )
        )
        ws = wb["Commission Rules"]
        cols = self._cols(ws)
        rows = self._rows(ws)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [r[cols["CPA"] - 1] for r in rows], ["With CPA", "Without CPA"]
        )
        tp = {r[cols["CPA"] - 1]: r[cols["TPPercentage"] - 1] for r in rows}
        self.assertEqual(tp["With CPA"], 25)
        self.assertEqual(tp["Without CPA"], 23.5)
        # TP-based rows pay in neither OD nor Net.
        for key in ("ODPercentage", "NetPercentage"):
            self.assertTrue(all(r[cols[key] - 1] == 0 for r in rows), key)

    def test_net_rule_routes_to_net_column(self):
        wb = self._export(_doc([_rule(None, 12, None, ["comprehensive"])]))
        ws = wb["Commission Rules"]
        cols = self._cols(ws)
        rows = self._rows(ws)
        self.assertEqual(rows[0][cols["NetPercentage"] - 1], 12)


CPA_CLAUSE = {
    "clause_type": "conditional_deduction",
    "text": "if CPA not collected then 1.5% of Net Payout deducted",
    "when": {"cpa": "without-cpa"},
    "effect": {
        "deduction": {
            "value": 1.5,
            "value_type": "percentage",
            "applied_on": "Net Payout",
        }
    },
}


def _gcv_doc(rules):
    return {
        "provider": {"provider_id": "Chola", "provider_name": "Cholamandalam MS"},
        "products": [
            {
                "product_name": "Commercial Vehicle",
                "product_code": "COMMERCIAL_VEHICLE_GCV",
                "product_subproduct": "commercial-gcv",
                "sub_category": "GCV",
                "commission_rules": rules,
            }
        ],
    }


class ExportGapFilledCpaTests(_ExportHelpers, DjangoTestCase):
    """A note-less Chola GCV rule exports TWO rows, not one merged-label row.

    End-to-end: the transform-time gap-fill runs, then the pure renderer emits
    what it produced. Before the fix the second rule below rendered as a single
    "With CPA,Without CPA" row at the undeducted 10%.
    """

    def _split_and_export(self, cics_doc):
        from cins.processors.core.cpa_split import materialize_cpa_split

        return self._export(materialize_cpa_split(cics_doc))

    def test_gap_filled_rule_exports_two_rows_at_deducted_rate(self):
        wb = self._split_and_export(
            _gcv_doc(
                [
                    _rule(None, 20, [CPA_CLAUSE], ["comprehensive"]),
                    # No qualifying_clauses, no other_conditions: the gap.
                    _rule(None, 10, None, ["comprehensive"]),
                ]
            )
        )
        ws = wb["Commission Rules"]
        cols = self._cols(ws)
        rows = self._rows(ws)
        self.assertEqual(len(rows), 4, "gap-filled rule did not bifurcate")
        self.assertNotIn(
            "With CPA,Without CPA",
            [r[cols["CPA"] - 1] for r in rows],
            "a merged-label row survived the gap-fill",
        )
        net = cols["NetPercentage"] - 1
        cpa = cols["CPA"] - 1
        pairs = {(r[cpa], r[net]) for r in rows}
        self.assertIn(("With CPA", 10), pairs)
        self.assertIn(("Without CPA", 8.5), pairs)

    def test_rule_with_unrelated_notes_still_exports_one_merged_row(self):
        gap = _rule(None, 45, None, ["comprehensive"])
        gap["outcome"]["other_conditions"] = (
            "Private Car Comp ... this sheet is just to identify the potential products"
        )
        wb = self._split_and_export(
            _gcv_doc([_rule(None, 20, [CPA_CLAUSE], ["comprehensive"]), gap])
        )
        ws = wb["Commission Rules"]
        cols = self._cols(ws)
        rows = self._rows(ws)
        labels = [r[cols["CPA"] - 1] for r in rows]
        self.assertEqual(labels.count("With CPA,Without CPA"), 1)
        self.assertEqual(len(rows), 3)
