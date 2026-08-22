"""
Auto-sized export columns must stay bounded.

Widths are fitted to the longest cell in the column. Several columns hold list
values with no natural ceiling — an expanded ``VehicleMake`` runs to thousands
of characters once ``exclude(...)`` is rendered as its positive complement — so
an unbounded fit produces a column nothing can read and pushes every later
column off-screen. Excel rejects a width above 255 outright, so this is a
correctness bound as much as a cosmetic one.

Run from the backend container:
    python manage.py test commission_intake.tests.test_export_column_width -v 2
"""

import io

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from rest_framework.test import APIClient

from commission_intake.models import CICSResult, CommissionDocument
from commission_intake.views.commission_document import _MAX_COL_WIDTH


class ExportColumnWidthTests(DjangoTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="width@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _sheet(self, conditions):
        doc = CommissionDocument.objects.create(original_filename="grid.xlsx")
        CICSResult.objects.create(
            document=doc,
            cics_doc={
                "provider": {"provider_name": "Acme"},
                "products": [
                    {
                        "product_name": "Private Car",
                        "sub_category": "Package",
                        "commission_rules": [
                            {
                                "rule_id": "r1",
                                "conditions": conditions,
                                "outcome": {
                                    "commission_type": "percentage",
                                    "percentage_value": 20.0,
                                    "applied_on": "Net Premium",
                                },
                            }
                        ],
                    }
                ],
            },
        )
        url = reverse("commission-documents-export-excel", args=[doc.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))
        wb = load_workbook(io.BytesIO(b"".join(resp.streaming_content)))
        return wb["Commission Rules"]

    def test_a_very_long_cell_does_not_widen_its_column_without_bound(self):
        makes = [f"MANUFACTURER NUMBER {n:04d}" for n in range(400)]
        ws = self._sheet({"deal_parameter": "net", "vehicle_make": makes})

        headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
        col_idx = headers.index("VehicleMake") + 1
        letter = get_column_letter(col_idx)

        # The value itself must survive intact — the bound is on the column, not
        # on the content. A cap that truncated cells would be a data loss bug.
        self.assertGreater(len(str(ws.cell(row=2, column=col_idx).value)), 5000)
        self.assertEqual(ws.column_dimensions[letter].width, _MAX_COL_WIDTH)

    def test_every_column_stays_within_the_bound(self):
        ws = self._sheet(
            {
                "deal_parameter": "net",
                "vehicle_make": [f"MAKE {n}" for n in range(300)],
                "vehicle_model": [f"MODEL {n}" for n in range(300)],
            }
        )
        for col_idx in range(1, ws.max_column + 1):
            letter = get_column_letter(col_idx)
            width = ws.column_dimensions[letter].width
            self.assertLessEqual(
                width,
                _MAX_COL_WIDTH,
                f"column {letter} ({ws.cell(row=1, column=col_idx).value}) "
                f"is {width} wide",
            )

    def test_short_columns_still_size_to_their_content(self):
        """The bound is a ceiling, not a fixed width — a narrow column must not
        be padded out to it, or every sheet becomes one uniform slab."""
        ws = self._sheet({"deal_parameter": "net"})
        headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
        letter = get_column_letter(headers.index("CPA") + 1)
        self.assertLess(ws.column_dimensions[letter].width, _MAX_COL_WIDTH)
