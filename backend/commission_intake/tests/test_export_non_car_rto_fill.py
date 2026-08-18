"""
Export RTO fill for NON-Private-Car products.

Every other export test hardcodes ``product_name="Private Car"``
(test_export_up_complement, test_export_column_alignment, test_export_cpa_split,
test_export_make_model_exclusion). That blind spot is why the client-reported
Chola defect survived: the export derives the RTO column from the rule's states
via ``product_bucket(product_name)``, and a product_name that resolved to no
bucket rendered an empty RTO cell even for a rule carrying a perfectly good
state. Private Car is immune because virtually every spelling contains "car".

These tests pin the Two Wheeler and Commercial Vehicle paths, including the
carrier spellings (``GCCV``/``PCCV``/``MSV``) a product_name takes when it
escapes canonicalization.

Run from the backend container:
    python manage.py test commission_intake.tests.test_export_non_car_rto_fill -v 2
"""

import io

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from openpyxl import load_workbook
from rest_framework.test import APIClient

from commission_intake.models import CICSResult, CommissionDocument
from master_data.models import RTOMaster


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


def _doc(product_name, rules, sub_category=""):
    return {
        "provider": {"provider_name": "Chola"},
        "products": [
            {
                "product_name": product_name,
                "sub_category": sub_category,
                "commission_rules": rules,
            }
        ],
    }


class ExportNonCarRtoFillTests(DjangoTestCase):
    @classmethod
    def setUpTestData(cls):
        # One Andhra Pradesh RTO active for all three products, so a blank RTO
        # cell can only mean the bucket failed to resolve.
        RTOMaster.objects.create(
            rto_id=1,
            code="AP01",
            value="AP01-Adilabad(Andhra Pradesh)(Group B)",
            state_slug="andhra-pradesh",
            car_zone=1,
            tw_zone=1,
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
        return load_workbook(io.BytesIO(b"".join(resp.streaming_content)))

    def _cells(self, wb):
        ws = wb.active
        cols = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
        return (
            ws.cell(row=2, column=cols["State"]).value,
            ws.cell(row=2, column=cols["RTO"]).value,
        )

    def _assert_filled(self, product_name, sub_category=""):
        wb = self._export(
            _doc(
                product_name,
                [_rule({"state": ["andhra-pradesh"]})],
                sub_category=sub_category,
            )
        )
        state, rto = self._cells(wb)
        self.assertTrue(state, f"{product_name}: State cell is blank")
        self.assertTrue(rto, f"{product_name}: RTO cell is blank")
        self.assertIn("AP01", rto)

    def test_two_wheeler_fills_rto_from_state(self):
        self._assert_filled("Two Wheeler", sub_category="Two Wheeler")

    def test_commercial_vehicle_fills_rto_from_state(self):
        for sub in ("GCV", "PCV", "Buses", "MISC-D"):
            with self.subTest(sub_category=sub):
                self._assert_filled("Commercial Vehicle", sub_category=sub)

    def test_carrier_cv_spellings_still_fill_rto(self):
        """A product_name that escaped canonicalization must not blank the cell."""
        for name in ("GCCV", "PCCV", "MSV"):
            with self.subTest(product_name=name):
                self._assert_filled(name)

    def test_private_car_is_unaffected(self):
        self._assert_filled("Private Car")

    def test_state_less_rule_leaves_both_cells_blank(self):
        """No defaulting: an unconstrained rule renders blank, not 'all states'."""
        wb = self._export(_doc("Two Wheeler", [_rule({})]))
        state, rto = self._cells(wb)
        self.assertFalse(state)
        self.assertFalse(rto)
