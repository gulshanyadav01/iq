"""
Chola: ``vehicle_sub_class`` is mandatory on every rule.

Covers the transformer-side backfill that derives it from the product/segment
label. What is NOT derivable is deliberately left empty for
``CholaProductHierarchyValidator`` to flag as a HIGH error — see
``test_chola_product_validator.MandatorySubClassTests``.

Run from the backend container:
    python manage.py test cins.tests.test_chola_mandatory_sub_class -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
from cins.schema.raw_extraction import (
    RawCommissionEntry,
    RawExtraction,
    RawProviderInfo,
)


def _raw(entries, carrier="Chola"):
    return RawExtraction(
        source_file="test.xlsx",
        source_type="excel",
        provider=RawProviderInfo(name=carrier),
        start_date="2024-07-01",
        end_date="2024-07-31",
        commission_entries=entries,
    )


def _sub_classes(doc):
    """Every rule's vehicle_sub_class across the whole document."""
    out = []
    for product in doc.products:
        for rule in product.commission_rules:
            cond = rule.conditions
            out.append(getattr(cond, "vehicle_sub_class", None) if cond else None)
    return out


class CarBackfillTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_private_car_defaults_when_no_body_word_anywhere(self):
        # A Chola Car grid labelled only by a CC band is the Private Car book.
        doc, _, _ = self.t.transform(
            _raw(
                [
                    RawCommissionEntry(
                        rule_text="UPTO 1000 CC",
                        commission_value="30%",
                        product_name="Private Car",
                    )
                ]
            )
        )
        self.assertEqual(_sub_classes(doc), [["private-car"]])

    def test_taxi_segment_wins_over_the_private_car_default(self):
        doc, _, _ = self.t.transform(
            _raw(
                [
                    RawCommissionEntry(
                        rule_text="UPTO 1000 CC",
                        commission_value="20%",
                        product_name="Private Car",
                        conditions={"vehicle_sub_class": "taxi"},
                    )
                ]
            )
        )
        self.assertEqual(_sub_classes(doc), [["taxi"]])

    def test_existing_sub_class_is_never_overwritten(self):
        doc, _, _ = self.t.transform(
            _raw(
                [
                    RawCommissionEntry(
                        rule_text="UPTO 1000 CC",
                        commission_value="20%",
                        product_name="Private Car",
                        conditions={"vehicle_sub_class": "kaali peeli taxi"},
                    )
                ]
            )
        )
        self.assertEqual(_sub_classes(doc), [["kaali-peeli-taxi"]])

    def test_non_chola_car_is_not_backfilled(self):
        t = RawToCICSTransformer()
        doc, _, _ = t.transform(
            _raw(
                [
                    RawCommissionEntry(
                        rule_text="UPTO 1000 CC",
                        commission_value="30%",
                        product_name="Private Car",
                    )
                ],
                carrier="Bajaj Allianz",
            )
        )
        self.assertEqual(_sub_classes(doc), [None])


class TwoWheelerHintTests(TestCase):
    """TW has four genuinely distinct bodies and no majority case, so it is
    never blindly defaulted.

    An explicit body word always wins. Failing that, the grid's own grading axis
    decides: each make group publishes a SCOOTER column beside three motorcycle
    displacement bands ("150cc", "150_350cc", "350cc"), so a cc-graded TW row is
    a Bike (client-confirmed). A row with neither a body word nor a cc grade is
    still left empty rather than guessed at."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def test_body_word_in_the_section_label_is_picked_up(self):
        for section, expected in (
            ("TW SCOOTER", "scooter"),
            ("TW BIKE", "bike"),
            ("TW E-BIKE", "e-bike"),
            ("TW MOPED", "moped"),
        ):
            with self.subTest(section=section):
                doc, _, _ = RawToCICSTransformer().transform(
                    _raw(
                        [
                            RawCommissionEntry(
                                rule_text="UPTO 150 CC",
                                commission_value="5%",
                                product_name="Two Wheeler",
                                source_section=section,
                            )
                        ]
                    )
                )
                self.assertEqual(_sub_classes(doc), [[expected]])

    def test_cc_graded_two_wheeler_is_a_bike(self):
        # No body word anywhere, but the row is displacement-graded — in this
        # grid only motorcycles are.
        doc, _, _ = self.t.transform(
            _raw(
                [
                    RawCommissionEntry(
                        rule_text="UPTO 150 CC",
                        commission_value="5%",
                        product_name="Two Wheeler",
                    )
                ]
            )
        )
        self.assertEqual(_sub_classes(doc), [["bike"]])

    def test_two_wheeler_with_no_body_word_and_no_cc_grade_is_left_empty(self):
        # Nothing to go on at all — still never guessed.
        doc, _, _ = self.t.transform(
            _raw(
                [
                    RawCommissionEntry(
                        rule_text="AP NEW",
                        commission_value="5%",
                        product_name="Two Wheeler",
                    )
                ]
            )
        )
        self.assertEqual(_sub_classes(doc), [None])
