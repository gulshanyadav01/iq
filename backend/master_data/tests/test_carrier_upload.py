"""
Tests for the carrier-scoped master upload endpoint
(``CarrierMasterUploadViewSet`` / POST /api/master-data/carrier-uploads/).

Run from the backend container:
    python manage.py test master_data.tests.test_carrier_upload -v 2
"""

import io

import openpyxl
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from master_data.models import (
    CarrierProductHierarchyMaster,
    CarrierRTOZoneMaster,
    CarrierVehicleMMVMaster,
    MasterDataUpload,
)

# The endpoint persists the uploaded workbook to ``MasterDataUpload.file``, whose
# default storage is S3/MinIO. CI has no object store, so keep the write in memory
# — nothing here asserts on the stored bytes.
in_memory_storage = override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
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


@in_memory_storage
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


@in_memory_storage
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


@in_memory_storage
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


@in_memory_storage
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


@in_memory_storage
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


def _mmv_xlsx_bytes(tw_rows, fw_rows):
    """Two-and-four-wheeler MMV workbook — the TW/FW sheet-name shape
    ``parse_chola_mmv`` dispatches on."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "TW"
    ws.append(
        (
            "VariantId",
            "MakeName",
            "makeid",
            "modelname",
            "ModelId",
            "variantname",
            "CubicCapacity",
            "KiloWatt",
            "fueltype",
            "segmenttype",
        )
    )
    for row in tw_rows:
        ws.append(row)
    fw = wb.create_sheet("FW")
    fw.append(("MAKEID", "MakeName", "modelname", "ModelId"))
    for row in fw_rows:
        fw.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


@in_memory_storage
class CarrierUploadMMVTests(TestCase):
    """The ``mmv`` master_type. Exercises the one path that reads a workbook
    from a FILE rather than an in-memory openpyxl object, so it covers
    ``open_workbook``'s opened/close branch that the parser unit tests
    (which pass a live Workbook) never reach."""

    def setUp(self):
        self.client = _auth_client()
        self.url = reverse("master-data-carrier-uploads-list")

    def _post(self):
        f = _mmv_xlsx_bytes(
            tw_rows=[
                (1, "BAJAJ", 6, "AVENGER", 10, "STD (180 CC)", 180, None, "P ", "BIKE")
            ],
            fw_rows=[(1, "MARUTI", "SWIFT", 20)],
        )
        f.name = "chola_mmv.xlsx"
        return self.client.post(
            self.url,
            {"carrier": "Chola", "master_type": "mmv", "file": f},
            format="multipart",
        )

    def test_uploads_and_upserts_mmv(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "success")
        self.assertEqual(resp.data["summary"]["CarrierVehicleMMV"]["created"], 2)
        self.assertEqual(CarrierVehicleMMVMaster.objects.count(), 2)
        self.assertEqual(
            set(CarrierVehicleMMVMaster.objects.values_list("product", flat=True)),
            {"TW", "Car"},
        )

    def test_reupload_updates_not_duplicates(self):
        self._post()
        self._post()
        self.assertEqual(CarrierVehicleMMVMaster.objects.count(), 2)
        upload = MasterDataUpload.objects.latest("created_at")
        self.assertEqual(upload.summary["CarrierVehicleMMV"]["updated"], 2)

    def test_audit_row_written(self):
        self._post()
        upload = MasterDataUpload.objects.latest("created_at")
        self.assertEqual(upload.status, MasterDataUpload.Status.SUCCESS)
        self.assertIn("CarrierVehicleMMV", upload.summary)

    def test_mmv_is_scoped_to_chola(self):
        """No parser is registered for another carrier's MMV book, so the
        request is rejected before the file is read."""
        f = _mmv_xlsx_bytes([], [(1, "MARUTI", "SWIFT", 20)])
        f.name = "icici_mmv.xlsx"
        resp = self.client.post(
            self.url,
            {"carrier": "ICICI", "master_type": "mmv", "file": f},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(CarrierVehicleMMVMaster.objects.count(), 0)
