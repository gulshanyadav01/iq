"""
Tests for the export RTO-fill complement (1A): when a rule fills its RTO cell
from state (no explicit ``conditions.rto``), the fill excludes any RTO code a
SIBLING rule in the same product bucket already claims explicitly. Closes the
Chola UP-East/UP-West TODOS.md:92 collision — a zone-tagged rule (explicit
``rto``) and the plain state rule (state-derived fill) render distinct,
non-overlapping RTO sets instead of colliding on ``uttar-pradesh``.

The mechanism itself is carrier-agnostic (keyed off product bucket only, not
carrier) — it also covers Chola's NCR zone (HR/UP mixed codes) subtracted
from the generic UP/HR fill, and degrades to today's plain state-fill
behaviour for any document with no explicit ``rto`` claims at all.

Run from the backend container:
    python manage.py test commission_intake.tests.test_export_up_complement -v 2
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


def _doc(provider_name, rules, product_name="Private Car"):
    return {
        "provider": {"provider_name": provider_name},
        "products": [
            {
                "product_name": product_name,
                "sub_category": "",
                "commission_rules": rules,
            }
        ],
    }


class ExportUpComplementTests(DjangoTestCase):
    @classmethod
    def setUpTestData(cls):
        # Uttar Pradesh: UP28/UP32 (the "East" zone's own codes) + UP99 (the
        # rest of the state), all car-active.
        RTOMaster.objects.create(
            rto_id=1,
            code="UP28",
            value="UP28-East1(Uttar Pradesh)(Group A)",
            state_slug="uttar-pradesh",
            car_zone=1,
            cv_zone=1,
        )
        RTOMaster.objects.create(
            rto_id=2,
            code="UP32",
            value="UP32-East2(Uttar Pradesh)(Group A)",
            state_slug="uttar-pradesh",
            car_zone=1,
            cv_zone=1,
        )
        RTOMaster.objects.create(
            rto_id=3,
            code="UP99",
            value="UP99-Rest(Uttar Pradesh)(Group A)",
            state_slug="uttar-pradesh",
            car_zone=1,
            cv_zone=1,
        )
        # Haryana: HR26 (claimed by an "NCR" style rule) + HR50 (the rest).
        RTOMaster.objects.create(
            rto_id=4,
            code="HR26",
            value="HR26-Ncr1(Haryana)(Group A)",
            state_slug="haryana",
            car_zone=1,
            cv_zone=1,
        )
        RTOMaster.objects.create(
            rto_id=5,
            code="HR50",
            value="HR50-Rest(Haryana)(Group A)",
            state_slug="haryana",
            car_zone=1,
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

    @staticmethod
    def _cols(ws):
        return {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}

    def _rto_values(self, ws, row_idx):
        cols = self._cols(ws)
        cell = ws.cell(row=row_idx, column=cols["RTO"]).value
        return set(cell.split(",")) if cell else set()

    def test_plain_up_renders_up_minus_east(self):
        wb = self._export(
            _doc(
                "Chola",
                [
                    # zone-tagged rule: explicit rto (as Chola zone expansion
                    # would produce for "UP- East").
                    _rule({"rto": ["UP28", "UP32"], "state": ["uttar-pradesh"]}),
                    # plain state rule: no explicit rto -> state-derived fill.
                    _rule({"state": ["uttar-pradesh"]}),
                ],
            )
        )
        ws = wb["Commission Rules"]
        row1 = self._rto_values(ws, 2)
        row2 = self._rto_values(ws, 3)

        # Explicit rto renders verbatim (bare codes), not expanded to display values.
        self.assertEqual(row1, {"UP28", "UP32"})
        # Plain UP excludes the sibling's explicitly-claimed East codes.
        self.assertEqual(row2, {"UP99-Rest(Uttar Pradesh)(Group A)"})

    def test_ncr_codes_excluded_from_generic_haryana_fill(self):
        wb = self._export(
            _doc(
                "Chola",
                [
                    _rule({"rto": ["HR26"], "state": ["haryana"]}),  # NCR-style claim
                    _rule({"state": ["haryana"]}),  # plain Haryana fill
                ],
            )
        )
        ws = wb["Commission Rules"]
        plain_fill = self._rto_values(ws, 3)
        self.assertEqual(plain_fill, {"HR50-Rest(Haryana)(Group A)"})

    def test_non_chola_no_explicit_claims_fill_unchanged(self):
        # No sibling rule claims any RTO explicitly -> full state fill, same
        # as before the complement mechanism existed.
        wb = self._export(_doc("Bajaj", [_rule({"state": ["uttar-pradesh"]})]))
        ws = wb["Commission Rules"]
        self.assertEqual(
            self._rto_values(ws, 2),
            {
                "UP28-East1(Uttar Pradesh)(Group A)",
                "UP32-East2(Uttar Pradesh)(Group A)",
                "UP99-Rest(Uttar Pradesh)(Group A)",
            },
        )

    def test_explicit_rto_rule_always_honored_verbatim(self):
        wb = self._export(
            _doc("Chola", [_rule({"rto": ["UP28"], "state": ["uttar-pradesh"]})])
        )
        ws = wb["Commission Rules"]
        cols = self._cols(ws)
        cell = ws.cell(row=2, column=cols["RTO"]).value
        self.assertEqual(cell, "UP28")  # explicit value rendered as-is, not expanded
