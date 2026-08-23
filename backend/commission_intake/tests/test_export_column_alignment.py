"""
Structural guard for the CICS Excel export: header row and data row must stay
the same length, and the NCB triplet must stay contiguous.

WHY THIS EXISTS (and why it is deliberately generic):
``commission_document.py`` builds the header list and the data-row list ~250
lines apart, matched purely by POSITION. Nothing in the renderer checks that the
two agree — the only consumer of the header count sizes column widths, so a
mismatch does not raise. Every cell is stringified, so a one-off insertion
silently shifts every later column: the client's upload template receives CPA
values under FuelType and nobody finds out until a commission dispute.

The alignment test below is therefore NOT about NCB. It protects every future
column insertion. The NCB-specific assertions ride along because that is the
change that surfaced the hazard.

Run from the backend container:
    python manage.py test commission_intake.tests.test_export_column_alignment -v 2
"""

import io

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from openpyxl import load_workbook
from rest_framework.test import APIClient

from commission_intake.models import CommissionDocument, CICSResult


def _doc(conditions):
    return {
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
    }


class ExportColumnAlignmentTests(DjangoTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="align@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _sheet(self, cics_doc):
        doc = CommissionDocument.objects.create(original_filename="grid.xlsx")
        CICSResult.objects.create(document=doc, cics_doc=cics_doc)
        url = reverse("commission-documents-export-excel", args=[doc.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))
        wb = load_workbook(io.BytesIO(b"".join(resp.streaming_content)))
        return wb["Commission Rules"]

    @staticmethod
    def _headers(ws):
        return [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]

    # ---- the generic guard -------------------------------------------------
    #
    # NOTE: comparing len(header_row) against len(data_row) does NOT work here.
    # openpyxl pads every row out to ws.max_column, so a data row that is one
    # cell SHORT still reads back at full width and the comparison passes. That
    # version of this test was verified vacuous by mutation (adding a header
    # with no matching cell did not fail it). Anchor on VALUES instead: give
    # columns spread across the whole sheet distinctive values and assert each
    # one lands under its own header. A misinsertion anywhere shifts at least
    # one of them.

    ALIGNMENT_PROBE_CONDITIONS = {
        "deal_parameter": "net",
        "zero_dep": "yes",
        "ncb": True,
        "ncb_start_range": 25,
        "ncb_end_range": 100,
        "cpa": "with-cpa",
        "fuel_type": ["petrol"],
        "vehicle_age": 7,
        "vehicle_make": ["MARUTI"],
        "vehicle_model": ["SWIFT"],
        "vehicle_carrier": ["private"],
        "business_type": ["renewal"],
        "od_term": 1,
        "tp_term": 3,
        "cc_start_range": 800,
        "cc_end_range": 1000,
    }

    def test_every_column_lands_under_its_own_header(self):
        """End-to-end positional guard for the header/cell pairing.

        The header list (~:759) and the data-row list (~:1009) are built ~250
        lines apart and matched only by position, with no length check in the
        renderer. Insert into one and not the other and every later column
        shifts silently.
        """
        ws = self._sheet(_doc(self.ALIGNMENT_PROBE_CONDITIONS))
        headers = self._headers(ws)
        row = list(ws.iter_rows(min_row=2, values_only=True))[0]
        cell = {h: row[i] for i, h in enumerate(headers)}

        # Probes deliberately span the sheet: before NCB, at NCB, just after
        # NCB, and well past it. The trailing ones are what catch a shift
        # introduced by the NCB insertion itself.
        expected = {
            "ZeroDep": "yes",
            "CCStartRange": "800",
            "CCEndRange": "1000",
            "NCB": "Yes",
            "NCBStartRange": "25",
            "NCBEndRange": "100",
            "CPA": "With CPA",
            "FuelType": "petrol",
            "VehicleAge": "7",
            "VehicleMake": "MARUTI",
            "VehicleModel": "SWIFT",
            "VehicleCarrier": "private",
            "BusinessType": "renewal",
            "ODTerm": "1",
            "TPTerm": "3",
            "NetPercentage": "20",
        }
        for header, want in expected.items():
            self.assertIn(header, cell, f"header {header} missing from export")
            self.assertEqual(
                str(cell[header]),
                want,
                f"column {header!r} holds {cell[header]!r}, expected {want!r} — "
                "the export header list and data-row list have drifted out of "
                "sync, so values are landing under the wrong headers",
            )

    def test_last_column_is_still_reachable(self):
        """The final header must still receive its own value.

        A header added without a matching cell leaves the last column empty
        while openpyxl silently pads the row, so assert the tail explicitly.
        """
        conditions = dict(self.ALIGNMENT_PROBE_CONDITIONS)
        doc = _doc(conditions)
        doc["products"][0]["commission_rules"][0]["outcome"]["other_conditions"] = (
            "tourist permit only"
        )
        ws = self._sheet(doc)
        headers = self._headers(ws)
        row = list(ws.iter_rows(min_row=2, values_only=True))[0]
        cell = {h: row[i] for i, h in enumerate(headers)}
        self.assertEqual(headers[-1], "QualifyingClauses")
        self.assertIn("tourist permit only", str(cell["OtherConditions"]))

    def test_headers_are_unique(self):
        """Duplicate headers make positional lookups ambiguous in every other
        export test (they resolve a name to a single column index)."""
        headers = self._headers(self._sheet(_doc({"deal_parameter": "net"})))
        self.assertEqual(len(headers), len(set(headers)))

    # ---- the NCB triplet ---------------------------------------------------

    def test_ncb_range_columns_follow_ncb(self):
        headers = self._headers(self._sheet(_doc({"deal_parameter": "net"})))
        i = headers.index("NCB")
        self.assertEqual(headers[i + 1], "NCBStartRange")
        self.assertEqual(headers[i + 2], "NCBEndRange")

    def test_ncb_band_values_render(self):
        ws = self._sheet(
            _doc(
                {
                    "deal_parameter": "net",
                    "ncb": True,
                    "ncb_start_range": 25,
                    "ncb_end_range": 100,
                }
            )
        )
        headers = self._headers(ws)
        row = list(ws.iter_rows(min_row=2, values_only=True))[0]
        cell = {h: row[i] for i, h in enumerate(headers)}
        self.assertEqual(str(cell["NCB"]), "Yes")
        self.assertEqual(str(cell["NCBStartRange"]), "25")
        self.assertEqual(str(cell["NCBEndRange"]), "100")

    def test_absent_ncb_band_defaults_to_full_range(self):
        """No NCB dimension means the rule applies across the whole 0-100 band,
        mirroring how the NCB boolean defaults to "Yes,No"."""
        ws = self._sheet(_doc({"deal_parameter": "net"}))
        headers = self._headers(ws)
        row = list(ws.iter_rows(min_row=2, values_only=True))[0]
        cell = {h: row[i] for i, h in enumerate(headers)}
        self.assertEqual(str(cell["NCB"]), "Yes,No")
        self.assertEqual(str(cell["NCBStartRange"]), "0")
        self.assertEqual(str(cell["NCBEndRange"]), "100")

    def test_no_bonus_row_renders_the_zero_point_band(self):
        """_pair_ncb_tiers narrows the base twin to (0, 0): NCB = No means the
        policy earned no bonus at all, not "some bonus below the threshold"."""
        ws = self._sheet(
            _doc(
                {
                    "deal_parameter": "net",
                    "ncb": False,
                    "ncb_start_range": 0,
                    "ncb_end_range": 0,
                }
            )
        )
        headers = self._headers(ws)
        row = list(ws.iter_rows(min_row=2, values_only=True))[0]
        cell = {h: row[i] for i, h in enumerate(headers)}
        self.assertEqual(str(cell["NCB"]), "No")
        self.assertEqual(str(cell["NCBStartRange"]), "0")
        self.assertEqual(str(cell["NCBEndRange"]), "0")

    def test_mirrored_base_twin_renders_yes_on_the_base_band(self):
        """The other half of the split: same rate, ncb=True on (0, 24). The
        boolean is set explicitly, so the export must render it verbatim rather
        than inferring "No" from a band that starts at 0."""
        ws = self._sheet(
            _doc(
                {
                    "deal_parameter": "net",
                    "ncb": True,
                    "ncb_start_range": 0,
                    "ncb_end_range": 24,
                }
            )
        )
        headers = self._headers(ws)
        row = list(ws.iter_rows(min_row=2, values_only=True))[0]
        cell = {h: row[i] for i, h in enumerate(headers)}
        self.assertEqual(str(cell["NCB"]), "Yes")
        self.assertEqual(str(cell["NCBStartRange"]), "0")
        self.assertEqual(str(cell["NCBEndRange"]), "24")
