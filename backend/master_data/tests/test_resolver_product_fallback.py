"""Tests for product_id resolution via VEHICLE_CLASS_FALLBACK.

resolve_product_id must accept the short product CODE ("CV"/"Car"/"TW") the parser
emits, not only the full names / vehicle-class keywords. These are pure dict->Resolution
checks, so we build a bare instance (no DB/SDK) with an empty Vehicle Master to force
the fallback path.

Run from the backend container:
    python manage.py test master_data.tests.test_resolver_product_fallback -v 2
"""

import unittest

from master_data.services.resolver import MasterDataResolver


class ResolveProductIdFallbackTests(unittest.TestCase):
    def setUp(self):
        self.r = MasterDataResolver.__new__(MasterDataResolver)
        self.r._vehicle = {}  # empty Vehicle Master -> exercise VEHICLE_CLASS_FALLBACK

    def test_cv_code_resolves_to_188(self):
        res = self.r.resolve_product_id("CV")
        self.assertTrue(res.matched)
        self.assertEqual(res.id, 188)

    def test_cv_code_is_case_insensitive(self):
        self.assertEqual(self.r.resolve_product_id("cv").id, 188)

    def test_commercial_vehicle_name_resolves(self):
        self.assertEqual(self.r.resolve_product_id("Commercial Vehicle").id, 188)

    def test_car_and_tw_codes(self):
        self.assertEqual(self.r.resolve_product_id("Car").id, 186)
        self.assertEqual(self.r.resolve_product_id("TW").id, 187)

    def test_bus_class_still_resolves(self):
        self.assertEqual(self.r.resolve_product_id("Bus").id, 188)

    def test_unknown_and_empty_miss(self):
        self.assertFalse(self.r.resolve_product_id("ZZZ").matched)
        self.assertFalse(self.r.resolve_product_id("").matched)


class ResolveProductIdMultiTests(unittest.TestCase):
    """Hierarchy: product -> vehicle class -> vehicle subclass -> carrier.

    Pure dict->Resolution checks: empty Vehicle Master forces the class/product
    signals through VEHICLE_CLASS_FALLBACK, and the subclass index is hand-built
    (keyed by product id, mirroring the string-derived index) so no DB is needed.
    ``taxi`` maps to both Car and CV to exercise the ambiguity guard.
    """

    def setUp(self):
        self.r = MasterDataResolver.__new__(MasterDataResolver)
        self.r._vehicle = {}
        self.r._subclass_product_index = {
            "e rickshaw": {188},
            "tipper": {188},
            "private car": {186},
            "bike": {187},
            "taxi": {186, 188},
        }

    def _multi(self, **kw):
        return self.r.resolve_product_id_multi(**kw)

    def test_all_signals_agree_cv(self):
        res = self._multi(
            product_text="CV",
            vehicle_class_text="PCV",
            vehicle_subclass_text="E Rickshaw",
            carrier_text="Public",
        )
        self.assertTrue(res.matched)
        self.assertEqual(res.id, 188)
        self.assertFalse(res.conflict)

    def test_car_via_subclass_not_188(self):
        # Guards the Master-Mapper data bug (Car subclasses stamped ProductId 188).
        res = self._multi(vehicle_subclass_text="Private Car")
        self.assertEqual(res.id, 186)
        self.assertFalse(res.conflict)

    def test_tw_via_subclass(self):
        self.assertEqual(self._multi(vehicle_subclass_text="Bike").id, 187)

    def test_fall_through_to_class(self):
        res = self._multi(vehicle_class_text="Private Car")
        self.assertEqual(res.id, 186)

    def test_subclass_only(self):
        self.assertEqual(self._multi(vehicle_subclass_text="Tipper").id, 188)

    def test_carrier_only_commercial(self):
        self.assertEqual(self._multi(carrier_text="COMMERCIAL").id, 188)

    def test_carrier_private_alone_misses(self):
        self.assertFalse(self._multi(carrier_text="PRIVATE").matched)

    def test_contradiction_flags_conflict(self):
        res = self._multi(vehicle_class_text="Private Car", carrier_text="COMMERCIAL")
        self.assertEqual(res.id, 186)  # class wins (higher priority)
        self.assertTrue(res.conflict)  # carrier -> CV disagrees

    def test_ambiguous_subclass_contributes_nothing(self):
        self.assertFalse(self._multi(vehicle_subclass_text="Taxi").matched)

    def test_all_empty_misses(self):
        self.assertFalse(self._multi().matched)


if __name__ == "__main__":
    unittest.main()
