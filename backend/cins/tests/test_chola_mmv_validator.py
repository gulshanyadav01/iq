"""
Tests for ``CholaMMVValidator``.

Operates on a CICS JSON file on disk (same contract as the other cins
validators), with the MMV index patched directly onto the instance so these
tests don't need the DB / seeded ``CarrierVehicleMMVMaster`` rows.

Run from the backend container:
    python manage.py test cins.tests.test_chola_mmv_validator -v 2
"""

import json
import os
import tempfile
from unittest import TestCase, mock

from cins.validators.chola_mmv_validator import CholaMMVValidator
from cins.validators.schema_validator import Severity, ValidationIssue


def _write_doc(doc) -> str:
    fd = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(doc, fd)
    fd.close()
    return fd.name


def _doc(provider_id, products):
    return {
        "provider": {"provider_id": provider_id, "provider_name": provider_id},
        "products": products,
    }


def _product(product_subproduct, rules):
    return {
        "product_subproduct": product_subproduct,
        "sub_category": None,
        "commission_rules": rules,
    }


def _rule(**conditions):
    return {"rule_id": "R1", "conditions": conditions}


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


def _validator(index=INDEX):
    validator = CholaMMVValidator.__new__(CholaMMVValidator)
    validator._index = index
    return validator


def _messages(result):
    return [i.message for i in result.warnings + result.errors + result.info]


def _issues(result, category):
    """Issues from ONE half of the validator.

    The validator runs two independent checks — "this value matches no
    vehicle in the master" (category ``Chola MMV``) and "this column is
    mandatory and blank" (``Chola MMV Presence``). Most fixtures trip both,
    so a test about one has to select it rather than count everything.
    """
    every = result.warnings + result.errors + result.info
    return [i for i in every if i.category == category]


def _unknown(result):
    return [i.message for i in _issues(result, "Chola MMV")]


class CholaMMVValidatorTests(TestCase):
    def test_known_make_and_model_produce_no_findings(self):
        path = _write_doc(
            _doc(
                "Chola",
                [
                    _product(
                        "car", [_rule(vehicle_make=["TATA"], vehicle_model=["NEXON"])]
                    )
                ],
            )
        )
        result = _validator().validate(path)
        self.assertEqual(_unknown(result), [])
        self.assertTrue(result.is_valid)

    def test_unknown_make_is_flagged(self):
        path = _write_doc(
            _doc("Chola", [_product("car", [_rule(vehicle_make=["SCOOTER"])])])
        )
        result = _validator().validate(path)
        self.assertEqual(len(_unknown(result)), 1)
        self.assertIn("SCOOTER", _unknown(result)[0])

    def test_findings_are_medium_only_and_never_block_the_cache_pin(self):
        """A HIGH here would make cics_cache.should_cache refuse to pin the
        document, forcing an unbounded re-extraction loop over a spelling the
        master merely has not got."""
        path = _write_doc(
            _doc(
                "Chola",
                [
                    _product(
                        "car",
                        [_rule(vehicle_make=["NOPE"], vehicle_model=["ALSO NOPE"])],
                    ),
                    _product("commercial-gcv", [_rule(vehicle_make=["MYSTERY"])]),
                ],
            )
        )
        result = _validator().validate(path)
        self.assertTrue(result.warnings)
        self.assertEqual(result.errors, [])
        self.assertTrue(result.is_valid)
        for issue in result.warnings:
            self.assertEqual(issue.severity, Severity.MEDIUM)

    def test_non_chola_documents_are_skipped(self):
        path = _write_doc(
            _doc("ICICI", [_product("car", [_rule(vehicle_make=["SCOOTER"])])])
        )
        self.assertEqual(_messages(_validator().validate(path)), [])

    def test_unseeded_master_fails_open(self):
        path = _write_doc(
            _doc("Chola", [_product("car", [_rule(vehicle_make=["SCOOTER"])])])
        )
        self.assertEqual(_unknown(_validator({}).validate(path)), [])
        # Only the carrier-wide make map present = no product rows seeded.
        self.assertEqual(_unknown(_validator({"_makes": {}}).validate(path)), [])

    def test_unreadable_cics_is_reported_but_not_fatal(self):
        result = _validator().validate("/nonexistent/path.json")
        self.assertEqual(result.errors, [])
        self.assertTrue(result.is_valid)
        self.assertIn("cannot read CICS", result.warnings[0].message)

    def test_sentinel_make_is_not_treated_as_a_vehicle(self):
        path = _write_doc(
            _doc(
                "Chola",
                [
                    _product(
                        "car",
                        [
                            _rule(
                                vehicle_make=["All Other Make/Models"],
                                vehicle_model=["All Other Make/Models"],
                            )
                        ],
                    )
                ],
            )
        )
        self.assertEqual(_messages(_validator().validate(path)), [])

    def test_exclusion_lists_are_validated_too(self):
        """An exclusion names real vehicles; a bogus value there is as wrong
        as one in the include list."""
        path = _write_doc(
            _doc("Chola", [_product("car", [_rule(vehicle_make_exclude=["NOTACAR"])])])
        )
        result = _validator().validate(path)
        self.assertEqual(len(_unknown(result)), 1)
        self.assertIn("NOTACAR", _unknown(result)[0])

    def test_a_product_bucket_the_master_does_not_cover_is_skipped(self):
        path = _write_doc(
            _doc("Chola", [_product("two-wheeler", [_rule(vehicle_make=["NOPE"])])])
        )
        self.assertEqual(_unknown(_validator().validate(path)), [])

    def test_unclassifiable_subproduct_is_skipped_by_BOTH_halves(self):
        """A non-motor line has no make or model by definition, so the
        presence half must skip it too — otherwise every such product yields
        a guaranteed "100% missing" false positive."""
        path = _write_doc(
            _doc("Chola", [_product("marine-cargo", [_rule(vehicle_make=["NOPE"])])])
        )
        result = _validator().validate(path)
        self.assertEqual(_unknown(result), [])
        self.assertEqual(_issues(result, "Chola MMV Presence"), [])

    def test_model_is_validated_against_the_rules_own_make(self):
        """NEXON is a TATA model — under MARUTI it is unknown."""
        path = _write_doc(
            _doc(
                "Chola",
                [
                    _product(
                        "car",
                        [_rule(vehicle_make=["MARUTI"], vehicle_model=["NEXON"])],
                    )
                ],
            )
        )
        result = _validator().validate(path)
        self.assertEqual(len(_unknown(result)), 1)
        self.assertIn("NEXON", _unknown(result)[0])

    def test_findings_are_deduped_across_rules(self):
        rules = [_rule(vehicle_make=["SCOOTER"]) for _ in range(20)]
        path = _write_doc(_doc("Chola", [_product("car", rules)]))
        self.assertEqual(len(_unknown(_validator().validate(path))), 1)

    def test_long_value_lists_are_capped_with_a_count(self):
        rules = [_rule(vehicle_make=[f"NOPE{i}"]) for i in range(9)]
        path = _write_doc(_doc("Chola", [_product("car", rules)]))
        message = _unknown(_validator().validate(path))[0]
        self.assertIn("+4 more", message)

    def test_commercial_subproducts_all_bucket_to_cv(self):
        for subproduct in ("commercial-gcv", "commercial-pcv", "commercial-buses"):
            path = _write_doc(
                _doc("Chola", [_product(subproduct, [_rule(vehicle_make=["TATA"])])])
            )
            self.assertEqual(_unknown(_validator().validate(path)), [], subproduct)


class CholaMMVPresenceTests(TestCase):
    """VehicleMake / VehicleModel are mandatory on the client's deal load, so
    a rule carrying neither an include nor an exclude list exports a blank
    cell."""

    def test_missing_make_and_model_are_both_flagged_with_counts(self):
        path = _write_doc(
            _doc(
                "Chola",
                [
                    {
                        "product_subproduct": "car",
                        "sub_category": "Private Car",
                        "commission_rules": [
                            _rule(vehicle_make=["TATA"], vehicle_model=["NEXON"]),
                            _rule(state=["goa"]),
                            _rule(state=["kerala"]),
                        ],
                    }
                ],
            )
        )
        messages = _messages(_validator().validate(path))
        presence = [m for m in messages if "mandatory but missing" in m]
        self.assertEqual(len(presence), 2)
        self.assertTrue(any("vehicle_make is mandatory" in m for m in presence))
        self.assertTrue(any("vehicle_model is mandatory" in m for m in presence))
        for m in presence:
            self.assertIn("2 of 3 rule(s) (66.7%)", m)
            self.assertIn("Private Car", m)

    def test_an_exclusion_list_counts_as_present(self):
        """The export renders it as ``exclude(A, B)``, so the cell is filled."""
        path = _write_doc(
            _doc(
                "Chola",
                [
                    _product(
                        "car",
                        [
                            _rule(
                                vehicle_make_exclude=["TATA"],
                                vehicle_model_exclude=["NEXON"],
                            )
                        ],
                    )
                ],
            )
        )
        self.assertEqual(
            [m for m in _messages(_validator().validate(path)) if "mandatory" in m], []
        )

    def test_presence_is_reported_per_product_not_per_rule(self):
        rules = [_rule(state=["goa"]) for _ in range(500)]
        path = _write_doc(_doc("Chola", [_product("car", rules)]))
        presence = [
            m for m in _messages(_validator().validate(path)) if "mandatory" in m
        ]
        self.assertEqual(len(presence), 2)  # one make + one model, not 1000

    def test_presence_never_raises_high_or_critical(self):
        """A HIGH would make cics_cache.should_cache refuse to pin the
        document — on a real Chola grid 97.5% of rules carry no model, so
        every document would be walled out of the cache permanently."""
        rules = [_rule(state=["goa"]) for _ in range(10)]
        path = _write_doc(_doc("Chola", [_product("car", rules)]))
        result = _validator().validate(path)
        self.assertEqual(result.errors, [])
        self.assertTrue(result.is_valid)

    def test_presence_runs_without_a_seeded_master(self):
        """Whether a rule names a make at all needs no seeded data, so an
        unseeded master must not disable this half."""
        path = _write_doc(_doc("Chola", [_product("car", [_rule(state=["goa"])])]))
        presence = [
            m for m in _messages(_validator({}).validate(path)) if "mandatory" in m
        ]
        self.assertEqual(len(presence), 2)

    def test_presence_is_scoped_to_chola(self):
        path = _write_doc(_doc("ICICI", [_product("car", [_rule(state=["goa"])])]))
        self.assertEqual(_messages(_validator().validate(path)), [])

    def test_presence_covers_products_the_mmv_master_does_not(self):
        """TW is absent from this fixture's index, so the unknown-value half
        skips it — the presence half must not."""
        path = _write_doc(
            _doc("Chola", [_product("two-wheeler", [_rule(state=["goa"])])])
        )
        presence = [
            m for m in _messages(_validator().validate(path)) if "mandatory" in m
        ]
        self.assertEqual(len(presence), 2)

    def test_mandatory_flag_spelling_matrix(self):
        from cins.validators.chola_mmv_validator import make_model_mandatory

        for value in ("0", "false", "FALSE", "no", "off", ""):
            with mock.patch.dict(os.environ, {"CINS_MAKE_MODEL_MANDATORY": value}):
                self.assertFalse(make_model_mandatory(), value)
        for value in ("1", "true", "yes", "on"):
            with mock.patch.dict(os.environ, {"CINS_MAKE_MODEL_MANDATORY": value}):
                self.assertTrue(make_model_mandatory(), value)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CINS_MAKE_MODEL_MANDATORY", None)
            self.assertTrue(make_model_mandatory())

    def test_a_scalar_condition_value_is_accepted_like_a_list(self):
        """RuleConditions coerces strings to lists, but a hand-built or
        legacy CICS on disk can still carry a bare string."""
        path = _write_doc(
            _doc("Chola", [_product("car", [_rule(vehicle_make="TATA")])])
        )
        result = _validator().validate(path)
        self.assertEqual(_unknown(result), [])
        presence = [i for i in _issues(result, "Chola MMV Presence")]
        self.assertEqual(len(presence), 1)  # model missing, make satisfied

    def test_env_flag_off_disables_presence_but_not_the_master_check(self):
        path = _write_doc(
            _doc(
                "Chola",
                [_product("car", [_rule(vehicle_make=["SCOOTER"])])],
            )
        )
        with mock.patch.dict(os.environ, {"CINS_MAKE_MODEL_MANDATORY": "false"}):
            messages = _messages(_validator().validate(path))
        self.assertEqual([m for m in messages if "mandatory" in m], [])
        # The unknown-value half is governed by CINS_CHOLA_MMV and still fires.
        self.assertTrue(any("match no vehicle" in m for m in messages))


class PipelinePlumbingTests(TestCase):
    """pipeline._run_validators merges this validator's findings into
    ``schema_result`` — and a failure inside it never breaks the pipeline.
    Same contract the sibling Chola validators pin."""

    def _path(self):
        return _write_doc(
            _doc("Chola", [_product("car", [_rule(vehicle_make=["SCOOTER"])])])
        )

    def test_findings_merged_into_schema_result(self):
        from pathlib import Path

        from cins.processors.core.pipeline import _run_validators
        from cins.validators.schema_validator import ValidationResult

        stub = ValidationResult(
            is_valid=True,
            errors=[],
            warnings=[
                ValidationIssue(
                    severity=Severity.MEDIUM,
                    category="Chola MMV",
                    message="chola-mmv-finding-marker",
                    field_path="products[0]",
                )
            ],
            info=[],
        )
        with mock.patch.object(CholaMMVValidator, "validate", return_value=stub):
            schema_result, _ = _run_validators(Path(self._path()))

        self.assertIsNotNone(schema_result)
        msgs = " ".join(
            i.message for i in (schema_result.errors + schema_result.warnings)
        )
        self.assertIn("chola-mmv-finding-marker", msgs)

    def test_validator_failure_never_breaks_the_pipeline(self):
        from pathlib import Path

        from cins.processors.core.pipeline import _run_validators

        with mock.patch.object(
            CholaMMVValidator, "validate", side_effect=RuntimeError("boom")
        ):
            schema_result, _ = _run_validators(Path(self._path()))

        self.assertIsNotNone(schema_result)


class CholaMMVValidatorFlagTests(TestCase):
    def test_chola_mmv_flag_off_disables_the_master_half_only(self):
        """The two halves have separate flags on purpose: CINS_CHOLA_MMV
        governs the MMV master, CINS_MAKE_MODEL_MANDATORY governs the export
        rule. Rolling the master back must not silently take the mandatory
        check with it."""
        with mock.patch.dict(os.environ, {"CINS_CHOLA_MMV": "false"}):
            validator = CholaMMVValidator()
        self.assertEqual(validator._index, {})
        path = _write_doc(
            _doc("Chola", [_product("car", [_rule(vehicle_make=["SCOOTER"])])])
        )
        result = validator.validate(path)
        self.assertEqual(_unknown(result), [])
        self.assertEqual(len(_issues(result, "Chola MMV Presence")), 1)

    def test_both_flags_off_leaves_the_validator_fully_inert(self):
        env = {"CINS_CHOLA_MMV": "false", "CINS_MAKE_MODEL_MANDATORY": "false"}
        with mock.patch.dict(os.environ, env):
            validator = CholaMMVValidator()
            path = _write_doc(
                _doc("Chola", [_product("car", [_rule(vehicle_make=["SCOOTER"])])])
            )
            self.assertEqual(_messages(validator.validate(path)), [])
