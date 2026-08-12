"""
Tests for ``StateRtoPresenceValidator`` and the shared ``product_bucket`` helper.

State and RTO are mandatory on every exported deal: no source grid in the
carrier corpus prices a deal without a geography, so a blank cell is always a
pipeline defect. The validator is the net that was missing when Chola's 2W and
commercial deals shipped with both columns blank --
``StateColumnAlignmentValidator`` is scoped to Private Car and
``RowCoverageValidator`` counts a state-less row as covered.

The RTO half asserts the EFFECTIVE EXPORTED value (explicit ``conditions.rto``,
else the state-derived fill) rather than ``conditions.rto`` itself, which is
legitimately empty on most rules.

Run from the backend container:
    python manage.py test cins.tests.test_state_rto_presence_validator -v 2
"""

import json
import os
import tempfile
import unittest.mock as mock

from django.test import TestCase as DjangoTestCase

from cins.validators.state_rto_presence_validator import StateRtoPresenceValidator
from cins.validators.schema_validator import Severity
from master_data.models import RTOMaster
from master_data.services.resolver import product_bucket


def _rule(rule_id, **conditions):
    return {
        "rule_id": rule_id,
        "conditions": conditions,
        "outcome": {"commission_type": "percentage", "percentage_value": 10},
    }


def _doc(rules, product_name="Two Wheeler", sub_category="2w"):
    return {
        "provider": {"provider_id": "Chola", "provider_name": "Chola"},
        "products": [
            {
                "product_name": product_name,
                "sub_category": sub_category,
                "commission_rules": rules,
            }
        ],
    }


def _write_doc(doc):
    """Write a CICS doc to a temp file and return its path.

    mkstemp, not mktemp: the latter returns a name without creating the file,
    which races under `manage.py test --parallel`.
    """
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return path


def _validate(doc):
    path = _write_doc(doc)
    try:
        return StateRtoPresenceValidator().validate(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)


class ProductBucketTests(DjangoTestCase):
    """Chola writes its CV tables as GCCV / PCCV / MSV.

    ``gcv`` is not a substring of ``gccv`` and ``pcv`` is not a substring of
    ``pccv``, so before these tokens existed a product_name that escaped
    canonicalization resolved to bucket None and the export silently emitted an
    empty RTO cell even for rules carrying a good state.
    """

    def test_canonical_names_resolve(self):
        self.assertEqual(product_bucket("Commercial Vehicle"), "CV")
        self.assertEqual(product_bucket("Two Wheeler"), "TW")
        self.assertEqual(product_bucket("Private Car"), "CAR")

    def test_chola_cv_spellings_resolve(self):
        for name in ("GCCV", "PCCV", "PCCV < 6", "MSV", "Misc-D", "MISCD"):
            with self.subTest(name=name):
                self.assertEqual(product_bucket(name), "CV")

    def test_two_wheeler_abbreviations_resolve(self):
        for name in ("TW", "2W", "Two Wheeler"):
            with self.subTest(name=name):
                self.assertEqual(product_bucket(name), "TW")

    def test_four_wheeler_is_not_a_two_wheeler(self):
        self.assertNotEqual(product_bucket("Four Wheeler"), "TW")

    def test_unknown_name_is_none(self):
        self.assertIsNone(product_bucket("Marine Cargo"))
        self.assertIsNone(product_bucket(""))
        self.assertIsNone(product_bucket(None))

    def test_plural_product_names_still_resolve(self):
        """Regression: word-boundary matching must not drop plurals.

        The first cut of the boundary fix matched a bare token only, so
        "Buses" / "Cars" / "PCVs" — the same products their singulars name —
        fell through to None. That is not a silent blank any more: a None
        bucket also raises a HIGH in StateRtoPresenceValidator, which blocks
        the CICS output-cache pin for the whole document.
        """
        self.assertEqual(product_bucket("Buses"), "CV")
        self.assertEqual(product_bucket("PCVs"), "CV")
        self.assertEqual(product_bucket("Cars"), "CAR")
        self.assertEqual(product_bucket("Taxis"), "CAR")
        self.assertEqual(product_bucket("Wheelers"), "TW")

    def test_camelcase_product_names_still_resolve(self):
        """Regression: ``PrivateCar`` is how chola_product_hierarchy.csv spells it.

        There is no word boundary before "Car" in "PrivateCar", so the bare
        boundary form rejected the carrier's own spelling.
        """
        self.assertEqual(product_bucket("PrivateCar"), "CAR")
        self.assertEqual(product_bucket("TwoWheeler"), "TW")

    def test_substring_hole_stays_closed(self):
        """The plural/camel allowances must not reopen what they replaced.

        "cargo" is not "car"/"cars"; "business" is not "bus"/"buses".
        """
        for name in ("Marine Cargo", "Cargo", "Business Package", "Business", "Scars"):
            with self.subTest(name=name):
                self.assertIsNone(product_bucket(name))

    def test_digit_adjacent_names_resolve(self):
        """Regression: a digit must read as a boundary, not as a letter.

        `(?!\\w)` counted digits as word characters, so "GCV3T" / "PCV3W" —
        ordinary carrier grid spellings the bare substring form matched fine —
        resolved to None, blanking the RTO cell and dropping the product out of
        the export's claimed-RTO pre-pass.
        """
        self.assertEqual(product_bucket("GCV3T"), "CV")
        self.assertEqual(product_bucket("PCV3W"), "CV")
        self.assertEqual(product_bucket("TW1+1"), "TW")

    def test_three_wheelers_are_commercial_not_two_wheeler(self):
        """A 3W is a passenger/goods carrier. Filling its RTO cell from the
        two-wheeler zone column is silent financial misattribution."""
        for name in ("Three Wheeler", "3W", "3 Wheeler", "GCV 3W"):
            with self.subTest(name=name):
                self.assertEqual(product_bucket(name), "CV")

    def test_four_wheeler_is_a_car(self):
        """PRODUCT_NAME_MAPPINGS maps it to Private Car; the bucket must agree.

        Previously the TW four-guard skipped TW and nothing else matched, so an
        ordinary product name resolved to None.
        """
        self.assertEqual(product_bucket("Four Wheeler"), "CAR")
        self.assertEqual(product_bucket("4W"), "CAR")

    def test_common_commercial_abbreviations_resolve(self):
        for name in (
            "CV",
            "SCV",
            "LCV",
            "HCV",
            "Passenger Carrying Vehicle",
            "Goods Carrying Vehicle",
            "Tractor",
            "Trailer",
            "E-Rickshaw",
        ):
            with self.subTest(name=name):
                self.assertEqual(product_bucket(name), "CV")

    def test_non_motor_lines_stay_unbucketed(self):
        """They must resolve to None — and that must not be a HIGH."""
        for name in ("Health", "Fire", "Marine Cargo", "Travel"):
            with self.subTest(name=name):
                self.assertIsNone(product_bucket(name))


class StatePresenceTests(DjangoTestCase):
    @classmethod
    def setUpTestData(cls):
        RTOMaster.objects.create(
            rto_id=1, code="AP01", value="AP-01", state_slug="andhra-pradesh", tw_zone=1
        )

    def test_missing_state_is_high(self):
        result = _validate(_doc([_rule("r1")]))
        self.assertEqual(len(result.errors), 1)
        issue = result.errors[0]
        self.assertEqual(issue.severity, Severity.HIGH)
        self.assertIn("State is mandatory", issue.message)
        self.assertIn("r1", issue.message)

    def test_well_formed_rule_is_silent(self):
        result = _validate(_doc([_rule("r1", state=["andhra-pradesh"])]))
        self.assertEqual(result.errors, [])

    def test_missing_state_reports_once_per_product_not_per_rule(self):
        rules = [_rule(f"r{i}") for i in range(20)]
        result = _validate(_doc(rules))
        self.assertEqual(len(result.errors), 1)
        self.assertIn("20 rule(s)", result.errors[0].message)

    def test_state_failure_does_not_also_report_rto(self):
        """RTO is derived from state; reporting both doubles every finding."""
        result = _validate(_doc([_rule("r1")]))
        self.assertEqual(len(result.errors), 1)

    def test_env_flag_disables_the_validator(self):
        with mock.patch.dict(os.environ, {"CINS_STATE_RTO_MANDATORY": "false"}):
            result = _validate(_doc([_rule("r1")]))
        self.assertEqual(result.errors, [])


class RtoPresenceTests(DjangoTestCase):
    @classmethod
    def setUpTestData(cls):
        # TW-active in Andhra Pradesh; Karnataka has no TW-active RTO at all.
        RTOMaster.objects.create(
            rto_id=1, code="AP01", value="AP-01", state_slug="andhra-pradesh", tw_zone=1
        )
        RTOMaster.objects.create(
            rto_id=2, code="KA01", value="KA-01", state_slug="karnataka", tw_zone=None
        )

    def test_explicit_rto_always_passes(self):
        result = _validate(
            _doc([_rule("r1", state=["karnataka"], rto=["KA-01"])]),
        )
        self.assertEqual(result.errors, [])

    def test_state_derived_fill_passes(self):
        result = _validate(_doc([_rule("r1", state=["andhra-pradesh"])]))
        self.assertEqual(result.errors, [])

    def test_unresolvable_product_bucket_is_medium_not_high(self):
        """A blank RTO from an unbucketable product must NOT block the cache.

        Non-motor lines (Health, Fire, Marine) resolve to None and always
        will. A HIGH would wall every mixed statement out of the output cache
        permanently, re-running full LLM codegen on every access.
        """
        result = _validate(
            _doc([_rule("r1", state=["andhra-pradesh"])], product_name="Widget Cover")
        )
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.warnings), 1)
        issue = result.warnings[0]
        self.assertEqual(issue.severity, Severity.MEDIUM)
        self.assertIn("Widget Cover", issue.message)
        self.assertIn("no RTO product bucket", issue.message)

    def test_non_motor_product_does_not_block_the_cache(self):
        """The end-to-end consequence of the severity choice."""
        from commission_intake.cics_cache import should_cache

        result = _validate(
            _doc([_rule("r1", state=["andhra-pradesh"])], product_name="Fire")
        )

        class _PipelineResult:
            errors = []
            cics_doc = {"products": [{"product_name": "Fire"}]}
            sheet_pass_results = []
            validation_issues = result.errors

        self.assertTrue(should_cache(_PipelineResult()))

    def test_state_with_no_product_active_rtos_is_high(self):
        result = _validate(_doc([_rule("r1", state=["karnataka"])]))
        self.assertEqual(len(result.errors), 1)
        self.assertIn("no product-active RTOs", result.errors[0].message)

    def test_unreadable_cics_degrades_to_medium_not_high(self):
        """A false hard-fail walls out a document; stay conservative."""
        result = StateRtoPresenceValidator().validate("/nonexistent/path.json")
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(result.warnings[0].severity, Severity.MEDIUM)

    def test_scalar_state_is_accepted(self):
        """A rule may carry a bare string instead of a list."""
        result = _validate(_doc([_rule("r1", state="andhra-pradesh")]))
        self.assertEqual(result.errors, [])

    def test_resolver_unavailable_skips_the_rto_half(self):
        """Master data missing means the RTO half is not evaluable.

        Guessing would wall out a document on a HIGH the pipeline cannot
        justify, so the state half still runs and the RTO half stays quiet.
        """
        v = StateRtoPresenceValidator()
        v._resolver = None
        path = _write_doc(_doc([_rule("r1", state=["karnataka"])]))
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        result = v.validate(path)
        # Non-blocking, but NOT silent: reporting clean here is how a blank-RTO
        # document gets pinned on a worker whose DB was briefly unavailable.
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(result.warnings[0].severity, Severity.MEDIUM)
        self.assertIn("RTO master could not be loaded", result.warnings[0].message)

    def test_resolver_exception_fails_open(self):
        """A raising resolver must not turn into a cache-blocking HIGH."""
        v = StateRtoPresenceValidator()
        path = _write_doc(_doc([_rule("r1", state=["andhra-pradesh"])]))
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        with mock.patch.object(
            v._resolver, "rtos_for_states", side_effect=RuntimeError("boom")
        ):
            self.assertEqual(v.validate(path).errors, [])


class PipelinePlumbingTests(DjangoTestCase):
    """pipeline._run_validators merges this validator's findings into
    ``schema_result`` — and a failure inside it never breaks the pipeline.

    Mirrors PipelinePlumbingTests in test_chola_product_validator.py; without
    it the registration itself is unverified and a silently-dropped validator
    would look identical to a clean document.
    """

    @classmethod
    def setUpTestData(cls):
        # Needed by the clean-document case: without a TW-active RTO the
        # "clean" doc would legitimately raise a no-product-active-RTOs HIGH.
        RTOMaster.objects.create(
            rto_id=1, code="AP01", value="AP-01", state_slug="andhra-pradesh", tw_zone=1
        )

    def _path(self):
        path = _write_doc(_doc([_rule("r1", state=["andhra-pradesh"])]))
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def test_findings_merged_into_schema_result(self):
        from pathlib import Path

        from cins.processors.core.pipeline import _run_validators
        from cins.validators.schema_validator import ValidationIssue, ValidationResult

        stub = ValidationResult(
            is_valid=True,
            errors=[
                ValidationIssue(
                    severity=Severity.HIGH,
                    category="State/RTO Presence",
                    message="state-rto-finding-marker",
                    field_path="products[0]",
                )
            ],
            warnings=[],
            info=[],
        )
        with mock.patch.object(
            StateRtoPresenceValidator, "validate", return_value=stub
        ):
            schema_result, _ = _run_validators(Path(self._path()))

        self.assertIsNotNone(schema_result)
        msgs = " ".join(
            i.message for i in (schema_result.errors + schema_result.warnings)
        )
        self.assertIn("state-rto-finding-marker", msgs)

    def test_validator_failure_never_breaks_the_pipeline(self):
        from pathlib import Path

        from cins.processors.core.pipeline import _run_validators

        with mock.patch.object(
            StateRtoPresenceValidator,
            "validate",
            side_effect=RuntimeError("boom"),
        ):
            schema_result, _ = _run_validators(Path(self._path()))

        self.assertIsNotNone(schema_result)

    def test_high_finding_blocks_the_output_cache_pin(self):
        """The whole point of HIGH: a state-less document must not be cached.

        Pins the full chain end to end — validator -> schema_result.errors ->
        PipelineResult.validation_issues (pipeline.py:828) -> should_cache.
        Without this, a refactor that dropped the merge would look identical to
        a clean document and the blank export would be pinned and replayed.
        """
        from commission_intake.cics_cache import should_cache

        result = StateRtoPresenceValidator().validate(
            _write_doc(_doc([_rule("r1")]))  # no state -> HIGH
        )
        self.assertTrue(result.errors)

        class _PipelineResult:
            errors = []
            cics_doc = {"products": []}
            sheet_pass_results = []
            validation_issues = result.errors

        self.assertFalse(should_cache(_PipelineResult()))

    def test_clean_document_still_caches(self):
        """Guard the other direction — the gate must not block everything."""
        from commission_intake.cics_cache import should_cache

        result = StateRtoPresenceValidator().validate(
            _write_doc(_doc([_rule("r1", state=["andhra-pradesh"])]))
        )
        self.assertEqual(result.errors, [])

        class _PipelineResult:
            errors = []
            cics_doc = {"products": [{"product_name": "Two Wheeler"}]}
            sheet_pass_results = []
            validation_issues = result.errors

        self.assertTrue(should_cache(_PipelineResult()))
