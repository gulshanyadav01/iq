"""
Export rendering of make/model exclusion buckets (Step 5).

Exclusion lists render INLINE as ``exclude(A, B, C)`` inside the existing
VehicleMake / VehicleModel columns — no new columns, so index-based importers
are unaffected. Include lists render unchanged; blank when absent.

Run from the backend container:
    python manage.py test commission_intake.tests.test_export_make_model_exclusion -v 2
"""

import io

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from openpyxl import load_workbook
from rest_framework.test import APIClient

from commission_intake.models import CommissionDocument, CICSResult


def _rule(conditions):
    return {
        "rule_id": "r",
        "conditions": {"deal_parameter": "net", **conditions},
        "outcome": {
            "commission_type": "percentage",
            "percentage_value": 10,
            "applied_on": "Net Premium",
        },
    }


def _doc(rules):
    return {
        "provider": {"provider_name": "Chola"},
        "products": [
            {
                "product_name": "Commercial Vehicle",
                "sub_category": "GCV",
                "commission_rules": rules,
            }
        ],
    }


class ExportExclusionRenderTests(DjangoTestCase):
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

    def test_three_bucket_rendering(self):
        wb = self._export(
            _doc(
                [
                    # (a) TMM + model list
                    _rule(
                        {
                            "vehicle_make": ["TATA", "Maruti", "Mahindra"],
                            "vehicle_model": ["Ace", "Super Ace"],
                        }
                    ),
                    # (b) TMM + model exclusion
                    _rule(
                        {
                            "vehicle_make": ["TATA", "Maruti", "Mahindra"],
                            "vehicle_model_exclude": ["Ace", "Super Ace"],
                        }
                    ),
                    # (c) make exclusion
                    _rule({"vehicle_make_exclude": ["TATA", "Maruti", "Mahindra"]}),
                ]
            )
        )
        ws = wb["Commission Rules"]
        cols = self._cols(ws)
        mk = cols["VehicleMake"] - 1
        md = cols["VehicleModel"] - 1
        rows = self._rows(ws)

        self.assertEqual(rows[0][mk], "TATA,Maruti,Mahindra")
        self.assertEqual(rows[0][md], "Ace,Super Ace")

        self.assertEqual(rows[1][mk], "TATA,Maruti,Mahindra")
        self.assertEqual(rows[1][md], "exclude(Ace, Super Ace)")

        self.assertEqual(rows[2][mk], "exclude(TATA, Maruti, Mahindra)")
        self.assertIn(rows[2][md], (None, ""))

    def test_columns_unchanged_count(self):
        wb = self._export(_doc([_rule({"vehicle_make_exclude": ["TATA"]})]))
        ws = wb["Commission Rules"]
        cols = self._cols(ws)
        # VehicleMake / VehicleModel are adjacent single columns (no new cols).
        self.assertEqual(cols["VehicleModel"] - cols["VehicleMake"], 1)

    def test_both_set_prefers_include_and_blank_when_absent(self):
        wb = self._export(
            _doc(
                [
                    # both make and make_exclude set -> prefer include (defensive)
                    _rule(
                        {"vehicle_make": ["TATA"], "vehicle_make_exclude": ["Maruti"]}
                    ),
                    # neither make nor make_exclude -> blank cell
                    _rule({"vehicle_model": ["Ace"]}),
                ]
            )
        )
        ws = wb["Commission Rules"]
        cols = self._cols(ws)
        mk = cols["VehicleMake"] - 1
        rows = self._rows(ws)
        self.assertEqual(rows[0][mk], "TATA")  # include wins
        self.assertIn(rows[1][mk], (None, ""))  # blank when absent
