"""
Transform warnings/notices must reach PipelineResult on every path.

`extract_multi_pass` runs its own transformer per sheet and returns
`transform_warnings` / `transform_notices` in the pass_result dict. The pipeline
used to ignore both in the sheet-processing paths, so a normal extraction stored
zero transform warnings and the UI banner never appeared — only the
`rerun_transform` task ever surfaced them (which is why doc 733711bd showed 1078
warnings after a re-transform but 0 after a fresh extraction).

Run from the backend container:
    python manage.py test cins.tests.test_transform_warning_plumbing -v 2
"""

from unittest import TestCase

from cins.processors.core.pipeline import PipelineResult


class PipelineResultChannelTests(TestCase):
    def test_warnings_and_notices_are_separate_channels(self):
        r = PipelineResult(
            cics_doc={},
            output_path=None,
            warnings=["Unknown policy_type: 0.325"],
            notices=["Boundary bump cc_start_range 1000 -> 1001"],
        )
        self.assertEqual(r.warnings, ["Unknown policy_type: 0.325"])
        self.assertEqual(r.notices, ["Boundary bump cc_start_range 1000 -> 1001"])

    def test_both_default_empty(self):
        r = PipelineResult(cics_doc={}, output_path=None)
        self.assertEqual(r.warnings, [])
        self.assertEqual(r.notices, [])


class ExtractMultiPassContractTests(TestCase):
    """The keys the pipeline reads off pass_result must keep their names."""

    def test_llm_extract_emits_both_transform_keys(self):
        import inspect

        from cins.processors.core import llm_extract

        src = inspect.getsource(llm_extract)
        # Both return_passes payloads must carry the notice channel alongside
        # the warning one, or the multi-sheet aggregation silently collects [].
        self.assertEqual(src.count('"transform_warnings": transform_warnings'), 2)
        self.assertEqual(src.count('"transform_notices": list(transformer.notices)'), 2)

    def test_pipeline_reads_both_keys(self):
        import inspect

        from cins.processors.core import pipeline

        src = inspect.getsource(pipeline)
        self.assertIn('pass_result.get("transform_warnings")', src)
        self.assertIn('pass_result.get("transform_notices")', src)
