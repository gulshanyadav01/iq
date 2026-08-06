"""
Tests for the Chola carrier-scoped master loader
(``upsert_carrier_rto_zone_rows`` / ``upsert_chola_product_hierarchy_rows``)
and the committed CSVs that seed them.

Run from the backend container:
    python manage.py test master_data.tests.test_chola_masters -v 2
"""

from django.test import TestCase

from master_data.models import (
    CarrierProductHierarchyMaster,
    CarrierRTOZoneMaster,
    VehicleSubClassMaster,
)
from master_data.services.loader import (
    CHOLA_PRODUCT_HIERARCHY_CSV_PATH,
    CHOLA_RTO_ZONE_CSV_PATH,
    MasterDataLoader,
)


class CholaRtoZoneLoaderTests(TestCase):
    def test_upsert_creates_then_updates_not_duplicates(self):
        loader = MasterDataLoader()
        rows = [("Chola", "mumbai", "MH01"), ("Chola", "up-east", "UP28")]
        loader.upsert_carrier_rto_zone_rows(rows)
        self.assertEqual(CarrierRTOZoneMaster.objects.count(), 2)
        self.assertEqual(loader.result.sheets["CarrierRTOZone"].created, 2)

        loader2 = MasterDataLoader()
        loader2.upsert_carrier_rto_zone_rows(rows)
        self.assertEqual(CarrierRTOZoneMaster.objects.count(), 2)  # no dup
        self.assertEqual(loader2.result.sheets["CarrierRTOZone"].created, 0)
        self.assertEqual(loader2.result.sheets["CarrierRTOZone"].updated, 2)

    def test_derives_state_slug_from_code_prefix(self):
        loader = MasterDataLoader()
        loader.upsert_carrier_rto_zone_rows([("Chola", "chennai", "TN01")])
        row = CarrierRTOZoneMaster.objects.get(rto_code="TN01")
        self.assertEqual(row.state_slug, "tamil-nadu")

    def test_unmapped_prefix_skipped_and_counted(self):
        loader = MasterDataLoader()
        loader.upsert_carrier_rto_zone_rows(
            [("Chola", "mumbai", "MH01"), ("Chola", "nowhere", "ZZ09")]
        )
        self.assertEqual(CarrierRTOZoneMaster.objects.count(), 1)
        self.assertEqual(loader.result.sheets["CarrierRTOZone"].skipped, 1)

    def test_missing_csv_is_a_noop(self):
        loader = MasterDataLoader()
        loader._load_chola_rto_zone(path=CHOLA_RTO_ZONE_CSV_PATH.parent / "nope.csv")
        self.assertEqual(CarrierRTOZoneMaster.objects.count(), 0)
        self.assertNotIn("CarrierRTOZone", loader.result.sheets)

    def test_committed_csv_loads_all_92_rows(self):
        self.assertTrue(CHOLA_RTO_ZONE_CSV_PATH.exists())
        loader = MasterDataLoader()
        loader._load_chola_rto_zone()
        self.assertEqual(CarrierRTOZoneMaster.objects.count(), 92)
        self.assertEqual(loader.result.sheets["CarrierRTOZone"].skipped, 0)


class CholaProductHierarchyLoaderTests(TestCase):
    def test_upsert_creates_then_updates_not_duplicates(self):
        loader = MasterDataLoader()
        rows = [("Chola", "CV", "GCV", "Truck")]
        loader.upsert_chola_product_hierarchy_rows(rows)
        self.assertEqual(CarrierProductHierarchyMaster.objects.count(), 1)
        self.assertEqual(loader.result.sheets["CarrierProductHierarchy"].created, 1)

        loader2 = MasterDataLoader()
        loader2.upsert_chola_product_hierarchy_rows(rows)
        self.assertEqual(CarrierProductHierarchyMaster.objects.count(), 1)
        self.assertEqual(loader2.result.sheets["CarrierProductHierarchy"].updated, 1)

    def test_canonical_subclass_reuses_seeded_vehicle_subclass_master(self):
        # Seed VehicleSubClassMaster the way _load_subclass would (same
        # canonical spelling VEHICLE_SUBCLASS_CANONICAL produces for CV #24).
        VehicleSubClassMaster.objects.create(
            pb_vehicle_subclass_id=24,
            vehicle_class_id=1,
            subclass_name="Excavaters",
            canonical_name="Excavator",
            product="CV",
            product_id=188,
        )
        loader = MasterDataLoader()
        loader.upsert_chola_product_hierarchy_rows(
            [("Chola", "CV", "MISC-D", "Excavaters")]
        )
        row = CarrierProductHierarchyMaster.objects.get(vehicle_subclass="Excavaters")
        self.assertEqual(row.canonical_subclass, "Excavator")

    def test_canonical_subclass_falls_back_to_raw_when_unseeded(self):
        # "Kaali Peeli Taxi" has no VehicleSubClassMaster row (not in the
        # shared Master Mapper sheet) -> falls back to the raw spelling.
        loader = MasterDataLoader()
        loader.upsert_chola_product_hierarchy_rows(
            [("Chola", "Car", "", "Kaali Peeli Taxi")]
        )
        row = CarrierProductHierarchyMaster.objects.get(
            vehicle_subclass="Kaali Peeli Taxi"
        )
        self.assertEqual(row.canonical_subclass, "Kaali Peeli Taxi")

    def test_blank_vehicle_subclass_skipped_and_counted(self):
        loader = MasterDataLoader()
        loader.upsert_chola_product_hierarchy_rows([("Chola", "CV", "GCV", "")])
        self.assertEqual(CarrierProductHierarchyMaster.objects.count(), 0)
        self.assertEqual(loader.result.sheets["CarrierProductHierarchy"].skipped, 1)

    def test_missing_csv_is_a_noop(self):
        loader = MasterDataLoader()
        loader._load_chola_product_hierarchy(
            path=CHOLA_PRODUCT_HIERARCHY_CSV_PATH.parent / "nope.csv"
        )
        self.assertEqual(CarrierProductHierarchyMaster.objects.count(), 0)

    def test_committed_csv_loads_all_56_rows(self):
        self.assertTrue(CHOLA_PRODUCT_HIERARCHY_CSV_PATH.exists())
        loader = MasterDataLoader()
        loader._load_chola_product_hierarchy()
        self.assertEqual(CarrierProductHierarchyMaster.objects.count(), 56)
        self.assertEqual(loader.result.sheets["CarrierProductHierarchy"].skipped, 0)
