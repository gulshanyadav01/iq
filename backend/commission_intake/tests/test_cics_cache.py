"""Tests for the deterministic CICS output cache.

Pins the plan's Layer-2 contract:
- the cache key includes CICS_PIPELINE_VERSION (a transformer bugfix → cache miss);
- only clean, gate-passing results are stored (never requires_review / errored);
- a hit repopulates ExtractionPass + CICSResult without an LLM call.

Run from the backend container:
    python manage.py test commission_intake.tests.test_cics_cache -v2
"""

from types import SimpleNamespace

from django.test import TestCase

from commission_intake import cics_cache
from commission_intake.models import (
    CommissionDocument,
    CICSResult,
    ExtractionPass,
    CicsOutputCache,
)


def _result(cics_doc=None, errors=None, sheet_pass_results=None):
    return SimpleNamespace(
        cics_doc=cics_doc
        if cics_doc is not None
        else {"provider": {"provider_id": "FG"}},
        warnings=[],
        errors=errors or [],
        duration=1.5,
        sheet_results=[{"sheet": "Grid", "status": "success"}],
        sheet_pass_results=sheet_pass_results
        if sheet_pass_results is not None
        else [
            {
                "sheet_name": "Grid",
                "attachment_name": "",
                "pass_number": 3,
                "result": {"products": [], "overall_requires_review": False},
                "status": "completed",
            }
        ],
    )


class CacheKeyTests(TestCase):
    def _key(self, **over):
        base = dict(
            content_hash="abc",
            indexer_version="1.5",
            prompt_version_hash="phash",
            llm_provider="gemini",
            llm_model="gemini-2.5-pro",
        )
        base.update(over)
        return cics_cache.compute_cache_key(**base)

    def test_stable_for_same_inputs(self):
        self.assertEqual(self._key(), self._key())

    def test_pipeline_version_changes_key(self):
        a = self._key(pipeline_version="1.0.0")
        b = self._key(pipeline_version="1.0.1")
        self.assertNotEqual(a, b)

    def test_content_hash_changes_key(self):
        self.assertNotEqual(self._key(content_hash="a"), self._key(content_hash="b"))

    def test_model_changes_key(self):
        self.assertNotEqual(
            self._key(llm_model="gemini-2.5-pro"),
            self._key(llm_model="gpt-4o"),
        )

    def test_prompt_hash_nonempty_and_stable(self):
        h = cics_cache.compute_prompt_version_hash()
        self.assertTrue(h)
        self.assertEqual(h, cics_cache.compute_prompt_version_hash())


class ShouldCacheTests(TestCase):
    def test_clean_result_cached(self):
        self.assertTrue(cics_cache.should_cache(_result()))

    def test_errored_result_not_cached(self):
        self.assertFalse(cics_cache.should_cache(_result(errors=["boom"])))

    def test_empty_cics_not_cached(self):
        self.assertFalse(cics_cache.should_cache(_result(cics_doc={})))

    def test_requires_review_pass3_not_cached(self):
        spr = [
            {
                "pass_number": 3,
                "result": {"overall_requires_review": True},
                "status": "completed",
            }
        ]
        self.assertFalse(cics_cache.should_cache(_result(sheet_pass_results=spr)))

    def test_failed_pass_not_cached(self):
        spr = [{"pass_number": 3, "result": {}, "status": "failed"}]
        self.assertFalse(cics_cache.should_cache(_result(sheet_pass_results=spr)))


class StoreLookupRepopulateTests(TestCase):
    def setUp(self):
        self.doc = CommissionDocument.objects.create(
            original_filename="grid.xlsx",
            status=CommissionDocument.Status.COMPLETED,
        )

    def test_store_then_lookup_roundtrip(self):
        key = "k1"
        cics_cache.store(
            cache_key=key,
            content_hash="abc",
            indexer_version="1.5",
            prompt_version_hash="phash",
            llm_provider="gemini",
            llm_model="gemini-2.5-pro",
            result=_result(),
            provider_id="FG",
        )
        row = cics_cache.lookup(key)
        self.assertIsNotNone(row)
        self.assertEqual(row.pipeline_version, cics_cache.CICS_PIPELINE_VERSION)
        self.assertEqual(row.provider_id, "FG")

    def test_repopulate_rebuilds_rows_without_llm(self):
        from commission_intake.tasks import _repopulate_from_cache

        cached = CicsOutputCache.objects.create(
            cache_key="k2",
            content_hash="abc",
            indexer_version="1.5",
            prompt_version_hash="phash",
            pipeline_version=cics_cache.CICS_PIPELINE_VERSION,
            cics_doc={"provider": {"provider_id": "FG"}},
            sheet_results=[{"sheet": "Grid"}],
            sheet_pass_results=[
                {
                    "sheet_name": "Grid",
                    "attachment_name": "",
                    "pass_number": 3,
                    "result": {"products": []},
                    "status": "completed",
                }
            ],
            provider_id="FG",
        )
        _repopulate_from_cache(self.doc, cached)

        self.assertTrue(
            ExtractionPass.objects.filter(
                document=self.doc, pass_number=3, sheet_name="Grid"
            ).exists()
        )
        cics = CICSResult.objects.get(document=self.doc)
        self.assertEqual(cics.cics_doc, {"provider": {"provider_id": "FG"}})
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.provider, "FG")


def _cics(products):
    """products: list of (name, sub_category, n_rules)."""
    return {
        "products": [
            {
                "product_name": n,
                "sub_category": s,
                "commission_rules": [{"rule_id": f"{n}-{i}"} for i in range(k)],
            }
            for (n, s, k) in products
        ]
    }


class RegressionGuardTests(TestCase):
    def test_no_regression_same_output(self):
        base = _cics([("Commercial Vehicle", "GCV", 100), ("Private Car", "", 50)])
        r = cics_cache.detect_regression(base, base)
        self.assertFalse(r.is_regression)

    def test_disappeared_product_flagged(self):
        base = _cics(
            [
                ("Commercial Vehicle", "PCV", 60),
                ("Commercial Vehicle", "Buses", 40),
                ("Private Car", "", 50),
            ]
        )
        # Buses segment dropped entirely.
        new = _cics([("Commercial Vehicle", "PCV", 60), ("Private Car", "", 50)])
        r = cics_cache.detect_regression(base, new)
        self.assertTrue(r.is_regression)
        self.assertEqual(r.reason, "disappeared products")
        self.assertIn("buses", r.warning.lower())

    def test_subcategory_rename_preserving_total_not_flagged(self):
        # A sub_category rename moves the same rules to a new bucket: the old key
        # disappears but the total is preserved → NOT a regression (taxonomy churn).
        base = _cics([("Commercial Vehicle", "gcv-3w", 100), ("Private Car", "", 50)])
        new = _cics(
            [("Commercial Vehicle", "goods carrying 3w", 100), ("Private Car", "", 50)]
        )
        self.assertFalse(cics_cache.detect_regression(base, new).is_regression)

    def test_big_total_shortfall_flagged(self):
        base = _cics([("Commercial Vehicle", "GCV", 400)])
        new = _cics([("Commercial Vehicle", "GCV", 200)])  # 50% < 0.75 floor
        r = cics_cache.detect_regression(base, new)
        self.assertTrue(r.is_regression)
        self.assertEqual(r.reason, "total shortfall")

    def test_small_shortfall_not_flagged(self):
        base = _cics([("Commercial Vehicle", "GCV", 100)])
        new = _cics([("Commercial Vehicle", "GCV", 90)])  # 90% > 0.75
        self.assertFalse(cics_cache.detect_regression(base, new).is_regression)

    def test_tiny_doc_below_min_total_not_flagged(self):
        base = _cics([("X", "", 10)])
        new = _cics([("X", "", 2)])  # big ratio drop but base_total 10 < 20 min
        self.assertFalse(cics_cache.detect_regression(base, new).is_regression)

    def test_growth_not_flagged(self):
        base = _cics([("Commercial Vehicle", "GCV", 100)])
        new = _cics([("Commercial Vehicle", "GCV", 300)])
        self.assertFalse(cics_cache.detect_regression(base, new).is_regression)

    def test_disabled_env(self):
        import os
        from unittest import mock

        base = _cics([("A", "", 100), ("B", "", 100)])
        new = _cics([("A", "", 100)])
        with mock.patch.dict(os.environ, {"CINS_REGRESSION_GUARD": "false"}):
            self.assertFalse(cics_cache.detect_regression(base, new).is_regression)

    def test_no_baseline_no_regression(self):
        self.assertFalse(
            cics_cache.detect_regression(None, _cics([("A", "", 5)])).is_regression
        )


class FindPriorBaselineTests(TestCase):
    def test_finds_by_content_hash_excluding_current_key(self):
        cics_cache.store(
            cache_key="old",
            content_hash="H1",
            indexer_version="1.5",
            prompt_version_hash="p1",
            llm_provider="gemini",
            llm_model="m",
            result=_result(cics_doc=_cics([("A", "", 10)])),
        )
        found = cics_cache.find_prior_baseline("H1", exclude_cache_key="new")
        self.assertIsNotNone(found)
        self.assertEqual(found.cache_key, "old")
        # excluding the only row's own key returns nothing
        self.assertIsNone(cics_cache.find_prior_baseline("H1", exclude_cache_key="old"))

    def test_no_match_returns_none(self):
        self.assertIsNone(cics_cache.find_prior_baseline("nope"))
        self.assertIsNone(cics_cache.find_prior_baseline(""))


class RunExtractionCacheHitTests(TestCase):
    """Integration: run_extraction serves a cache HIT without any LLM/pipeline work
    and rebuilds the UI rows (the plan's 'rerun → LLM not called' verification)."""

    def test_hit_repopulates_without_invoking_pipeline(self):
        from unittest import mock
        from commission_intake.models import IndexedContent
        from commission_intake.tasks import run_extraction

        doc = CommissionDocument.objects.create(
            original_filename="grid.xlsx",
            status=CommissionDocument.Status.COMPLETED,
            llm_provider="gemini",
        )
        IndexedContent.objects.create(
            document=doc,
            source_type="excel",
            sheet_contents={"Grid": "| P | Rate |\n| A | 20% |"},
            sheet_metadata={},
            content_hash="HASH123",
            indexer_version="1.5",
        )

        svc = mock.Mock()
        svc.model = "test-model"

        # The exact key run_extraction will compute for this doc.
        key = cics_cache.compute_cache_key(
            content_hash="HASH123",
            indexer_version="1.5",
            prompt_version_hash=cics_cache.compute_prompt_version_hash(),
            llm_provider="gemini",
            llm_model="test-model",
        )
        CicsOutputCache.objects.create(
            cache_key=key,
            content_hash="HASH123",
            indexer_version="1.5",
            prompt_version_hash="x",
            pipeline_version=cics_cache.CICS_PIPELINE_VERSION,
            cics_doc={"provider": {"provider_id": "FG"}, "products": []},
            sheet_pass_results=[
                {
                    "sheet_name": "Grid",
                    "attachment_name": "",
                    "pass_number": 3,
                    "result": {"products": []},
                    "status": "completed",
                }
            ],
            provider_id="FG",
            hit_count=0,
        )

        # Pipeline must NEVER be constructed on a hit — patch it to blow up if it is.
        with (
            mock.patch(
                "cins.processors.llm_services.factory.get_llm_service",
                return_value=svc,
            ),
            mock.patch(
                "cins.processors.core.pipeline.Pipeline",
                side_effect=AssertionError("LLM pipeline invoked on a cache HIT"),
            ),
        ):
            run_extraction(str(doc.id))

        # UI rows rebuilt from the cache, no LLM.
        self.assertTrue(CICSResult.objects.filter(document=doc).exists())
        self.assertTrue(
            ExtractionPass.objects.filter(
                document=doc, pass_number=3, sheet_name="Grid"
            ).exists()
        )
        self.assertEqual(CicsOutputCache.objects.get(cache_key=key).hit_count, 1)
        doc.refresh_from_db()
        self.assertEqual(doc.provider, "FG")
