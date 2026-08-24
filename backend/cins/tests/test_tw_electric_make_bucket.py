"""
The Chola Two-Wheeler electric grid prices every electric two-wheeler under ONE
bucket column whose make cell reads "All Electric Make". `chola_pass3.md` tells
codegen to set `fuel_type="Electric"` from that bucket; on doc 1457d5e8 all 84
raw rules of the block came back with the bucket in `vehicle_make` and
`fuel_type` unset, so an electric-only segment exported as fuel-agnostic.

Run from the backend container:
    python manage.py test cins.tests.test_tw_electric_make_bucket -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
from cins.schema.raw_extraction import (
    RawCommissionEntry,
    RawExtraction,
    RawProviderInfo,
)


def _raw(entries):
    return RawExtraction(
        source_file="test.xlsx",
        source_type="excel",
        provider=RawProviderInfo(name="Chola"),
        start_date="2026-04-01",
        end_date="2026-04-30",
        commission_entries=entries,
    )


def _entry(**conditions):
    return RawCommissionEntry(
        product_name="Two Wheeler",
        commission_value="0.17",
        rule_text="TW Electric AP",
        conditions=conditions,
    )


class TwElectricMakeBucketTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_all_electric_make_bucket_sets_fuel_type(self):
        cond = self.t._transform_conditions(
            _entry(vehicle_make="All Electric Make", vehicle_sub_class="bike"),
            None,
            _raw([]),
        )
        self.assertEqual(cond.fuel_type, ["electric"])

    def test_electric_bucket_carries_no_cc_filter(self):
        # kW power bands are not engine CC — the band never reaches conditions,
        # and the electric-two-wheeler reset keeps the open sentinel.
        cond = self.t._transform_conditions(
            _entry(vehicle_make="All Electric Make", vehicle_sub_class="scooter"),
            None,
            _raw([]),
        )
        self.assertEqual((cond.cc_start_range, cond.cc_end_range), (0, 99999))

    def test_an_explicit_fuel_is_never_overridden(self):
        cond = self.t._transform_conditions(
            _entry(vehicle_make="All Electric Make", fuel_type="petrol"), None, _raw([])
        )
        self.assertEqual(cond.fuel_type, ["petrol"])

    def test_an_ordinary_make_is_left_fuel_agnostic(self):
        cond = self.t._transform_conditions(
            _entry(vehicle_make="Hero OEM", cc="150-350"), None, _raw([])
        )
        self.assertIsNone(cond.fuel_type)

    def test_a_make_merely_containing_electric_still_tags(self):
        # Deliberate: any make naming electric is an electric bucket on this
        # corpus, and tagging is the safe direction — an untagged electric rule
        # exports as all five fuels.
        cond = self.t._transform_conditions(
            _entry(vehicle_make="Electric Vehicles"), None, _raw([])
        )
        self.assertEqual(cond.fuel_type, ["electric"])
