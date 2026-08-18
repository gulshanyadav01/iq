"""
Export rendering of the CV/PCV -> Private Car taxi mirror.

The mirror is materialized at transform time (cins.processors.core
.pcv_taxi_mirror), so the export stays a pure renderer: it must emit BOTH the
Commercial Vehicle / PCV / taxi row and the Private Car / Taxi / taxi row, one
row per rule, and fill each one's RTO from its own product bucket (CV vs CAR).

Run from the backend container:
    python manage.py test commission_intake.tests.test_export_pcv_taxi_mirror -v 2
"""

import io

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from openpyxl import load_workbook
from rest_framework.test import APIClient

from cins.processors.core.pcv_taxi_mirror import materialize_pcv_taxi_mirror
from commission_intake.models import CICSResult, CommissionDocument
from master_data.models import RTOMaster


def _rule(rule_id="R1"):
    return {
        "rule_id": rule_id,
        "priority": 10,
        "conditions": {
            "deal_parameter": "net",
            "state": ["andhra-pradesh"],
            "vehicle_sub_class": ["taxi"],
        },
        "outcome": {
            "commission_type": "percentage",
            "percentage_value": 12.5,
            "applied_on": "Net Premium",
        },
    }


def _pcv_doc():
    return {
        "provider": {"provider_id": "Chola", "provider_name": "Chola"},
        "products": [
            {
                "product_code": "P1",
                "product_name": "Commercial Vehicle",
                "product_category": "motor",
                "sub_category": "PCV",
                "product_subproduct": "commercial-pcv",
                "vehicle_type": "pcv",
                "commission_rules": [_rule()],
            }
        ],
    }


class ExportPcvTaxiMirrorTests(DjangoTestCase):
    @classmethod
    def setUpTestData(cls):
        # Distinct RTO codes per bucket, so each row's RTO cell proves which
        # bucket its product resolved to.
        RTOMaster.objects.create(
            rto_id=1,
            code="AP01",
            value="AP01-Adilabad(Andhra Pradesh)(Group B)",
            state_slug="andhra-pradesh",
            car_zone=1,
            tw_zone=None,
            cv_zone=None,
        )
        RTOMaster.objects.create(
            rto_id=2,
            code="AP02",
            value="AP02-Anantapur(Andhra Pradesh)(Group B)",
            state_slug="andhra-pradesh",
            car_zone=None,
            tw_zone=None,
            cv_zone=1,
        )

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
        wb = load_workbook(io.BytesIO(b"".join(resp.streaming_content)))
        ws = wb.active
        cols = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
        rows = [
            {name: ws.cell(row=r, column=c).value for name, c in cols.items()}
            for r in range(2, ws.max_row + 1)
        ]
        return rows

    def test_both_rows_are_exported(self):
        cics_doc = materialize_pcv_taxi_mirror(_pcv_doc())
        rows = self._export(cics_doc)

        # One row per rule — the export must stay a pure renderer.
        self.assertEqual(len(rows), 2)
        triples = {(r["Product"], r["SubProduct"], r["VehicleSubClass"]) for r in rows}
        self.assertEqual(
            triples,
            {
                ("Commercial Vehicle", "PCV", "taxi"),
                ("Private Car", "Taxi", "taxi"),
            },
        )

    def test_each_row_fills_rto_from_its_own_product_bucket(self):
        cics_doc = materialize_pcv_taxi_mirror(_pcv_doc())
        rows = {r["Product"]: r for r in self._export(cics_doc)}

        cv_rto = rows["Commercial Vehicle"]["RTO"] or ""
        pc_rto = rows["Private Car"]["RTO"] or ""
        self.assertIn("AP02", cv_rto)
        self.assertNotIn("AP01", cv_rto)
        self.assertIn("AP01", pc_rto)
        self.assertNotIn("AP02", pc_rto)

    def test_rates_and_state_are_identical_on_both_rows(self):
        """The mirror is a duplicate of the deal, not a re-priced one."""
        cics_doc = materialize_pcv_taxi_mirror(_pcv_doc())
        rows = {r["Product"]: r for r in self._export(cics_doc)}
        cv_row, pc_row = rows["Commercial Vehicle"], rows["Private Car"]
        for column in (
            "State",
            "ODPercentage",
            "TPPercentage",
            "NetPercentage",
            "FlatAmount",
            "PolicyType",
            "CPA",
        ):
            self.assertEqual(cv_row[column], pc_row[column], column)
        self.assertEqual(cv_row["NetPercentage"], 12.5)
