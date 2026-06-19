"""Tests for the shallow-key config merge (F4 in the approved plan).

Run from the backend container:
    python manage.py test pipelines.tests.test_snapshot_merge -v 2
"""

from unittest import TestCase

from pipelines.services.snapshot import merge_config


class MergeConfigTests(TestCase):
    """Shallow-key replacement, NOT recursive deep-merge."""

    def test_replaces_top_level_keys(self):
        base = {"model": "gemini-3.1-flash-lite", "prompt": "default"}
        override = {"prompt": "custom"}
        self.assertEqual(
            merge_config(base, override),
            {"model": "gemini-3.1-flash-lite", "prompt": "custom"},
        )

    def test_shallow_not_deep(self):
        """Nested dicts in override REPLACE the base entry; they do NOT merge recursively."""
        base = {"options": {"x": 1, "y": 2}}
        override = {"options": {"y": 99}}
        self.assertEqual(merge_config(base, override), {"options": {"y": 99}})

    def test_handles_none_inputs(self):
        self.assertEqual(merge_config(None, None), {})
        self.assertEqual(merge_config({"a": 1}, None), {"a": 1})
        self.assertEqual(merge_config(None, {"a": 1}), {"a": 1})

    def test_does_not_mutate_base(self):
        base = {"a": 1}
        merge_config(base, {"a": 2})
        self.assertEqual(base, {"a": 1})
