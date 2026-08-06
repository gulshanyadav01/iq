"""
Tests for ``CholaProductHierarchyValidator``.

Operates on a CICS JSON file on disk (same contract as the other cins
validators — see ``state_column_alignment_validator.py``), with the
hierarchy master patched directly onto the instance so these tests don't
need the DB / seeded ``CarrierProductHierarchyMaster`` rows.

Run from the backend container:
    python manage.py test cins.tests.test_chola_product_validator -v 2
"""

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from cins.validators.chola_product_hierarchy_validator import (
    CholaProductHierarchyValidator,
)


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


def _product(product_subproduct, sub_category, rules):
    return {
        "product_subproduct": product_subproduct,
        "sub_category": sub_category,
        "commission_rules": rules,
    }


def _rule(vehicle_sub_class):
    return {"conditions": {"vehicle_sub_class": vehicle_sub_class}}


class _ValidatorWithSeededHierarchy(CholaProductHierarchyValidator):
    """Bypasses the DB lookup in __init__ — injects a fixed hierarchy."""

    def __init__(self, hierarchy_rows):
        self._valid_sub_products = {}
        self._valid_subclasses = {}
        self._covered_products = set()
        from cins.processors.core.raw_to_cics_transformer import (
            VEHICLE_SUB_CLASS_MAPPINGS,
            RawToCICSTransformer,
        )
        import re

        normalizer = RawToCICSTransformer()
        camel = re.compile(r"(?<=[a-z])(?=[A-Z])")
        for row in hierarchy_rows:
            product = row["product"]
            self._covered_products.add(product)
            sub_product = row.get("sub_product") or ""
            if sub_product:
                self._valid_sub_products.setdefault(product, set()).add(sub_product)
            spaced = camel.sub(" ", row["vehicle_subclass"])
            normalizer.warnings = []
            slugs = normalizer._normalize_list_field(
                spaced, VEHICLE_SUB_CLASS_MAPPINGS, "x"
            )
            for slug in slugs or ():
                self._valid_subclasses.setdefault((product, sub_product), set()).add(
                    slug
                )


_HIERARCHY = [
    {"product": "CV", "sub_product": "GCV", "vehicle_subclass": "Truck"},
    {"product": "CV", "sub_product": "GCV", "vehicle_subclass": "Tipper"},
    {"product": "CV", "sub_product": "PCV", "vehicle_subclass": "Taxi"},
    {"product": "Car", "sub_product": "", "vehicle_subclass": "PrivateCar"},
    {"product": "TW", "sub_product": "", "vehicle_subclass": "Bike"},
]


class ValidCombosPassTests(TestCase):
    def test_valid_gcv_subclass_passes(self):
        v = _ValidatorWithSeededHierarchy(_HIERARCHY)
        path = _write_doc(
            _doc(
                "Chola",
                [_product("commercial-gcv", "GCV", [_rule(["truck"])])],
            )
        )
        result = v.validate(path)
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.errors, [])

    def test_camel_case_source_spelling_matches_normalized_condition_slug(self):
        # Master row is "PrivateCar" (no space); the transformer condition
        # carries the normalized slug "private-car" — must still match.
        v = _ValidatorWithSeededHierarchy(_HIERARCHY)
        path = _write_doc(
            _doc("Chola", [_product("car", "Private Car", [_rule(["private-car"])])])
        )
        result = v.validate(path)
        self.assertEqual(result.warnings, [])


class InvalidComboFindingTests(TestCase):
    def test_invalid_subclass_for_subproduct_flagged_once_deduped(self):
        v = _ValidatorWithSeededHierarchy(_HIERARCHY)
        path = _write_doc(
            _doc(
                "Chola",
                [
                    _product(
                        "commercial-gcv",
                        "GCV",
                        [_rule(["bike"]), _rule(["bike"])],  # same invalid combo twice
                    )
                ],
            )
        )
        result = v.validate(path)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("bike", result.warnings[0].message)

    def test_unknown_subproduct_flagged(self):
        v = _ValidatorWithSeededHierarchy(_HIERARCHY)
        path = _write_doc(
            _doc(
                "Chola",
                [_product("commercial-gcv", "NOT-A-SEGMENT", [_rule(["truck"])])],
            )
        )
        result = v.validate(path)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("NOT-A-SEGMENT", result.warnings[0].message)

    def test_uncovered_product_bucket_not_flagged(self):
        # Master has no rows for TW's second segment scenario etc. — a
        # product bucket absent from the master entirely is skipped, not
        # flagged, since that's missing coverage rather than a violation.
        v = _ValidatorWithSeededHierarchy(
            [{"product": "CV", "sub_product": "GCV", "vehicle_subclass": "Truck"}]
        )
        path = _write_doc(
            _doc("Chola", [_product("two-wheeler", "", [_rule(["bike"])])])
        )
        result = v.validate(path)
        self.assertEqual(result.warnings, [])


class NonCholaUntouchedTests(TestCase):
    def test_non_chola_provider_produces_no_findings(self):
        v = _ValidatorWithSeededHierarchy(_HIERARCHY)
        path = _write_doc(
            _doc(
                "Bajaj",
                [_product("commercial-gcv", "GCV", [_rule(["bike"])])],
            )
        )
        result = v.validate(path)
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.errors, [])


class MasterUnavailableFailsOpenTests(TestCase):
    def test_no_hierarchy_rows_fails_open(self):
        v = _ValidatorWithSeededHierarchy([])
        path = _write_doc(
            _doc("Chola", [_product("commercial-gcv", "GCV", [_rule(["bike"])])])
        )
        result = v.validate(path)
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.errors, [])


class PipelinePlumbingTests(TestCase):
    """pipeline._run_validators merges this validator's findings into
    ``schema_result`` — and a failure inside it never breaks the pipeline."""

    def _path(self):
        return _write_doc(
            _doc("Chola", [_product("commercial-gcv", "GCV", [_rule(["bike"])])])
        )

    def test_findings_merged_into_schema_result(self):
        from unittest import mock

        from cins.processors.core.pipeline import _run_validators
        from cins.validators.schema_validator import (
            Severity,
            ValidationIssue,
            ValidationResult,
        )

        stub = ValidationResult(
            is_valid=True,
            errors=[],
            warnings=[
                ValidationIssue(
                    severity=Severity.MEDIUM,
                    category="chola_product_hierarchy",
                    message="chola-finding-marker",
                    field_path="products[0]",
                )
            ],
            info=[],
        )
        with mock.patch.object(
            CholaProductHierarchyValidator, "validate", return_value=stub
        ):
            schema_result, _ = _run_validators(Path(self._path()))

        self.assertIsNotNone(schema_result)
        msgs = " ".join(
            i.message for i in (schema_result.errors + schema_result.warnings)
        )
        self.assertIn("chola-finding-marker", msgs)

    def test_validator_failure_never_breaks_the_pipeline(self):
        from unittest import mock

        from cins.processors.core.pipeline import _run_validators

        with mock.patch.object(
            CholaProductHierarchyValidator,
            "validate",
            side_effect=RuntimeError("boom"),
        ):
            schema_result, overlap_result = _run_validators(Path(self._path()))

        self.assertIsNotNone(schema_result)
