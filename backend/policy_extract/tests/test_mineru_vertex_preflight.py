"""
Regression tests for MineruGeminiParser's Vertex-mode ADC preflight.

Mirrors the cins gemini.py fail-fast contract: when Vertex is enabled
(GOOGLE_GENAI_USE_VERTEXAI=true) the client authenticates via ADC with no API
key, and a misconfigured environment must raise at construction rather than
deferring an opaque auth failure to the first generate call.

These raise-paths run BEFORE genai.Client() and before the DB-backed
MasterDataResolver is touched, so they are unit-testable in isolation with only
an environment patch — no SDK or database needed.
"""

import unittest
from unittest import mock

from policy_extract.services.mineru_gemini_parser import MineruGeminiParser


class MineruVertexPreflightTests(unittest.TestCase):
    def test_vertex_missing_project_raises(self):
        """Vertex mode without GOOGLE_CLOUD_PROJECT fails fast at construction.

        Pins GOOGLE_APPLICATION_CREDENTIALS empty so an ambient value can't leak,
        and asserts the project branch (not the cred-file branch) is what fired.
        """
        with mock.patch.dict(
            "os.environ",
            {
                "GOOGLE_GENAI_USE_VERTEXAI": "true",
                "GOOGLE_CLOUD_PROJECT": "",
                "GOOGLE_APPLICATION_CREDENTIALS": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "GOOGLE_CLOUD_PROJECT"):
                MineruGeminiParser(model="gemini-2.5-flash-lite")

    def test_vertex_missing_cred_file_raises(self):
        """Vertex mode with GOOGLE_APPLICATION_CREDENTIALS pointing at a missing
        file fails fast at construction (offline, before any network call)."""
        with mock.patch.dict(
            "os.environ",
            {
                "GOOGLE_GENAI_USE_VERTEXAI": "true",
                "GOOGLE_CLOUD_PROJECT": "proj",
                "GOOGLE_CLOUD_LOCATION": "global",
                "GOOGLE_APPLICATION_CREDENTIALS": "/nonexistent/sa-key.json",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "missing file"):
                MineruGeminiParser(model="gemini-2.5-flash-lite")

    def test_vertex_bare_multiregion_location_raises(self):
        """Bare 'us'/'eu' multi-region location is rejected (would 404)."""
        with mock.patch.dict(
            "os.environ",
            {
                "GOOGLE_GENAI_USE_VERTEXAI": "true",
                "GOOGLE_CLOUD_PROJECT": "proj",
                "GOOGLE_CLOUD_LOCATION": "us",
                "GOOGLE_APPLICATION_CREDENTIALS": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "multi-region"):
                MineruGeminiParser(model="gemini-2.5-flash-lite")


if __name__ == "__main__":
    unittest.main()
