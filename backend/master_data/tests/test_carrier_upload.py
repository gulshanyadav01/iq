"""
Tests for the carrier-scoped master upload endpoint
(``CarrierMasterUploadViewSet`` / POST /api/master-data/carrier-uploads/).

Run from the backend container:
    python manage.py test master_data.tests.test_carrier_upload -v 2
"""

import io

import openpyxl
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from master_data.models import (
    CarrierProductHierarchyMaster,
    CarrierRTOZoneMaster,
    MasterDataUpload,
)


def _xlsx_bytes(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _auth_client():
    """The endpoint requires an authenticated caller — it rewrites the master
    tables every Chola extraction reads."""
    client = APIClient()
    client.force_authenticate(
        get_user_model().objects.create_user(email="masters@example.com", password="x")
    )
    return client


class CarrierUploadRtoZoneTests(TestCase):
    def setUp(self):
        self.client = _auth_client()
        self.url = reverse("master-data-carrier-uploads-list")

    def test_uploads_and_upserts_rto_zone(self):
        f = _xlsx_bytes(
            [
                ("RTO_CODE", "STATE", "UW ZONE", None, "UP EAST", "NCR"),
                ("MH01", "MAHARASHTRA", "MUMBAI", None, "UP28", "HR26"),
            ]
        )
        f.name = "chola_rto.xlsx"
        resp = self.client.post(
            self.url,
            {"carrier": "Chola", "master_type": "rto_zone", "file": f},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "success")
        self.assertEqual(resp.data["summary"]["CarrierRTOZone"]["created"], 3)
        self.assertEqual(CarrierRTOZoneMaster.objects.count(), 3)

    def test_audit_row_written(self):
        f = _xlsx_bytes(
            [
                ("RTO_CODE", "STATE", "UW ZONE"),
                ("MH01", "MAHARASHTRA", "MUMBAI"),
            ]
        )
        f.name = "chola_rto.xlsx"
        self.client.post(
            self.url,
            {"carrier": "Chola", "master_type": "rto_zone", "file": f},
            format="multipart",
        )
        upload = MasterDataUpload.objects.latest("created_at")
        self.assertEqual(upload.status, MasterDataUpload.Status.SUCCESS)
        self.assertIn("CarrierRTOZone", upload.summary)

    def test_reupload_updates_not_duplicates(self):
        rows = [
            ("RTO_CODE", "STATE", "UW ZONE"),
            ("MH01", "MAHARASHTRA", "MUMBAI"),
        ]
        for _ in range(2):
            f = _xlsx_bytes(rows)
            f.name = "chola_rto.xlsx"
            self.client.post(
                self.url,
                {"carrier": "Chola", "master_type": "rto_zone", "file": f},
                format="multipart",
            )
        self.assertEqual(CarrierRTOZoneMaster.objects.count(), 1)


class CarrierUploadIciciRtoGridTests(TestCase):
    def setUp(self):
        self.client = _auth_client()
        self.url = reverse("master-data-carrier-uploads-list")

    def test_uploads_and_upserts_icici_rto_grid(self):
        f = _xlsx_bytes(
            [
                ("RTO_CODE", "RTO_STATE", "RTO_CITY", "RTO category", "GRID MAPPING"),
                ("AP01", "TELANGANA", "ADILABAD", "GEOGRAPHY", "TELANGANA"),
                ("AP09", "TELANGANA", "HYDERABAD", "VERTICAL", "HYDERABAD"),
            ]
        )
        f.name = "icici_rto.xlsx"
        resp = self.client.post(
            self.url,
            {"carrier": "ICICI", "master_type": "rto_zone", "file": f},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["summary"]["CarrierRTOZone"]["created"], 2)
        self.assertEqual(
            CarrierRTOZoneMaster.objects.filter(carrier="ICICI").count(), 2
        )

    def test_icici_and_chola_uploads_coexist(self):
        chola = _xlsx_bytes(
            [
                ("RTO_CODE", "STATE", "UW ZONE"),
                ("MH01", "MAHARASHTRA", "MUMBAI"),
            ]
        )
        chola.name = "chola.xlsx"
        self.client.post(
            self.url,
            {"carrier": "Chola", "master_type": "rto_zone", "file": chola},
            format="multipart",
        )
        icici = _xlsx_bytes(
            [
                ("RTO_CODE", "GRID MAPPING"),
                ("AP01", "TELANGANA"),
            ]
        )
        icici.name = "icici.xlsx"
        self.client.post(
            self.url,
            {"carrier": "ICICI", "master_type": "rto_zone", "file": icici},
            format="multipart",
        )
        self.assertEqual(
            CarrierRTOZoneMaster.objects.filter(carrier="Chola").count(), 1
        )
        self.assertEqual(
            CarrierRTOZoneMaster.objects.filter(carrier="ICICI").count(), 1
        )

    def test_icici_product_hierarchy_unsupported(self):
        # ICICI only sent an RTO master, not a product-hierarchy workbook —
        # that (carrier, master_type) combo must be rejected, not silently
        # routed to Chola's parser.
        f = _xlsx_bytes([("Product", "SubProduct", "VehicleSubClass")])
        f.name = "x.xlsx"
        resp = self.client.post(
            self.url,
            {"carrier": "ICICI", "master_type": "product_hierarchy", "file": f},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)


class CarrierUploadProductHierarchyTests(TestCase):
    def setUp(self):
        self.client = _auth_client()
        self.url = reverse("master-data-carrier-uploads-list")

    def test_uploads_and_upserts_product_hierarchy(self):
        f = _xlsx_bytes(
            [
                ("Product", "SubProduct", "VehicleSubClass"),
                ("CV", "GCV", "Truck"),
                (None, None, "Tipper"),
            ]
        )
        f.name = "chola_ph.xlsx"
        resp = self.client.post(
            self.url,
            {"carrier": "Chola", "master_type": "product_hierarchy", "file": f},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["summary"]["CarrierProductHierarchy"]["created"], 2)
        self.assertEqual(CarrierProductHierarchyMaster.objects.count(), 2)


class CarrierUploadValidationTests(TestCase):
    def setUp(self):
        self.client = _auth_client()
        self.url = reverse("master-data-carrier-uploads-list")

    def test_unsupported_carrier_rejected(self):
        f = _xlsx_bytes([("RTO_CODE", "STATE", "UW ZONE")])
        f.name = "x.xlsx"
        resp = self.client.post(
            self.url,
            {"carrier": "Bajaj", "master_type": "rto_zone", "file": f},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(CarrierRTOZoneMaster.objects.count(), 0)

    def test_unknown_master_type_rejected_by_serializer(self):
        f = _xlsx_bytes([("RTO_CODE", "STATE", "UW ZONE")])
        f.name = "x.xlsx"
        resp = self.client.post(
            self.url,
            {"carrier": "Chola", "master_type": "not_a_type", "file": f},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)


class CarrierUploadAuthTests(TestCase):
    """An unauthenticated caller must not be able to rewrite the master tables
    every Chola extraction reads."""

    def setUp(self):
        self.client = APIClient()
        self.url = reverse("master-data-carrier-uploads-list")

    def test_anonymous_upload_rejected_and_writes_nothing(self):
        f = _xlsx_bytes([("RTO_CODE", "STATE", "UW ZONE"), ("MH01", "MH", "Mumbai")])
        f.name = "zones.xlsx"
        resp = self.client.post(
            self.url,
            {"carrier": "Chola", "master_type": "rto_zone", "file": f},
            format="multipart",
        )
        self.assertIn(resp.status_code, (401, 403))
        self.assertEqual(CarrierRTOZoneMaster.objects.count(), 0)
        self.assertEqual(MasterDataUpload.objects.count(), 0)

    def test_anonymous_list_rejected(self):
        self.assertIn(self.client.get(self.url).status_code, (401, 403))
