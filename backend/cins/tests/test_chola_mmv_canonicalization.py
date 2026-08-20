"""
Chola make/model canonicalization (``_canonicalize_make_model``).

A rule's free-text ``vehicle_make`` / ``vehicle_model`` — and their
``_exclude`` twins — are folded onto the spelling seeded in
``CarrierVehicleMMVMaster``, so one manufacturer stops exporting under several
names. Guards under test:

  - spelling only: list length, order and membership are never changed,
  - a value the master cannot match passes through VERBATIM (the matcher never
    guesses, so a sub-class word mis-extracted into vehicle_make stays visible
    to CholaMMVValidator instead of being rewritten into a real manufacturer),
  - the "All Other Make/Models" sentinel is preserved (``_clone_make_bucket``
    matches the literal phrase),
  - models are scoped to the rule's own make,
  - an empty index (non-Chola, unseeded master, or ``CINS_CHOLA_MMV=false``)
    is a bit-for-bit no-op.

Run from the backend container:
    python manage.py test cins.tests.test_chola_mmv_canonicalization -v 2
"""

import os
from unittest import TestCase, mock

from cins.processors.core.raw_to_cics_transformer import (
    RawToCICSTransformer,
    mmv_enabled,
    product_subproduct_bucket,
)
from cins.schema.raw_extraction import (
    RawCommissionEntry,
    RawExtraction,
    RawProviderInfo,
)


def _index(rows):
    """Build the MMV index through the REAL builder with the resolver stubbed.

    Never hand-assemble the index dict in a fixture — its internal shape is an
    implementation detail (it has already changed once, from a flat
    ``(make_slug, model_slug)`` map to a nested one plus a precomputed
    unscoped map), and a hand-built copy silently rots when it does.
    """
    from master_data.services.mmv import build_mmv_index

    with mock.patch(
        "master_data.services.resolver.carrier_vehicle_mmv", return_value=rows
    ):
        return build_mmv_index("Chola")


def _row(product, make, model):
    from master_data.services.mmv import mmv_slug

    return {
        "product": product,
        "make": make,
        "model": model,
        "make_slug": mmv_slug(make),
        "model_slug": mmv_slug(model),
    }


INDEX = _index(
    [
        _row("Car", "TATA", "NEXON"),
        _row("Car", "MARUTI", "SWIFT"),
        _row("Car", "MARUTI", "SWIFT DZIRE"),
        # Car spells it long, CV short: exercises the carrier-wide preferred
        # spelling (one brand, one exported name) rather than the per-bucket one.
        _row("Car", "MAHINDRA AND MAHINDRA", "XUV"),
        _row("CV", "MAHINDRA", "BOLERO"),
        _row("CV", "TATA MOTORS LTD", "407"),
    ]
)


def _transformer(product_subproduct="car", index=INDEX):
    t = RawToCICSTransformer()
    t._carrier_code = "Chola"
    t._product_subproduct = product_subproduct
    t._mmv_index = index
    return t


class CanonicalizeMakeModelTests(TestCase):
    def test_boilerplate_spellings_fold_onto_one_name(self):
        t = _transformer()
        makes, _ = t._canonicalize_make_model(["MAHINDRA AND MAHINDRA"], None)
        self.assertEqual(makes, ["MAHINDRA"])
        makes, _ = t._canonicalize_make_model(["mahindra"], None)
        self.assertEqual(makes, ["MAHINDRA"])

    def test_the_same_brand_spells_the_same_across_products(self):
        car = _transformer("car")._canonicalize_make_model(["TATA"], None)[0]
        cv = _transformer("commercial-gcv")._canonicalize_make_model(
            ["TATA MOTORS LTD"], None
        )[0]
        self.assertEqual(car, cv)

    def test_unmatched_values_pass_through_verbatim(self):
        """The extractor is known to mis-land sub-class words and CC bands in
        vehicle_make. Rewriting one into a real manufacturer would hide it
        from the validator that exists to catch it."""
        t = _transformer()
        for text in ("SCOOTER", "150_350cc", "Loader", "CCM"):
            makes, _ = t._canonicalize_make_model([text], None)
            self.assertEqual(makes, [text])

    def test_list_shape_is_never_changed(self):
        t = _transformer()
        makes, models = t._canonicalize_make_model(
            ["tata", "SCOOTER", "maruti"], ["nexon", "MYSTERY"]
        )
        self.assertEqual(makes, ["TATA", "SCOOTER", "MARUTI"])
        self.assertEqual(len(models), 2)

    def test_sentinels_are_preserved_verbatim(self):
        t = _transformer()
        makes, models = t._canonicalize_make_model(
            ["All Other Make/Models"], ["All Other Make/Models"]
        )
        self.assertEqual(makes, ["All Other Make/Models"])
        self.assertEqual(models, ["All Other Make/Models"])

    def test_models_are_scoped_to_the_rules_make(self):
        t = _transformer()
        _, models = t._canonicalize_make_model(["maruti"], ["swift"])
        self.assertEqual(models, ["SWIFT"])
        # NEXON is a TATA model; under MARUTI it must not be canonicalized.
        _, models = t._canonicalize_make_model(["maruti"], ["nexon"])
        self.assertEqual(models, ["nexon"])

    def test_model_scoping_is_dropped_when_the_rule_names_several_makes(self):
        t = _transformer()
        _, models = t._canonicalize_make_model(["tata", "maruti"], ["nexon"])
        self.assertEqual(models, ["NEXON"])  # unambiguous across the bucket

    def test_none_inputs_are_returned_unchanged(self):
        t = _transformer()
        self.assertEqual(t._canonicalize_make_model(None, None), (None, None))
        makes, models = t._canonicalize_make_model(None, ["nexon"])
        self.assertIsNone(makes)
        self.assertEqual(models, ["NEXON"])

    def test_empty_index_is_a_no_op(self):
        t = _transformer(index={})
        args = (["mahindra"], ["nexon"])
        self.assertEqual(t._canonicalize_make_model(*args), args)

    def test_unclassifiable_product_is_a_no_op(self):
        t = _transformer(product_subproduct="marine-cargo")
        args = (["mahindra"], ["nexon"])
        self.assertEqual(t._canonicalize_make_model(*args), args)

    def test_a_bucket_absent_from_the_index_is_a_no_op(self):
        t = _transformer(product_subproduct="two-wheeler")
        args = (["mahindra"], ["nexon"])
        self.assertEqual(t._canonicalize_make_model(*args), args)


class TransformWiringTests(TestCase):
    """The helper above is unit-tested in isolation; these drive a real
    document through ``transform()`` so the GATING and the CALL SITES are
    covered too. Without them, "the _exclude twins are canonicalized" — the
    reason CICS_PIPELINE_VERSION moved to 1.7.0 — is asserted only against a
    function nothing proves is wired up."""

    def _raw(self, conditions, provider="Chola"):
        return RawExtraction(
            source_file="test.xlsx",
            source_type="excel",
            provider=RawProviderInfo(name=provider),
            start_date="2026-04-01",
            end_date="2026-04-30",
            commission_entries=[
                RawCommissionEntry(
                    rule_text="Private Car",
                    commission_value="25%",
                    product_name="Private Car",
                    product_type="Car",
                    coverage_type_text="comprehensive",
                    conditions=conditions,
                )
            ],
        )

    def _transform(self, raw):
        with mock.patch("master_data.services.mmv.build_mmv_index", return_value=INDEX):
            doc, _, _ = RawToCICSTransformer().transform(raw)
        return doc

    @staticmethod
    def _conditions(doc):
        for product in doc.products:
            for rule in product.commission_rules:
                yield rule.conditions

    def test_a_chola_document_gets_its_makes_canonicalized(self):
        doc = self._transform(self._raw({"state": "GJ", "vehicle_make": "mahindra"}))
        makes = [c.vehicle_make for c in self._conditions(doc) if c.vehicle_make]
        self.assertTrue(makes, "no rule carried a make")
        for make in makes:
            # The carrier-wide preferred spelling, not the Car bucket's own
            # "MAHINDRA AND MAHINDRA" — one brand, one name across products.
            self.assertEqual(make, ["MAHINDRA"])

    def test_a_non_chola_document_is_left_alone(self):
        """The index is only built for Chola, so ICICI keeps its own
        spelling — this is what scopes the whole feature to one carrier."""
        doc = self._transform(
            self._raw({"state": "GJ", "vehicle_make": "mahindra"}, provider="ICICI")
        )
        makes = [c.vehicle_make for c in self._conditions(doc) if c.vehicle_make]
        self.assertTrue(makes)
        for make in makes:
            self.assertEqual(make, ["mahindra"])

    def test_the_env_flag_disables_it_end_to_end(self):
        with mock.patch.dict(os.environ, {"CINS_CHOLA_MMV": "false"}):
            doc = self._transform(
                self._raw({"state": "GJ", "vehicle_make": "mahindra"})
            )
        makes = [c.vehicle_make for c in self._conditions(doc) if c.vehicle_make]
        self.assertTrue(makes)
        for make in makes:
            self.assertEqual(make, ["mahindra"])

    def test_an_unavailable_master_degrades_instead_of_raising(self):
        raw = self._raw({"state": "GJ", "vehicle_make": "mahindra"})
        with mock.patch(
            "master_data.services.mmv.build_mmv_index",
            side_effect=RuntimeError("db down"),
        ):
            doc, _, _ = RawToCICSTransformer().transform(raw)
        makes = [c.vehicle_make for c in self._conditions(doc) if c.vehicle_make]
        self.assertTrue(makes)
        for make in makes:
            self.assertEqual(make, ["mahindra"])

    def test_exclusion_lists_are_canonicalized_through_the_pipeline(self):
        """``vehicle_make_exclude`` IS in _DEDUPE_SCALAR_FIELDS, so this is
        the branch that can merge two dedupe groups — the behaviour change
        the version bump exists for."""
        doc = self._transform(
            self._raw({"state": "GJ", "vehicle_make_exclude": "mahindra"})
        )
        excludes = [
            c.vehicle_make_exclude
            for c in self._conditions(doc)
            if c.vehicle_make_exclude
        ]
        self.assertTrue(excludes, "no rule carried a make exclusion")
        for exclude in excludes:
            self.assertEqual(exclude, ["MAHINDRA"])


class ScopeAgreementTests(TestCase):
    """Regression: the transformer and CholaMMVValidator MUST derive the
    model-scoping make identically.

    They once did not. The transformer scoped by ``match_make(...) or item``,
    so an UNMATCHED make became the scope (matching nothing, model left
    verbatim), while the validator counted only MATCHED makes and fell through
    to the unscoped lookup — where the model could resolve and so went
    unflagged. A model was then neither canonicalized nor reported. Both now
    call ``scope_make``.
    """

    def test_one_unmatched_make_scopes_to_nothing_on_both_sides(self):
        from master_data.services.mmv import scope_make

        self.assertIsNone(scope_make([None]))

    def test_one_matched_make_scopes_to_it(self):
        from master_data.services.mmv import scope_make

        self.assertEqual(scope_make(["MARUTI"]), "MARUTI")

    def test_several_matched_makes_decline_to_scope(self):
        from master_data.services.mmv import scope_make

        self.assertIsNone(scope_make(["MARUTI", "TATA"]))

    def test_a_rule_with_an_unmatched_make_leaves_its_model_verbatim(self):
        """The end-to-end shape of the bug: with the make unmatched there is
        no scope, and NEXON is unambiguous in the Car bucket, so the model
        does canonicalize — but the make stays verbatim and gets flagged."""
        t = _transformer()
        makes, models = t._canonicalize_make_model(["NOTAMAKE"], ["nexon"])
        self.assertEqual(makes, ["NOTAMAKE"])
        self.assertEqual(models, ["NEXON"])

    def test_validator_and_transformer_agree_on_the_same_rule(self):
        import json
        import tempfile

        from cins.validators.chola_mmv_validator import CholaMMVValidator

        t = _transformer()
        makes, models = t._canonicalize_make_model(["NOTAMAKE"], ["nexon"])

        doc = {
            "provider": {"provider_id": "Chola"},
            "products": [
                {
                    "product_subproduct": "car",
                    "sub_category": "Private Car",
                    "commission_rules": [
                        {
                            "rule_id": "R1",
                            "conditions": {
                                "vehicle_make": makes,
                                "vehicle_model": models,
                            },
                        }
                    ],
                }
            ],
        }
        fd = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(doc, fd)
        fd.close()

        validator = CholaMMVValidator.__new__(CholaMMVValidator)
        validator._index = INDEX
        result = validator.validate(fd.name)
        unknown = [i.message for i in result.warnings if i.category == "Chola MMV"]

        # The make the transformer could not fold is exactly the one the
        # validator reports — and the model it DID fold is not reported.
        self.assertEqual(len(unknown), 1)
        self.assertIn("NOTAMAKE", unknown[0])
        self.assertNotIn("NEXON", unknown[0])


class ProductBucketTests(TestCase):
    def test_buckets(self):
        self.assertEqual(product_subproduct_bucket("car"), "Car")
        self.assertEqual(product_subproduct_bucket("two-wheeler"), "TW")
        for commercial in (
            "commercial-gcv",
            "commercial-pcv",
            "commercial-buses",
            "commercial-miscd",
        ):
            self.assertEqual(product_subproduct_bucket(commercial), "CV")
        self.assertIsNone(product_subproduct_bucket(None))
        self.assertIsNone(product_subproduct_bucket(""))
        self.assertIsNone(product_subproduct_bucket("marine-cargo"))

    def test_the_hierarchy_validator_shares_this_one_definition(self):
        from cins.validators import chola_product_hierarchy_validator as mod

        self.assertIs(mod._bucket_for, product_subproduct_bucket)


class MMVFlagTests(TestCase):
    def test_default_is_on(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CINS_CHOLA_MMV", None)
            self.assertTrue(mmv_enabled())

    def test_falsy_spellings_disable(self):
        for value in ("0", "false", "FALSE", "no", "off", ""):
            with mock.patch.dict(os.environ, {"CINS_CHOLA_MMV": value}):
                self.assertFalse(mmv_enabled(), value)

    def test_truthy_spellings_enable(self):
        for value in ("1", "true", "yes", "on"):
            with mock.patch.dict(os.environ, {"CINS_CHOLA_MMV": value}):
                self.assertTrue(mmv_enabled(), value)
