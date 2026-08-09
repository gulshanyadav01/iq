"""
Tests for the ICICI RTO-grid loader (``_load_icici_rto_grid``) and the
committed CSV that seeds it. The upsert itself is exercised generically by
``test_chola_masters.py`` (``upsert_carrier_rto_zone_rows`` is carrier-
agnostic) — these tests focus on ICICI's own CSV path and its coexistence
with Chola's rows in the same table.

Run from the backend container:
    python manage.py test master_data.tests.test_icici_masters -v 2
"""

from django.test import TestCase

from master_data.models import CarrierRTOZoneMaster
from master_data.services.loader import ICICI_RTO_GRID_CSV_PATH, MasterDataLoader


class IciciRtoGridLoaderTests(TestCase):
    def test_missing_csv_is_a_noop(self):
        loader = MasterDataLoader()
        loader._load_icici_rto_grid(path=ICICI_RTO_GRID_CSV_PATH.parent / "nope.csv")
        self.assertEqual(CarrierRTOZoneMaster.objects.count(), 0)

    def test_committed_csv_loads_all_1703_rows(self):
        self.assertTrue(ICICI_RTO_GRID_CSV_PATH.exists())
        loader = MasterDataLoader()
        loader._load_icici_rto_grid()
        self.assertEqual(
            CarrierRTOZoneMaster.objects.filter(carrier="ICICI").count(), 1703
        )
        self.assertEqual(loader.result.sheets["CarrierRTOZone"].skipped, 0)

    def test_coexists_with_other_carriers_in_same_table(self):
        loader = MasterDataLoader()
        loader.upsert_carrier_rto_zone_rows([("Chola", "mumbai", "MH01")])
        loader.upsert_carrier_rto_zone_rows([("ICICI", "telangana", "AP01")])
        self.assertEqual(CarrierRTOZoneMaster.objects.count(), 2)
        self.assertEqual(
            CarrierRTOZoneMaster.objects.get(carrier="ICICI").zone, "telangana"
        )

    def test_idempotent_reload(self):
        loader = MasterDataLoader()
        loader._load_icici_rto_grid()
        count_after_first = CarrierRTOZoneMaster.objects.filter(carrier="ICICI").count()

        loader2 = MasterDataLoader()
        loader2._load_icici_rto_grid()
        self.assertEqual(
            CarrierRTOZoneMaster.objects.filter(carrier="ICICI").count(),
            count_after_first,
        )
        self.assertEqual(loader2.result.sheets["CarrierRTOZone"].created, 0)
        self.assertEqual(
            loader2.result.sheets["CarrierRTOZone"].updated, count_after_first
        )
