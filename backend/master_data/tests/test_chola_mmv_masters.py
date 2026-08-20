"""
Tests for the Chola MMV master loader (``upsert_chola_mmv_rows`` /
``_load_chola_mmv``) and the committed CSV that seeds it.

Run from the backend container:
    python manage.py test master_data.tests.test_chola_mmv_masters -v 2
"""

import csv
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase

from master_data.models import CarrierVehicleMMVMaster
from master_data.services.loader import CHOLA_MMV_CSV_PATH, MasterDataLoader
from master_data.services.mmv import build_mmv_index, match_make

KEY = "CarrierVehicleMMV"


def _row(**overrides):
    row = {
        "carrier": "Chola",
        "product": "Car",
        "make": "MARUTI",
        "model": "SWIFT",
        "variant": "",
        "fuel": "petrol",
        "sub_class": "",
        "vehicle_class": "",
    }
    row.update(overrides)
    return row


class CholaMMVLoaderTests(TestCase):
    def test_upsert_creates_then_updates_not_duplicates(self):
        rows = [_row(), _row(make="TATA", model="NEXON")]

        loader = MasterDataLoader()
        loader.upsert_chola_mmv_rows(rows)
        self.assertEqual(CarrierVehicleMMVMaster.objects.count(), 2)
        self.assertEqual(loader.result.sheets[KEY].created, 2)
        self.assertEqual(loader.result.sheets[KEY].updated, 0)

        loader2 = MasterDataLoader()
        loader2.upsert_chola_mmv_rows(rows)
        self.assertEqual(CarrierVehicleMMVMaster.objects.count(), 2)  # no dup
        self.assertEqual(loader2.result.sheets[KEY].created, 0)
        self.assertEqual(loader2.result.sheets[KEY].updated, 2)

    def test_reseed_refreshes_the_descriptive_extras(self):
        MasterDataLoader().upsert_chola_mmv_rows([_row(fuel="petrol")])
        MasterDataLoader().upsert_chola_mmv_rows([_row(fuel="cng")])
        self.assertEqual(CarrierVehicleMMVMaster.objects.get().fuel, "cng")

    def test_slugs_are_recomputed_not_read_from_the_csv(self):
        """The CSV deliberately does not carry the slug columns — they are a
        pure function of make/model/variant, and storing them would let the
        seeded keys drift from the code the matcher queries with."""
        MasterDataLoader().upsert_chola_mmv_rows(
            [_row(make="MAHINDRA AND MAHINDRA", model="BOLERO")]
        )
        obj = CarrierVehicleMMVMaster.objects.get()
        self.assertEqual(obj.make_slug, "mahindra")
        self.assertEqual(obj.model_slug, "bolero")

    def test_rows_colliding_within_one_payload_keep_the_first(self):
        loader = MasterDataLoader()
        loader.upsert_chola_mmv_rows(
            [
                _row(make="TATA MOTORS PVT LTD", model="407", fuel="diesel"),
                _row(make="Tata Motors", model="407", fuel="cng"),
            ]
        )
        self.assertEqual(CarrierVehicleMMVMaster.objects.count(), 1)
        self.assertEqual(CarrierVehicleMMVMaster.objects.get().fuel, "diesel")
        self.assertEqual(loader.result.sheets[KEY].skipped, 1)

    def test_rows_naming_no_vehicle_are_skipped(self):
        loader = MasterDataLoader()
        loader.upsert_chola_mmv_rows(
            [_row(make=""), _row(model=""), _row(product=""), _row(carrier="")]
        )
        self.assertEqual(CarrierVehicleMMVMaster.objects.count(), 0)
        self.assertEqual(loader.result.sheets[KEY].skipped, 4)

    def test_same_make_model_in_two_products_are_distinct_rows(self):
        MasterDataLoader().upsert_chola_mmv_rows(
            [_row(product="Car"), _row(product="CV")]
        )
        self.assertEqual(CarrierVehicleMMVMaster.objects.count(), 2)

    def test_variant_separates_otherwise_identical_rows(self):
        MasterDataLoader().upsert_chola_mmv_rows(
            [
                _row(
                    product="TW", make="BAJAJ", model="AVENGER", variant="STD (180 CC)"
                ),
                _row(
                    product="TW", make="BAJAJ", model="AVENGER", variant="STD (200 CC)"
                ),
            ]
        )
        self.assertEqual(CarrierVehicleMMVMaster.objects.count(), 2)

    def test_missing_csv_is_a_no_op(self):
        with TemporaryDirectory() as tmp:
            MasterDataLoader()._load_chola_mmv(Path(tmp) / "absent.csv")
        self.assertEqual(CarrierVehicleMMVMaster.objects.count(), 0)

    def test_load_from_csv_path(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "chola_mmv.csv"
            rows = [_row(), _row(make="TATA", model="NEXON")]
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            MasterDataLoader()._load_chola_mmv(path)
        self.assertEqual(CarrierVehicleMMVMaster.objects.count(), 2)


class CholaMMVCommittedCsvTests(TestCase):
    def test_committed_csv_exists_and_covers_all_three_products(self):
        self.assertTrue(CHOLA_MMV_CSV_PATH.exists(), CHOLA_MMV_CSV_PATH)
        with open(CHOLA_MMV_CSV_PATH, newline="", encoding="utf-8") as fh:
            products = {row["product"] for row in csv.DictReader(fh)}
        self.assertEqual(products, {"Car", "TW", "CV"})

    def test_csv_carries_neither_derived_slugs_nor_unread_numeric_specs(self):
        """Slugs are recomputed at load (so they cannot drift from the
        matcher); the numeric specs are dropped because nothing reads them
        and they dominated the committed file's size."""
        with open(CHOLA_MMV_CSV_PATH, newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh))
        for absent in (
            "make_slug",
            "model_slug",
            "variant_slug",
            "cc",
            "gvw",
            "seating_capacity",
            "wheels",
        ):
            self.assertNotIn(absent, header)


class CholaMMVIndexTests(TestCase):
    def test_index_is_built_from_the_seeded_rows(self):
        MasterDataLoader().upsert_chola_mmv_rows(
            [
                _row(product="CV", make="TATA MOTORS PVT LTD", model="407"),
                _row(product="Car", make="TATA", model="NEXON"),
            ]
        )
        index = build_mmv_index("Chola")
        # Makes match CARRIER-WIDE, so a make seeded only on Car resolves for
        # a CV rule and vice versa. Note the two spellings are NOT folded
        # together: "TATA MOTORS" is a different slug from "TATA" now that
        # industry words count as identity.
        self.assertEqual(
            match_make(index, "Car", "Tata Motors Pvt Ltd"), "TATA MOTORS PVT LTD"
        )
        self.assertEqual(match_make(index, "CV", "Tata"), "TATA")

    def test_index_for_an_unseeded_carrier_is_empty(self):
        self.assertEqual(build_mmv_index("ICICI"), {"_makes": {}})

    def test_a_blank_carrier_never_queries(self):
        from master_data.services.resolver import carrier_vehicle_mmv

        MasterDataLoader().upsert_chola_mmv_rows([_row()])
        self.assertEqual(carrier_vehicle_mmv(""), [])
        self.assertEqual(carrier_vehicle_mmv(None), [])

    def test_resolver_returns_only_the_columns_the_matcher_needs(self):
        """The descriptive extras stay in the DB — this table is ~22k rows
        per carrier and the index is rebuilt per document."""
        from master_data.services.resolver import carrier_vehicle_mmv

        MasterDataLoader().upsert_chola_mmv_rows([_row()])
        self.assertEqual(
            set(carrier_vehicle_mmv("Chola")[0]),
            {"product", "make", "model", "make_slug", "model_slug"},
        )
