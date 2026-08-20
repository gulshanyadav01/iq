"""
VehicleSubClass fill for weight-banded commercial rules.

A GCV rule priced purely on GVW names no body class, which the transformer
records as Chola's own canonical "Not Available" — a real value in the carrier's
hierarchy master, not a gap. The rule still applies to every body in its
sub-product, and the carrier's live deal records say so by enumerating all of
them ("Pick Up/Delivery Van,Truck,Tractor,Tipper,...").

The widening happens in the EXPORT CELL, never in the rule's conditions, so it
cannot shift a dedupe group key or move CICS_PIPELINE_VERSION.

Run from the backend container:
    python manage.py test commission_intake.tests.test_export_subclass_fill -v 2
"""

import io

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from openpyxl import load_workbook
from rest_framework.test import APIClient

from commission_intake.models import CommissionDocument, CICSResult
from master_data.models import CarrierProductHierarchyMaster

GCV_BODIES = ["Truck", "Tipper", "Tractor", "Not Available"]
MISCD_BODIES = ["Crane", "Excavator"]


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


def _doc(sub_category, rules):
    return {
        "provider": {"provider_name": "Cholamandalam MS"},
        "products": [
            {
                "product_name": "Commercial Vehicle",
                "sub_category": sub_category,
                "commission_rules": rules,
            }
        ],
    }


class ExportSubclassFillTests(DjangoTestCase):
    @classmethod
    def setUpTestData(cls):
        for body in GCV_BODIES:
            CarrierProductHierarchyMaster.objects.create(
                carrier="Chola",
                product="CV",
                sub_product="GCV",
                vehicle_subclass=body,
                canonical_subclass=body,
            )
        for body in MISCD_BODIES:
            CarrierProductHierarchyMaster.objects.create(
                carrier="Chola",
                product="CV",
                sub_product="MISC-D",
                vehicle_subclass=body,
                canonical_subclass=body,
            )

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="fill@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _cells(self, cics_doc):
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
            r[cols["VehicleSubClass"] - 1]
            for r in ws.iter_rows(min_row=2, values_only=True)
        ]

    def test_not_available_widens_to_the_sub_products_bodies(self):
        (cell,) = self._cells(
            _doc("GCV", [_rule({"vehicle_sub_class": ["not-available"]})])
        )
        self.assertEqual(cell, "Not Available,Tipper,Tractor,Truck")

    def test_fill_is_scoped_to_the_rules_sub_product(self):
        (cell,) = self._cells(
            _doc("MISC-D", [_rule({"vehicle_sub_class": ["not-available"]})])
        )
        self.assertEqual(cell, "Crane,Excavator")

    def test_a_resolved_body_is_never_widened(self):
        (cell,) = self._cells(_doc("GCV", [_rule({"vehicle_sub_class": ["tipper"]})]))
        self.assertEqual(cell, "tipper")

    def test_a_mixed_bucket_is_left_alone(self):
        # "Not Available" beside real bodies is a genuine enumeration, not an
        # absent dimension — widening it would invent coverage.
        (cell,) = self._cells(
            _doc("GCV", [_rule({"vehicle_sub_class": ["not-available", "tipper"]})])
        )
        self.assertEqual(cell, "not-available,tipper")

    def test_unknown_sub_product_leaves_the_sentinel(self):
        (cell,) = self._cells(
            _doc("NOSUCH", [_rule({"vehicle_sub_class": ["not-available"]})])
        )
        self.assertEqual(cell, "not-available")

    def test_other_carriers_are_not_filled_from_cholas_book(self):
        doc = _doc("GCV", [_rule({"vehicle_sub_class": ["not-available"]})])
        doc["provider"]["provider_name"] = "ICICI Lombard"
        (cell,) = self._cells(doc)
        self.assertEqual(cell, "not-available")
