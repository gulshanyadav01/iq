"""Tests for RTO state-slug derivation and product-active resolution (Fix v).

Run from the backend container:
    python manage.py test master_data.tests.test_rto -v 2
"""

import tempfile
from pathlib import Path

from django.test import TestCase

from master_data.models import RTOMaster
from master_data.services.loader import RTO_CSV_PATH, MasterDataLoader
from master_data.services.resolver import (
    MasterDataResolver,
    match_rto_localities,
    rto_locality_expansions,
)
from master_data.services.rto import state_slug_for_code


class StateSlugForCodeTests(TestCase):
    def test_prefix_maps_to_canonical_slug(self):
        self.assertEqual(state_slug_for_code("AP01"), "andhra-pradesh")
        self.assertEqual(state_slug_for_code("OD05"), "orissa")
        self.assertEqual(state_slug_for_code("TS09"), "telangana")
        self.assertEqual(state_slug_for_code("UK07"), "uttarakhand")

    def test_combined_ut_prefixes(self):
        self.assertEqual(
            state_slug_for_code("DD01"), "dadra-and-nagar-haveli-and-daman-and-diu"
        )
        self.assertEqual(
            state_slug_for_code("DN09"), "dadra-and-nagar-haveli-and-daman-and-diu"
        )

    def test_unmapped_or_malformed_returns_none(self):
        self.assertIsNone(state_slug_for_code(""))
        self.assertIsNone(state_slug_for_code("ZZ01"))  # no such prefix
        self.assertIsNone(state_slug_for_code("1234"))  # not alpha
        self.assertIsNone(state_slug_for_code("A"))  # too short


class RtosForStatesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # AP: one car-active, one cv-active, one inactive-everywhere.
        RTOMaster.objects.create(
            rto_id=1,
            code="AP01",
            value="AP01-Adilabad(Andhra Pradesh)(Group B)",
            state_slug="andhra-pradesh",
            car_zone=2,
            tw_zone=None,
            cv_zone=3,
        )
        RTOMaster.objects.create(
            rto_id=2,
            code="AP02",
            value="AP02-Anantapur(Andhra Pradesh)(Group A)",
            state_slug="andhra-pradesh",
            car_zone=1,
            tw_zone=1,
            cv_zone=None,
        )
        RTOMaster.objects.create(
            rto_id=3,
            code="AP03",
            value="AP03-Chittoor(Andhra Pradesh)(Group C)",
            state_slug="andhra-pradesh",
            car_zone=None,
            tw_zone=None,
            cv_zone=None,
        )
        RTOMaster.objects.create(
            rto_id=4,
            code="KA01",
            value="KA01-Bangalore(Karnataka)(Group A)",
            state_slug="karnataka",
            car_zone=1,
            tw_zone=1,
            cv_zone=1,
        )

    def setUp(self):
        self.r = MasterDataResolver()

    def test_car_returns_only_car_active_sorted(self):
        got = self.r.rtos_for_states(["andhra-pradesh"], "CAR")
        self.assertEqual(
            got,
            [
                "AP01-Adilabad(Andhra Pradesh)(Group B)",
                "AP02-Anantapur(Andhra Pradesh)(Group A)",
            ],
        )

    def test_car_set_differs_from_cv_set(self):
        car = self.r.rtos_for_states(["andhra-pradesh"], "CAR")
        cv = self.r.rtos_for_states(["andhra-pradesh"], "CV")
        self.assertNotEqual(car, cv)
        self.assertEqual(cv, ["AP01-Adilabad(Andhra Pradesh)(Group B)"])

    def test_product_active_subset_of_all_state_rtos(self):
        car = set(self.r.rtos_for_states(["andhra-pradesh"], "CAR"))
        all_ap = set(
            RTOMaster.objects.filter(state_slug="andhra-pradesh").values_list(
                "value", flat=True
            )
        )
        self.assertTrue(car.issubset(all_ap))
        self.assertTrue(car)  # non-empty

    def test_multi_state(self):
        got = self.r.rtos_for_states(["andhra-pradesh", "karnataka"], "TW")
        self.assertEqual(
            got,
            [
                "AP02-Anantapur(Andhra Pradesh)(Group A)",
                "KA01-Bangalore(Karnataka)(Group A)",
            ],
        )

    def test_unknown_product_returns_empty(self):
        self.assertEqual(self.r.rtos_for_states(["andhra-pradesh"], "BOAT"), [])

    def test_empty_or_unmapped_state_returns_empty(self):
        self.assertEqual(self.r.rtos_for_states([], "CAR"), [])
        self.assertEqual(self.r.rtos_for_states(["narnia"], "CAR"), [])


class RtoLocalityFallbackTests(TestCase):
    """The global-master fallback for a metro a carrier's own zone master does
    not cover -- Chola keys a Two-Wheeler row ``KA (Bangalore)`` but
    ``chola_rto_zone.csv`` seeds no bangalore zone.
    """

    @classmethod
    def setUpTestData(cls):
        rows = [
            # Bangalore: the metro the fallback exists for.
            (1, "KA01", "KA01-Bangalore(Karnataka)(Group A)", "karnataka"),
            (2, "KA02", "KA02-Bangalore(Karnataka)(Group A)", "karnataka"),
            # Same state, different locality -- must NOT come along.
            (3, "KA20", "KA20-Mysore(Karnataka)(Group C)", "karnataka"),
            # Tamil Nadu, so a "TN/ROK" cell has a second state in scope.
            (4, "TN01", "TN01-Chennai(Tamil Nadu)(Group A)", "tamil-nadu"),
            # Same locality name in two states -- the scope is the disambiguator.
            (5, "AP09", "AP09-Hyderabad(Andhra Pradesh)(Group A)", "andhra-pradesh"),
            (6, "TS09", "TS09-Hyderabad(Telangana)(Group A)", "telangana"),
            # No "(Group X)" suffix: two rows in the shipped master look like
            # this, and they must still index.
            (7, "UP29", "UP29-Padrauna(Uttar Pradesh)", "uttar-pradesh"),
        ]
        for rto_id, code, value, slug in rows:
            RTOMaster.objects.create(
                rto_id=rto_id,
                code=code,
                value=value,
                state_slug=slug,
                car_zone=1,
                tw_zone=1,
                cv_zone=1,
            )

    def setUp(self):
        self.index = rto_locality_expansions()

    def test_metro_label_resolves_only_that_localitys_codes(self):
        self.assertEqual(
            match_rto_localities(self.index, ["karnataka"], "KA (Bangalore)"),
            ["KA01", "KA02"],
        )

    def test_state_scope_disambiguates_a_shared_locality_name(self):
        """48 locality names in the shipped master span more than one state."""
        self.assertEqual(
            match_rto_localities(self.index, ["telangana"], "TS (Hyderabad)"),
            ["TS09"],
        )
        self.assertEqual(
            match_rto_localities(self.index, ["andhra-pradesh"], "AP (Hyderabad)"),
            ["AP09"],
        )

    def test_state_scope_blocks_an_out_of_scope_locality(self):
        self.assertEqual(
            match_rto_localities(self.index, ["tamil-nadu"], "KA (Bangalore)"), []
        )

    def test_abbreviation_and_remainder_tokens_never_match(self):
        """The ≥4-char + word-boundary rule is what keeps these inert. A false
        hit here fills the export's RTO cell with another geography's codes.
        """
        for label in ("TN/ROK", "KA", "ROM", "ROTN", "NE", "AP/TS", "UP- East"):
            with self.subTest(label=label):
                self.assertEqual(
                    match_rto_localities(
                        self.index,
                        ["karnataka", "tamil-nadu", "uttar-pradesh"],
                        label,
                    ),
                    [],
                )

    def test_value_without_group_suffix_still_indexes(self):
        self.assertEqual(
            match_rto_localities(self.index, ["uttar-pradesh"], "UP (Padrauna)"),
            ["UP29"],
        )

    def test_empty_inputs_return_empty(self):
        self.assertEqual(match_rto_localities(self.index, [], "Bangalore"), [])
        self.assertEqual(match_rto_localities(self.index, ["karnataka"], ""), [])
        self.assertEqual(match_rto_localities({}, ["karnataka"], "Bangalore"), [])


class LoadRtoCsvTests(TestCase):
    def test_csv_maps_mapped_rows_and_counts_unmapped(self):
        # 2 mapped prefixes + 1 unmapped (ZZ) -> 2 created, 1 skipped.
        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, newline=""
        ) as f:
            f.write("rto_id,code,value,car_zone,tw_zone,cv_zone\n")
            f.write("1,AP01,AP01-Adilabad(Andhra Pradesh)(Group B),2,,3\n")
            f.write("2,KA01,KA01-Bangalore(Karnataka)(Group A),1,1,1\n")
            f.write("3,ZZ09,ZZ09-Nowhere(Atlantis)(Group A),1,1,1\n")
            tmp = Path(f.name)
        loader = MasterDataLoader()
        loader._load_rto(tmp)
        tmp.unlink()
        self.assertEqual(RTOMaster.objects.count(), 2)
        self.assertEqual(loader.result.sheets["RTO"].created, 2)
        self.assertEqual(loader.result.sheets["RTO"].skipped, 1)
        ap = RTOMaster.objects.get(code="AP01")
        self.assertEqual(ap.state_slug, "andhra-pradesh")
        self.assertEqual(ap.group, "B")
        self.assertEqual(ap.car_zone, 2)
        self.assertIsNone(ap.tw_zone)

    def test_repo_csv_loads_all_rows(self):
        # The committed rto_master.csv seeds every mapped RTO.
        self.assertTrue(RTO_CSV_PATH.exists(), f"missing {RTO_CSV_PATH}")
        loader = MasterDataLoader()
        loader._load_rto()
        # 1721 distinct RTO codes; 1696 active for each product.
        self.assertEqual(RTOMaster.objects.count(), 1721)
        self.assertEqual(RTOMaster.objects.exclude(car_zone=None).count(), 1696)
        self.assertEqual(RTOMaster.objects.exclude(cv_zone=None).count(), 1696)
