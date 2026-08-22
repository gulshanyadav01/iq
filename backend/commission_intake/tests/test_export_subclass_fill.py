"""
VehicleSubClass fill for commercial rules that name no body class.

A rule whose grid names no body is recorded as Chola's own canonical "Not
Available" — a real value in the carrier's hierarchy master, not a gap. The rule
still applies to every body in its sub-product, and the carrier's live deal
records say so by enumerating all of them ("Crane,Excavator,...").

The widening happens in the EXPORT CELL, never in the rule's conditions, so it
cannot shift a dedupe group key or move CICS_PIPELINE_VERSION.

**GCV is out of scope for the widening** — a goods-carrying grid prices by
weight band and publishes no body type at all, so ``_strip_gcv_subclass`` clears
the field in the transformer and the cell renders BLANK. It used to carry the
sentinel and widen into all 15 seeded GCV bodies, which was a guess the grid
never made. The GCV fixtures below therefore exercise the null path; the
widening itself is pinned on PCV / MISC-D.

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

# GCV is seeded on purpose even though it is never widened: the blank-cell
# tests below have to prove the null path wins over an available fill, not
# merely that the master happens to be empty.
GCV_BODIES = ["Truck", "Tipper", "Tractor", "Not Available"]
PCV_BODIES = ["Taxi", "Auto Rickshaw", "Not Available"]
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
        for body in PCV_BODIES:
            CarrierProductHierarchyMaster.objects.create(
                carrier="Chola",
                product="CV",
                sub_product="PCV",
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
            _doc("PCV", [_rule({"vehicle_sub_class": ["not-available"]})])
        )
        self.assertEqual(cell, "Auto Rickshaw,Not Available,Taxi")

    def test_gcv_null_subclass_exports_blank(self):
        # What the transformer now emits for GCV. The master HAS 4 GCV bodies
        # seeded, so this proves the null path wins over an available fill
        # rather than passing by accident.
        (cell,) = self._cells(_doc("GCV", [_rule({"vehicle_sub_class": None})]))
        self.assertIn(cell, (None, ""))

    def test_gcv_absent_subclass_key_exports_blank(self):
        (cell,) = self._cells(_doc("GCV", [_rule({})]))
        self.assertIn(cell, (None, ""))

    def test_fill_is_scoped_to_the_rules_sub_product(self):
        (cell,) = self._cells(
            _doc("MISC-D", [_rule({"vehicle_sub_class": ["not-available"]})])
        )
        self.assertEqual(cell, "Crane,Excavator")

    def test_a_resolved_body_is_never_widened(self):
        (cell,) = self._cells(_doc("PCV", [_rule({"vehicle_sub_class": ["taxi"]})]))
        self.assertEqual(cell, "taxi")

    def test_a_mixed_bucket_is_left_alone(self):
        # "Not Available" beside real bodies is a genuine enumeration, not an
        # absent dimension — widening it would invent coverage.
        (cell,) = self._cells(
            _doc("PCV", [_rule({"vehicle_sub_class": ["not-available", "taxi"]})])
        )
        self.assertEqual(cell, "not-available,taxi")

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
