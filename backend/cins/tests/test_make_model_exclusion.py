"""
R3: Chola GCCV ≤3.5T make/model bucket fan-out.

The grid publishes an explicit ``TATA/Maruti/Mahindra + <model list>`` row and a
sentinel ``All Other Make/Models`` row sharing one rate. The transformer must
fan the sentinel into two exclusion buckets so all three buckets survive:

  (a) TMM + model list                 (explicit sibling, unchanged)
  (b) TMM, NOT those models            (sentinel → _XMODEL, model_exclude)
  (c) makes OTHER than TMM             (sentinel → _XMAKE, make_exclude)

Non-Chola docs must NOT fan out (the sentinel regex would otherwise re-expand
TW junk "Others" rows the collapse exists to kill).

Run from the backend container:
    python manage.py test cins.tests.test_make_model_exclusion -v 2
"""

from unittest import TestCase

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
from cins.schema.raw_extraction import (
    RawCommissionEntry,
    RawExtraction,
    RawProviderInfo,
)


def _raw(entries, provider="Chola"):
    return RawExtraction(
        source_file="test.xlsx",
        source_type="excel",
        provider=RawProviderInfo(name=provider),
        start_date="2024-07-01",
        end_date="2024-07-31",
        commission_entries=entries,
    )


def _gccv_entry(make, model, value):
    return RawCommissionEntry(
        rule_text="Commercial Vehicle 2_UPTO_3.5T",
        commission_value=value,
        product_name="Commercial Vehicle",
        product_type="GCV",
        coverage_type_text="comprehensive",
        conditions={"gvw": "UPTO 3.5T", "vehicle_make": make, "vehicle_model": model},
    )


class MakeBucketFanOutTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def _buckets(self, entries):
        product = self.t._transform_product(
            "Commercial Vehicle", entries, None, _raw(entries), segment="GCV"
        )
        return product.commission_rules

    def test_sentinel_and_sibling_produce_three_buckets(self):
        entries = [
            _gccv_entry("TATA, Maruti, Mahindra", "Ace, Super Ace", "42.5%"),
            _gccv_entry("All Other Make/Models", "All Other Make/Models", "20%"),
        ]
        rules = self._buckets(entries)

        a = [
            r
            for r in rules
            if r.conditions.vehicle_make
            and r.conditions.vehicle_model
            and not r.conditions.vehicle_model_exclude
        ]
        b = [
            r
            for r in rules
            if r.conditions.vehicle_make and r.conditions.vehicle_model_exclude
        ]
        c = [r for r in rules if r.conditions.vehicle_make_exclude]
        self.assertEqual(len(a), 1, rules)
        self.assertEqual(len(b), 1, rules)
        self.assertEqual(len(c), 1, rules)

        self.assertEqual(
            set(b[0].conditions.vehicle_make), {"TATA", "Maruti", "Mahindra"}
        )
        self.assertEqual(
            set(b[0].conditions.vehicle_model_exclude), {"Ace", "Super Ace"}
        )
        self.assertEqual(
            set(c[0].conditions.vehicle_make_exclude), {"TATA", "Maruti", "Mahindra"}
        )

    def test_bucket_outcomes_preserved(self):
        # (b) and (c) share the sentinel's rate; (a) keeps its own.
        entries = [
            _gccv_entry("TATA, Maruti, Mahindra", "Ace, Super Ace", "42.5%"),
            _gccv_entry("All Other Make/Models", "All Other Make/Models", "20%"),
        ]
        rules = self._buckets(entries)
        a = next(
            r
            for r in rules
            if r.conditions.vehicle_model and not r.conditions.vehicle_model_exclude
        )
        b = next(r for r in rules if r.conditions.vehicle_model_exclude)
        c = next(r for r in rules if r.conditions.vehicle_make_exclude)
        self.assertEqual(a.outcome.percentage_value, 42.5)
        self.assertEqual(b.outcome.percentage_value, 20.0)
        self.assertEqual(c.outcome.percentage_value, 20.0)

    def test_buckets_stay_4w_only_with_light_gvw(self):
        entries = [
            _gccv_entry("TATA, Maruti, Mahindra", "Ace", "42.5%"),
            _gccv_entry("All Other Make/Models", "All Other Make/Models", "20%"),
        ]
        rules = self._buckets(entries)
        # A light (0,3500) GVW band is a real weight grade → 4W-and-Above only.
        for r in rules:
            self.assertEqual(r.conditions.number_of_wheels, ["4w-and-above"])
            self.assertEqual(
                (r.conditions.gvw_start_range, r.conditions.gvw_end_range), (0, 3500)
            )

    def test_fan_out_idempotent_on_double_run(self):
        # Re-running the fan-out on already-bucketed rules is a no-op (the
        # sentinel is gone after the first pass) — guards against a double
        # transform doubling the buckets.
        entries = [
            _gccv_entry("TATA, Maruti, Mahindra", "Ace", "42.5%"),
            _gccv_entry("All Other Make/Models", "All Other Make/Models", "20%"),
        ]
        rules = self._buckets(entries)
        again = self.t._bifurcate_make_buckets(list(rules))
        self.assertEqual(len(again), len(rules))
        self.assertEqual(
            sum(1 for r in again if r.conditions.vehicle_make_exclude),
            sum(1 for r in rules if r.conditions.vehicle_make_exclude),
        )

    def test_sentinel_equal_rate_not_collapsed_regression(self):
        # The screenshot bug: sentinel with the SAME rate as its sibling must
        # NOT be collapsed away — it fans into (b)+(c).
        entries = [
            _gccv_entry("TATA, Maruti, Mahindra", "Ace", "20%"),
            _gccv_entry("All Other Make/Models", "All Other Make/Models", "20%"),
        ]
        rules = self._buckets(entries)
        self.assertTrue(any(r.conditions.vehicle_make_exclude for r in rules))
        self.assertTrue(any(r.conditions.vehicle_model_exclude for r in rules))


class NoSiblingFallbackTests(TestCase):
    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def test_sentinel_without_sibling_kept_verbatim_with_warning(self):
        entries = [_gccv_entry("All Other Make/Models", "All Other Make/Models", "20%")]
        product = self.t._transform_product(
            "Commercial Vehicle", entries, None, _raw(entries), segment="GCV"
        )
        rules = product.commission_rules
        # No exclusion buckets fabricated.
        self.assertFalse(any(r.conditions.vehicle_make_exclude for r in rules))
        self.assertTrue(
            any("no explicit sibling" in w for w in self.t.warnings), self.t.warnings
        )

    def test_sibling_with_no_make_list_kept_verbatim(self):
        # Sibling matches the sentinel's conditions but carries only a model
        # list (no make) — buckets can't be built; sentinel kept, warned.
        model_only = RawCommissionEntry(
            rule_text="Commercial Vehicle 2_UPTO_3.5T",
            commission_value="42.5%",
            product_name="Commercial Vehicle",
            product_type="GCV",
            coverage_type_text="comprehensive",
            conditions={"gvw": "UPTO 3.5T", "vehicle_model": "Ace"},
        )
        entries = [
            model_only,
            _gccv_entry("All Other Make/Models", "All Other Make/Models", "20%"),
        ]
        product = self.t._transform_product(
            "Commercial Vehicle", entries, None, _raw(entries), segment="GCV"
        )
        rules = product.commission_rules
        self.assertFalse(any(r.conditions.vehicle_make_exclude for r in rules))
        self.assertTrue(
            any("no make list" in w for w in self.t.warnings), self.t.warnings
        )


class NonCholaPassthroughTests(TestCase):
    def test_non_chola_does_not_fan_out(self):
        t = RawToCICSTransformer()
        t._carrier_code = "Digit"
        entries = [
            _gccv_entry("TATA, Maruti, Mahindra", "Ace", "42.5%"),
            _gccv_entry("All Other Make/Models", "All Other Make/Models", "20%"),
        ]
        product = t._transform_product(
            "Commercial Vehicle",
            entries,
            None,
            _raw(entries, provider="Digit"),
            segment="GCV",
        )
        rules = product.commission_rules
        self.assertFalse(any(r.conditions.vehicle_make_exclude for r in rules))
        self.assertFalse(any(r.conditions.vehicle_model_exclude for r in rules))


class ZeroAllOtherSynthesisTests(TestCase):
    """When the grid grades only the named makes (explicit make/model row) and
    leaves the 'All Other Make/Models' cell BLANK (no sentinel row), the
    transformer synthesizes the missing all-other buckets at 0% so every graded
    cell carries the full make/model distinction (client rule: blank = 0%)."""

    def setUp(self):
        self.t = RawToCICSTransformer()
        self.t._carrier_code = "Chola"

    def _buckets(self, entries):
        product = self.t._transform_product(
            "Commercial Vehicle", entries, None, _raw(entries), segment="GCV"
        )
        return product.commission_rules

    def test_explicit_row_without_sentinel_synthesizes_zero_buckets(self):
        # Only the TMM make/model row — no 'All Other' sibling in the source.
        entries = [_gccv_entry("TATA, Maruti, Mahindra", "Ace, Super Ace", "42.5%")]
        rules = self._buckets(entries)

        a = [
            r
            for r in rules
            if r.conditions.vehicle_make
            and r.conditions.vehicle_model
            and not r.conditions.vehicle_model_exclude
        ]
        b = [
            r
            for r in rules
            if r.conditions.vehicle_make and r.conditions.vehicle_model_exclude
        ]
        c = [r for r in rules if r.conditions.vehicle_make_exclude]
        self.assertEqual(len(a), 1, rules)
        self.assertEqual(len(b), 1, rules)
        self.assertEqual(len(c), 1, rules)

        # (a) keeps its real rate; the synthesized all-other buckets are 0%.
        self.assertEqual(a[0].outcome.percentage_value, 42.5)
        self.assertEqual(b[0].outcome.percentage_value, 0.0)
        self.assertEqual(c[0].outcome.percentage_value, 0.0)

        # Exclusion lists mirror the named makes/models.
        self.assertEqual(
            set(b[0].conditions.vehicle_make), {"TATA", "Maruti", "Mahindra"}
        )
        self.assertEqual(
            set(b[0].conditions.vehicle_model_exclude), {"Ace", "Super Ace"}
        )
        self.assertEqual(
            set(c[0].conditions.vehicle_make_exclude), {"TATA", "Maruti", "Mahindra"}
        )
        self.assertTrue(
            any("0% synthesis" in n for n in self.t.notices), self.t.notices
        )

    def test_sentinel_present_no_zero_synthesis(self):
        # A real sentinel supplies the all-other rate — buckets come from it at
        # the sentinel rate, NOT synthesized at 0%.
        entries = [
            _gccv_entry("TATA, Maruti, Mahindra", "Ace", "42.5%"),
            _gccv_entry("All Other Make/Models", "All Other Make/Models", "20%"),
        ]
        rules = self._buckets(entries)
        c = [r for r in rules if r.conditions.vehicle_make_exclude]
        self.assertEqual(len(c), 1, rules)
        self.assertEqual(c[0].outcome.percentage_value, 20.0)
        self.assertFalse(
            any("0% synthesis" in n for n in self.t.notices), self.t.notices
        )

    def test_no_makemodel_row_no_synthesis(self):
        # A plain rule with no make/model is not graded — nothing synthesized.
        plain = RawCommissionEntry(
            rule_text="Commercial Vehicle 3_3.5T_TO_7.5T",
            commission_value="10%",
            product_name="Commercial Vehicle",
            product_type="GCV",
            coverage_type_text="comprehensive",
            conditions={"gvw": "3.5T TO 7.5T"},
        )
        rules = self._buckets([plain])
        self.assertFalse(any(r.conditions.vehicle_make_exclude for r in rules))
        self.assertFalse(any(r.conditions.vehicle_model_exclude for r in rules))

    def test_non_chola_no_zero_synthesis(self):
        t = RawToCICSTransformer()
        t._carrier_code = "Digit"
        entries = [_gccv_entry("TATA, Maruti, Mahindra", "Ace", "42.5%")]
        product = t._transform_product(
            "Commercial Vehicle",
            entries,
            None,
            _raw(entries, provider="Digit"),
            segment="GCV",
        )
        rules = product.commission_rules
        self.assertFalse(any(r.conditions.vehicle_make_exclude for r in rules))
        self.assertFalse(any(r.conditions.vehicle_model_exclude for r in rules))


class SentinelRegexNegativeTests(TestCase):
    """Word-boundary anchored regex must NOT match legit makes ending in
    '…brothers' (would wrongly fan out real TW rows)."""

    def setUp(self):
        self.t = RawToCICSTransformer()

    def _rule(self, make):
        e = RawCommissionEntry(
            rule_text="r",
            commission_value="10%",
            product_name="Commercial Vehicle",
            conditions={"vehicle_make": make},
        )
        cond = self.t._transform_conditions(e, None, _raw([e]))
        from cins.schema.cics_model import CommissionRule, CommissionOutcome

        return CommissionRule(
            rule_id="r1",
            priority=1,
            conditions=cond,
            outcome=CommissionOutcome(
                commission_type="percentage", percentage_value=10.0
            ),
        )

    def test_all_other_is_sentinel(self):
        self.assertTrue(
            self.t._is_make_model_sentinel(self._rule("All Other Make/Models"))
        )

    def test_others_is_sentinel(self):
        self.assertTrue(self.t._is_make_model_sentinel(self._rule("Others")))

    def test_brothers_is_not_sentinel(self):
        self.assertFalse(self.t._is_make_model_sentinel(self._rule("Ashok Brothers")))

    def test_named_make_is_not_sentinel(self):
        self.assertFalse(self.t._is_make_model_sentinel(self._rule("TATA")))
