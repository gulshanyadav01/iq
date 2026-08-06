"""
Chola two-wheeler body class, and the rate-leaked-into-cc guard.

Each TW make group publishes four columns — a SCOOTER column beside three
motorcycle displacement bands::

    | Hero OEM > 150cc | Hero OEM > SCOOTER | Hero OEM > 150_350cc | Hero OEM > 350cc |

Scooters are not cc-graded here, motorcycles are, so a cc-graded column is a
Bike. Codegen only carried the body word through for the SCOOTER column; the cc
columns arrived with no sub-class (1101 entries on doc 733711bd), leaving 1523
rules failing Chola's mandatory-vehicle_sub_class check.

The `All Electric Make` group already resolves to bike/scooter with fuel_type
Electric and must be left alone — the rates differ between electric and
non-electric.

Separately, 124 of those entries carried a commission RATE in the cc field
(0.37, 0.22, -0.205, -0.055). Left alone that becomes a bogus tight band which
silently excludes every real policy.

Run from the backend container:
    python manage.py test cins.tests.test_chola_tw_subclass -v 2
"""

from types import SimpleNamespace
from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer


def _entry(rule_text="", source_section=""):
    return SimpleNamespace(rule_text=rule_text, source_section=source_section)


class CholaTWSubclassTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"
        self.t._product_subproduct = "two-wheeler"

    def _apply(self, sub_class=None, rule_text="", cc=(0, 99999)):
        return self.t._apply_chola_tw_subclass(
            sub_class, _entry(rule_text), cc[0], cc[1]
        )

    def test_cc_band_column_is_a_bike(self):
        for text in (
            "Two Wheeler Hero OEM 150cc AP NEW(30-100 NOP)",
            "Two Wheeler TVS OEM 150_350cc AP NEW(30-100 NOP)",
            "Two Wheeler Bajaj 350cc MH ACT",
        ):
            with self.subTest(text=text):
                self.assertEqual(self._apply(rule_text=text), ["bike"])

    def test_scooter_column_is_a_scooter(self):
        self.assertEqual(
            self._apply(rule_text="Two Wheeler Hero OEM SCOOTER AP ACT"), ["scooter"]
        )

    def test_scooter_wins_over_a_stray_cc_token(self):
        # A scooter row whose cc leaked a rate still names SCOOTER in rule_text.
        self.assertEqual(
            self._apply(rule_text="Two Wheeler SCOOTER 0.37 150cc"), ["scooter"]
        )

    def test_parsed_cc_band_implies_bike_without_a_text_token(self):
        self.assertEqual(
            self._apply(rule_text="Two Wheeler AP ACT", cc=(150, 350)), ["bike"]
        )

    def test_open_cc_sentinel_proves_nothing(self):
        self.assertIsNone(self._apply(rule_text="Two Wheeler AP ACT", cc=(0, 99999)))

    def test_existing_subclass_is_never_overwritten(self):
        # The electric rows arrive already resolved; leave them exactly as-is.
        self.assertEqual(
            self._apply(
                sub_class=["scooter"],
                rule_text="Two Wheeler All Electric Make 7 KW SCOOTER AP ACT",
            ),
            ["scooter"],
        )
        self.assertEqual(
            self._apply(
                sub_class=["bike"],
                rule_text="Two Wheeler All Electric Make 7 KW Bike AP ACT",
            ),
            ["bike"],
        )

    def test_other_products_untouched(self):
        self.t._product_subproduct = "commercial-gcv"
        self.assertIsNone(self._apply(rule_text="GCV 150cc"))

    def test_other_carriers_untouched(self):
        self.t._carrier_code = "ICICI"
        self.assertIsNone(self._apply(rule_text="Two Wheeler Hero OEM 150cc"))

    def test_emitted_values_are_valid_for_chola_tw(self):
        # Chola's TW allowlist is Bike / E-Bike / Moped / Scooter.
        from cins.schema.cics_model import RuleConditions

        for slug in ("bike", "scooter"):
            with self.subTest(slug=slug):
                RuleConditions(vehicle_sub_class=[slug])


class LeakedCCRateTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_fractional_rate_is_discarded_and_warned(self):
        self.assertIsNone(self.t._cc_or_leaked_rate("0.37"))
        self.assertTrue(
            any("Discarded implausible cc" in w for w in self.t.warnings),
            self.t.warnings,
        )

    def test_negative_rate_is_discarded(self):
        self.assertIsNone(self.t._cc_or_leaked_rate("-0.205"))

    def test_real_displacements_survive(self):
        for cc in ("150", "350", "1500", "50"):
            with self.subTest(cc=cc):
                self.t.warnings = []
                self.assertEqual(self.t._cc_or_leaked_rate(cc), cc)
                self.assertEqual(self.t.warnings, [])

    def test_range_expressions_are_untouched(self):
        for text in ("UPTO 150", "150-350", "ABOVE 350"):
            with self.subTest(text=text):
                self.assertEqual(self.t._cc_or_leaked_rate(text), text)

    def test_none_and_empty_pass_through(self):
        self.assertIsNone(self.t._cc_or_leaked_rate(None))
        self.assertEqual(self.t._cc_or_leaked_rate(""), "")
        self.assertEqual(self.t.warnings, [])

    def test_non_scalar_values_pass_through(self):
        self.assertEqual(self.t._cc_or_leaked_rate(["150", "350"]), ["150", "350"])
