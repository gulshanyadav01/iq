"""
Export rendering of make/model exclusion buckets.

An exclusion is EXPANDED against the carrier's MMV master into the positive
list it implies — the form the carrier's own system carries, and the only form
an index-based importer can act on. When the expansion cannot be trusted the
cell falls back to the literal ``exclude(A, B, C)`` inside the existing column
(no new columns, so index-based importers are unaffected). Include lists render
unchanged; blank when absent.

"Cannot be trusted" is deliberately broad, because a positive list that
silently still contains an excluded make would pay commission on a vehicle the
carrier carved out — strictly worse than a visibly-unexpanded cell. It covers:
no seeded master, no resolvable product bucket, and any exclusion term the
master does not know.

Run from the backend container:
    python manage.py test commission_intake.tests.test_export_make_model_exclusion -v 2
"""

import io

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase
from django.urls import reverse
from openpyxl import load_workbook
from rest_framework.test import APIClient

from commission_intake.models import CommissionDocument, CICSResult


def _rule(conditions):
    return {
        "rule_id": "r",
        "conditions": {"deal_parameter": "net", **conditions},
        "outcome": {
            "commission_type": "percentage",
            "percentage_value": 10,
            "applied_on": "Net Premium",
        },
    }


def _doc(rules):
    return {
        "provider": {"provider_name": "Chola"},
        "products": [
            {
                "product_name": "Commercial Vehicle",
                "sub_category": "GCV",
                "commission_rules": rules,
            }
        ],
    }


class ExportExclusionFallbackTests(DjangoTestCase):
    """No MMV master seeded -> every exclusion renders verbatim."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="t@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _export(self, cics_doc):
        doc = CommissionDocument.objects.create(original_filename="grid.xlsx")
        CICSResult.objects.create(document=doc, cics_doc=cics_doc)
        url = reverse("commission-documents-export-excel", args=[doc.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))
        return load_workbook(io.BytesIO(b"".join(resp.streaming_content)))

    @staticmethod
    def _cols(ws):
        return {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}

    def _rows(self, ws):
        return list(ws.iter_rows(min_row=2, values_only=True))

    def test_three_bucket_rendering(self):
        wb = self._export(
            _doc(
                [
                    # (a) TMM + model list
                    _rule(
                        {
                            "vehicle_make": ["TATA", "Maruti", "Mahindra"],
                            "vehicle_model": ["Ace", "Super Ace"],
                        }
                    ),
                    # (b) TMM + model exclusion
                    _rule(
                        {
                            "vehicle_make": ["TATA", "Maruti", "Mahindra"],
                            "vehicle_model_exclude": ["Ace", "Super Ace"],
                        }
                    ),
                    # (c) make exclusion
                    _rule({"vehicle_make_exclude": ["TATA", "Maruti", "Mahindra"]}),
                ]
            )
        )
        ws = wb["Commission Rules"]
        cols = self._cols(ws)
        mk = cols["VehicleMake"] - 1
        md = cols["VehicleModel"] - 1
        rows = self._rows(ws)

        self.assertEqual(rows[0][mk], "TATA,Maruti,Mahindra")
        self.assertEqual(rows[0][md], "Ace,Super Ace")

        self.assertEqual(rows[1][mk], "TATA,Maruti,Mahindra")
        self.assertEqual(rows[1][md], "exclude(Ace, Super Ace)")

        self.assertEqual(rows[2][mk], "exclude(TATA, Maruti, Mahindra)")
        self.assertIn(rows[2][md], (None, ""))

    def test_columns_unchanged_count(self):
        wb = self._export(_doc([_rule({"vehicle_make_exclude": ["TATA"]})]))
        ws = wb["Commission Rules"]
        cols = self._cols(ws)
        # VehicleMake / VehicleModel are adjacent single columns (no new cols).
        self.assertEqual(cols["VehicleModel"] - cols["VehicleMake"], 1)

    def test_both_set_prefers_include_and_blank_when_absent(self):
        wb = self._export(
            _doc(
                [
                    # both make and make_exclude set -> prefer include (defensive)
                    _rule(
                        {"vehicle_make": ["TATA"], "vehicle_make_exclude": ["Maruti"]}
                    ),
                    # neither make nor make_exclude -> blank cell
                    _rule({"vehicle_model": ["Ace"]}),
                ]
            )
        )
        ws = wb["Commission Rules"]
        cols = self._cols(ws)
        mk = cols["VehicleMake"] - 1
        rows = self._rows(ws)
        self.assertEqual(rows[0][mk], "TATA")  # include wins
        self.assertIn(rows[1][mk], (None, ""))  # blank when absent


class ExportExclusionExpansionTests(DjangoTestCase):
    """With the master seeded, an exclusion becomes the positive list it means.

    Reconciled against a dump of live Chola deals: the carrier's own system
    carries "every make in the book except these" as an enumerated list, never
    as a carve-out expression.
    """

    CAR_MAKES = ["MARUTI", "HYUNDAI", "TATA", "HONDA"]

    @classmethod
    def setUpTestData(cls):
        from master_data.models import CarrierVehicleMMVMaster
        from master_data.services.mmv import mmv_slug

        rows = [
            # Car book: four makes, one of them with two models.
            ("Car", "MARUTI", "Swift"),
            ("Car", "HYUNDAI", "i20"),
            ("Car", "TATA", "Nexon"),
            ("Car", "HONDA", "City"),
            ("Car", "HONDA", "Amaze"),
            # A CV-only make, to prove the Car expansion is product-scoped
            # even though MATCHING is carrier-wide.
            ("CV", "ASHOK LEYLAND", "Dost"),
        ]
        for product, make, model in rows:
            CarrierVehicleMMVMaster.objects.create(
                carrier="Chola",
                product=product,
                make=make,
                model=model,
                make_slug=mmv_slug(make),
                model_slug=mmv_slug(model),
            )

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="mmv@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _export(self, cics_doc):
        doc = CommissionDocument.objects.create(original_filename="grid.xlsx")
        CICSResult.objects.create(document=doc, cics_doc=cics_doc)
        url = reverse("commission-documents-export-excel", args=[doc.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))
        return load_workbook(io.BytesIO(b"".join(resp.streaming_content)))

    def _cells(self, cics_doc):
        ws = self._export(cics_doc)["Commission Rules"]
        cols = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        return [(r[cols["VehicleMake"] - 1], r[cols["VehicleModel"] - 1]) for r in rows]

    @staticmethod
    def _car_doc(rules):
        return {
            "provider": {"provider_name": "Cholamandalam MS"},
            "products": [
                {
                    "product_name": "Private Car",
                    "sub_category": "Private Car",
                    "commission_rules": rules,
                }
            ],
        }

    def test_make_exclusion_expands_to_the_rest_of_the_book(self):
        ((make, _model),) = self._cells(
            self._car_doc([_rule({"vehicle_make_exclude": ["TATA"]})])
        )
        self.assertEqual(make, "HONDA,HYUNDAI,MARUTI")

    def test_expansion_is_product_scoped(self):
        # ASHOK LEYLAND is in the carrier's book but not the Car book, so it
        # must not appear in a Private Car cell.
        ((make, _model),) = self._cells(
            self._car_doc([_rule({"vehicle_make_exclude": ["HONDA"]})])
        )
        self.assertNotIn("ASHOK LEYLAND", make)
        self.assertEqual(make, "HYUNDAI,MARUTI,TATA")

    def test_several_excluded_makes_all_come_out(self):
        ((make, _model),) = self._cells(
            self._car_doc([_rule({"vehicle_make_exclude": ["TATA", "MARUTI"]})])
        )
        self.assertEqual(make, "HONDA,HYUNDAI")

    def test_unmatched_term_falls_back_verbatim(self):
        # "NOTACAR" is in no book. Expanding would drop nothing and silently
        # hand back a list that still contains everything the grid excluded.
        ((make, _model),) = self._cells(
            self._car_doc([_rule({"vehicle_make_exclude": ["TATA", "NOTACAR"]})])
        )
        self.assertEqual(make, "exclude(TATA, NOTACAR)")

    def test_excluding_the_whole_book_falls_back(self):
        # An empty remainder is not a usable cell — say so rather than blank it.
        ((make, _model),) = self._cells(
            self._car_doc([_rule({"vehicle_make_exclude": self.CAR_MAKES})])
        )
        self.assertTrue(make.startswith("exclude("))

    def test_model_exclusion_expands_scoped_by_the_rules_make(self):
        ((_make, model),) = self._cells(
            self._car_doc(
                [
                    _rule(
                        {
                            "vehicle_make": ["HONDA"],
                            "vehicle_model_exclude": ["City"],
                        }
                    )
                ]
            )
        )
        self.assertEqual(model, "Amaze")

    def test_include_list_still_wins(self):
        ((make, model),) = self._cells(
            self._car_doc(
                [
                    _rule(
                        {
                            "vehicle_make": ["TATA"],
                            "vehicle_make_exclude": ["MARUTI"],
                        }
                    )
                ]
            )
        )
        self.assertEqual(make, "TATA")
        self.assertIn(model, (None, ""))

    def test_unbucketable_product_falls_back(self):
        # product_bucket returns None for a non-motor line, so there is no book
        # to expand against.
        doc = {
            "provider": {"provider_name": "Cholamandalam MS"},
            "products": [
                {
                    "product_name": "Marine Cargo",
                    "sub_category": "",
                    "commission_rules": [_rule({"vehicle_make_exclude": ["TATA"]})],
                }
            ],
        }
        ((make, _model),) = self._cells(doc)
        self.assertEqual(make, "exclude(TATA)")


class ExportMakeFamilyExpansionTests(DjangoTestCase):
    """A grid writes one manufacturer word where the master lists a family.

    Chola's CV grids exclude "Mahindra, Maruti, TATA"; the master has no bare
    "Mahindra" or "TATA" row, so an exact match resolves nothing and the cell
    used to fall back. The family groupings in
    ``mmv.CARRIER_MAKE_FAMILIES`` are read off the client's own deals — see
    that table's comment for the 1,713-vs-1,700 arithmetic.
    """

    @classmethod
    def setUpTestData(cls):
        from master_data.models import CarrierVehicleMMVMaster
        from master_data.services.mmv import mmv_slug

        makes = [
            "MAHINDRA AND MAHINDRA",
            "MAHINDRA NAVISTAR",
            "MARUTI",
            "MARUTI SUZUKI",
            "TATA MOTORS LTD",
            # The carrier files the construction JV as a SEPARATE brand and
            # keeps it in the complement — the reason the family table is an
            # explicit list rather than a prefix rule.
            "TATA HITACHI",
            "ASHOK LEYLAND",
            "EICHER MOTORS",
        ]
        for make in makes:
            CarrierVehicleMMVMaster.objects.create(
                carrier="Chola",
                product="CV",
                make=make,
                model=f"{make} model",
                make_slug=mmv_slug(make),
                model_slug=mmv_slug(f"{make} model"),
            )

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="fam@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _make_cell(self, exclusion):
        cics_doc = {
            "provider": {"provider_name": "Cholamandalam MS"},
            "products": [
                {
                    "product_name": "Commercial Vehicle",
                    "sub_category": "GCV",
                    "commission_rules": [_rule({"vehicle_make_exclude": exclusion})],
                }
            ],
        }
        doc = CommissionDocument.objects.create(original_filename="grid.xlsx")
        CICSResult.objects.create(document=doc, cics_doc=cics_doc)
        resp = self.client.get(
            reverse("commission-documents-export-excel", args=[doc.id])
        )
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))
        ws = load_workbook(io.BytesIO(b"".join(resp.streaming_content)))[
            "Commission Rules"
        ]
        cols = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
        return next(ws.iter_rows(min_row=2, values_only=True))[cols["VehicleMake"] - 1]

    def test_tmm_bucket_expands_to_the_rest_of_the_book(self):
        cell = self._make_cell(["Mahindra", "Maruti", "TATA"])
        self.assertEqual(cell, "ASHOK LEYLAND,EICHER MOTORS,TATA HITACHI")

    def test_tata_family_does_not_sweep_in_tata_hitachi(self):
        cell = self._make_cell(["TATA"])
        self.assertIn("TATA HITACHI", cell.split(","))
        self.assertNotIn("TATA MOTORS LTD", cell.split(","))

    def test_mahindra_family_takes_every_member(self):
        cell = self._make_cell(["Mahindra"])
        kept = cell.split(",")
        self.assertNotIn("MAHINDRA AND MAHINDRA", kept)
        self.assertNotIn("MAHINDRA NAVISTAR", kept)
        self.assertIn("MARUTI", kept)

    def test_an_unknown_word_still_falls_back(self):
        # Only the curated families resolve; nothing else is guessed at.
        self.assertEqual(self._make_cell(["Leyland"]), "exclude(Leyland)")

    def test_names_are_not_repeated(self):
        cell = self._make_cell(["Mahindra"])
        parts = cell.split(",")
        self.assertEqual(len(parts), len(set(parts)), cell)


class ExportModelScopeTests(DjangoTestCase):
    """Model expansion is scoped by ALL of a rule's makes, not a lone one.

    Chola's TMM rules carry three makes and a model list drawn from across
    them ("Ace, Yodha, Xenon, Mahindra Jeeto"), so scoping by a single make —
    which is right for ``mmv.scope_make``, whose question is "what does this
    model name mean" — would resolve almost nothing here.
    """

    @classmethod
    def setUpTestData(cls):
        from master_data.models import CarrierVehicleMMVMaster
        from master_data.services.mmv import mmv_slug

        rows = [
            ("TATA MOTORS LTD", "ACE"),
            ("TATA MOTORS LTD", "YODHA"),
            ("TATA MOTORS LTD", "XENON"),
            # The master lists the bare model; the grid writes "Mahindra Jeeto".
            ("MAHINDRA AND MAHINDRA", "JEETO"),
            ("MAHINDRA AND MAHINDRA", "SUPRO"),
            ("MARUTI", "EECO"),
        ]
        for make, model in rows:
            CarrierVehicleMMVMaster.objects.create(
                carrier="Chola",
                product="CV",
                make=make,
                model=model,
                make_slug=mmv_slug(make),
                model_slug=mmv_slug(model),
            )

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="scope@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _model_cell(self, makes, model_exclusion):
        cics_doc = {
            "provider": {"provider_name": "Cholamandalam MS"},
            "products": [
                {
                    "product_name": "Commercial Vehicle",
                    "sub_category": "GCV",
                    "commission_rules": [
                        _rule(
                            {
                                "vehicle_make": makes,
                                "vehicle_model_exclude": model_exclusion,
                            }
                        )
                    ],
                }
            ],
        }
        doc = CommissionDocument.objects.create(original_filename="grid.xlsx")
        CICSResult.objects.create(document=doc, cics_doc=cics_doc)
        resp = self.client.get(
            reverse("commission-documents-export-excel", args=[doc.id])
        )
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))
        ws = load_workbook(io.BytesIO(b"".join(resp.streaming_content)))[
            "Commission Rules"
        ]
        cols = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
        return next(ws.iter_rows(min_row=2, values_only=True))[cols["VehicleModel"] - 1]

    def test_universe_spans_every_make_the_rule_names(self):
        cell = self._model_cell(["TATA", "Maruti", "Mahindra"], ["ACE"])
        kept = cell.split(",")
        self.assertNotIn("ACE", kept)
        # Models from all three makes' books are in scope.
        for model in ("YODHA", "XENON", "JEETO", "SUPRO", "EECO"):
            self.assertIn(model, kept)

    def test_a_repeated_make_word_in_the_model_cell_is_stripped(self):
        # The grid writes "Mahindra Jeeto"; the master lists "JEETO".
        cell = self._model_cell(["TATA", "Maruti", "Mahindra"], ["Mahindra Jeeto"])
        self.assertNotIn("JEETO", cell.split(","))
        self.assertIn("SUPRO", cell.split(","))

    def test_the_strip_only_uses_makes_this_rule_carries(self):
        # No Mahindra on the rule -> "Mahindra Jeeto" must not resolve, so the
        # cell falls back rather than reaching another make's model.
        cell = self._model_cell(["TATA"], ["Mahindra Jeeto"])
        self.assertEqual(cell, "exclude(Mahindra Jeeto)")
