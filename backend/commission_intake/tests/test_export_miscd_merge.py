"""End-to-end export of the two CV corrections shipped in CICS 1.10.0.

Both changes happen in the RULES (see ``cins/tests/test_miscd_subclass_merge.py``
and ``test_renewal_rollover.py``); this file only proves the exporter renders
them, i.e. that a multi-value ``vehicle_sub_class`` and a widened
``business_type`` reach the sheet as one comma-separated cell each rather than
as extra rows or a dropped value.

Run from the backend container:
    python manage.py test commission_intake.tests.test_export_miscd_merge -v 2
"""

import io

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from openpyxl import load_workbook
from rest_framework.test import APIClient

from commission_intake.models import CICSResult, CommissionDocument

MERGED_BODIES = ["ambulance", "crane", "fork-lift-trucks", "loader", "road-rollers"]


def _rule(rule_id, conditions):
    return {
        "rule_id": rule_id,
        "conditions": {"deal_parameter": "net", **conditions},
        "outcome": {
            "commission_type": "percentage",
            "percentage_value": 10,
            "applied_on": "Net Premium",
        },
    }


def _doc(rules):
    return {
        "provider": {"provider_name": "Cholamandalam MS"},
        "products": [
            {
                "product_name": "Commercial Vehicle",
                "sub_category": "MISC-D",
                "commission_rules": rules,
            }
        ],
    }


class ExportMiscDMergeTests(DjangoTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="miscd@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _rows(self, cics_doc, *columns):
        doc = CommissionDocument.objects.create(original_filename="grid.xlsx")
        CICSResult.objects.create(document=doc, cics_doc=cics_doc)
        url = reverse("commission-documents-export-excel", args=[doc.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))
        ws = load_workbook(io.BytesIO(b"".join(resp.streaming_content)))[
            "Commission Rules"
        ]
        cols = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
        return [
            tuple(r[cols[name] - 1] for name in columns)
            for r in ws.iter_rows(min_row=2, values_only=True)
        ]

    def test_merged_bodies_export_as_one_comma_separated_row(self):
        rows = self._rows(
            _doc([_rule("R1_SC_MISCD", {"vehicle_sub_class": MERGED_BODIES})]),
            "VehicleSubClass",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], ",".join(MERGED_BODIES))

    def test_tractor_and_harvester_keep_their_own_rows(self):
        rows = self._rows(
            _doc(
                [
                    _rule(
                        "R1_SC_agricultural-tractor",
                        {"vehicle_sub_class": ["agricultural-tractor"]},
                    ),
                    _rule("R1_SC_harvester", {"vehicle_sub_class": ["harvester"]}),
                    _rule("R1_SC_MISCD", {"vehicle_sub_class": MERGED_BODIES}),
                ]
            ),
            "VehicleSubClass",
        )
        self.assertEqual(
            [r[0] for r in rows],
            ["agricultural-tractor", "harvester", ",".join(MERGED_BODIES)],
        )

    def test_renewal_rollover_exports_as_one_cell(self):
        rows = self._rows(
            _doc(
                [
                    _rule(
                        "R1",
                        {
                            "vehicle_sub_class": MERGED_BODIES,
                            "business_type": ["renewal", "rollover"],
                        },
                    )
                ]
            ),
            "BusinessType",
        )
        self.assertEqual(rows[0][0], "renewal,rollover")

    def test_absent_business_type_still_exports_blank(self):
        # The widening never fills an EMPTY business_type — that would be a
        # different claim than "renewals include rollovers".
        rows = self._rows(
            _doc([_rule("R1", {"vehicle_sub_class": MERGED_BODIES})]),
            "BusinessType",
        )
        self.assertIn(rows[0][0], (None, ""))
