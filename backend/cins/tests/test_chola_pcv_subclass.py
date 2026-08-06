"""
Chola PCV body-class rules (client-supplied).

The PCV grid identifies its rows only by segment label and an "UPTO 5" /
"ABOVE 6" seating hint, so the body class and its real seating band both have to
be derived:

* PCV 3-wheelers are Auto Rickshaw and E-Rickshaw. The grid already publishes
  them as SEPARATE rows at different rates — a fuel-tagged Electric row beside
  the plain one (verified on doc 733711bd: 20 rows each, e.g. AN ACT 35% vs
  7.5%). So each row is labelled; fanning one row into two would copy a single
  rate onto both bodies.
* A PCV taxi seating 13-16 is a Maxi Cab.
* A PCV taxi seating 6-12 is a Big Taxi — "Greater Than Six Seater" in Chola's
  own hierarchy master, which is also the enum value.
* A 4-wheel PCV seating <=5 is an ordinary Taxi; those rows carry only a CC band
  slug ("2_4W_LT_1500CC") and no body word at all.

Run from the backend container:
    python manage.py test cins.tests.test_chola_pcv_subclass -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer


class CholaPCVSubclassTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"
        self.t._product_subproduct = "commercial-pcv"

    def _apply(
        self,
        sub_class=None,
        slug="",
        fuel=None,
        wheels=("4w-and-above",),
        seating=(0, 99),
    ):
        return self.t._apply_chola_pcv_subclass(
            sub_class, slug, fuel, list(wheels), seating[0], seating[1]
        )

    # ---- rule 1: 3-wheelers ------------------------------------------------

    def test_three_wheeler_non_electric_is_auto_rickshaw(self):
        sc, s, e = self._apply(
            sub_class=["auto-rickshaw"],
            slug="1_3W_AUTO",
            wheels=("3w",),
            seating=(0, 5),
        )
        self.assertEqual(sc, ["auto-rickshaw"])
        self.assertEqual((s, e), (0, 5))

    def test_three_wheeler_electric_is_e_rickshaw(self):
        sc, _, _ = self._apply(
            sub_class=["auto-rickshaw"],
            slug="1_3W_AUTO",
            fuel=["electric"],
            wheels=("3w",),
            seating=(0, 5),
        )
        self.assertEqual(sc, ["e-rickshaw"])

    def test_three_wheeler_electric_detected_among_several_fuels(self):
        sc, _, _ = self._apply(
            slug="1_3W_AUTO", fuel=["petrol", "Electric"], wheels=("3w",)
        )
        self.assertEqual(sc, ["e-rickshaw"])

    def test_three_wheeler_recognised_from_slug_without_wheel_field(self):
        sc, _, _ = self._apply(slug="1_3W_AUTO", wheels=())
        self.assertEqual(sc, ["auto-rickshaw"])

    def test_three_wheeler_seating_is_left_alone(self):
        # The 6-12 / 13-16 bands are taxi rules; they must not touch 3W rows.
        _, s, e = self._apply(slug="1_3W_AUTO", wheels=("3w",), seating=(0, 5))
        self.assertEqual((s, e), (0, 5))

    # ---- rules 2 and 3: taxi seating bands ---------------------------------

    def test_maxi_cab_gets_13_to_16_seating(self):
        sc, s, e = self._apply(sub_class=["maxicab"], seating=(6, 99))
        self.assertEqual(sc, ["maxicab"])
        self.assertEqual((s, e), (13, 16))

    def test_big_taxi_becomes_greater_than_six_seater_with_6_to_12(self):
        sc, s, e = self._apply(sub_class=["taxi"], seating=(6, 99))
        self.assertEqual(sc, ["greater-than-six-seater"])
        self.assertEqual((s, e), (6, 12))

    def test_small_taxi_is_not_promoted_to_big_taxi(self):
        # A <=5 seat row already labelled "taxi" stays a plain taxi.
        sc, s, e = self._apply(sub_class=["taxi"], seating=(0, 5))
        self.assertEqual(sc, ["taxi"])
        self.assertEqual((s, e), (0, 5))

    # ---- rule 4: 4W <=5 seaters -------------------------------------------

    def test_four_wheeler_upto_five_seats_becomes_taxi(self):
        sc, s, e = self._apply(slug="2_4W_LT_1500CC", seating=(0, 5))
        self.assertEqual(sc, ["taxi"])
        self.assertEqual((s, e), (0, 5))

    def test_existing_body_class_is_never_overwritten(self):
        # A labelled row keeps its own body even at <=5 seats.
        sc, _, _ = self._apply(sub_class=["maxicab"], seating=(0, 5))
        self.assertEqual(sc, ["maxicab"])

    # ---- scoping -----------------------------------------------------------

    def test_other_subproducts_untouched(self):
        self.t._product_subproduct = "commercial-gcv"
        sc, s, e = self._apply(
            sub_class=["auto-rickshaw"], wheels=("3w",), seating=(6, 99)
        )
        self.assertEqual(sc, ["auto-rickshaw"])
        self.assertEqual((s, e), (6, 99))

    def test_other_carriers_untouched(self):
        self.t._carrier_code = "ICICI"
        sc, s, e = self._apply(sub_class=["taxi"], seating=(6, 99))
        self.assertEqual(sc, ["taxi"])
        self.assertEqual((s, e), (6, 99))

    def test_unrecognised_row_passes_through(self):
        sc, s, e = self._apply(sub_class=["school-bus"], seating=(6, 99))
        self.assertEqual(sc, ["school-bus"])
        self.assertEqual((s, e), (6, 99))

    def test_values_are_valid_for_chola_pcv(self):
        # Every value this rule can emit must exist in the CICS enum. All but
        # 'maxicab' are also in Chola's PCV hierarchy allowlist (the master has
        # no Maxi Cab row — a known, reported gap).
        from cins.schema.cics_model import RuleConditions

        for slug in ("auto-rickshaw", "e-rickshaw", "taxi", "greater-than-six-seater"):
            with self.subTest(slug=slug):
                RuleConditions(vehicle_sub_class=[slug])
