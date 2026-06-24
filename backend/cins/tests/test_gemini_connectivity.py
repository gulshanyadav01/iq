"""
Live connectivity test for the Gemini API key.

Verifies that the configured GEMINI_API_KEY can actually reach the Gemini
backend and produce a response from `gemini-2.5-flash-lite`. This is a real
network call, not a mock — it exists to catch dead/expired keys, wrong
backend (Vertex vs. AI Studio) wiring, and revoked model access before they
surface deep inside the extraction pipeline.

Opt-in only: it is skipped unless `RUN_LIVE_GEMINI_TESTS=1` is set. A live
probe must not gate the default suite — it makes a real network call and its
result depends on whether the configured credentials can reach the model in
their region/project (e.g. a Vertex express key is region-locked and may not
serve `gemini-2.5-flash-lite`). To run it against real credentials:

    RUN_LIVE_GEMINI_TESTS=1 python manage.py test cins.tests.test_gemini_connectivity -v 2
"""

import os
import unittest

from cins.processors.llm_services.factory import _load_env
from cins.processors.llm_services.gemini import (
    GeminiConfig,
    GeminiService,
)

TEST_MODEL = "gemini-2.5-flash-lite"


def _live_tests_enabled() -> bool:
    """Live Gemini tests run only when explicitly opted in AND a key is present."""
    if os.environ.get("RUN_LIVE_GEMINI_TESTS") != "1":
        return False
    _load_env()
    return True


@unittest.skipUnless(
    _live_tests_enabled(),
    "Live Gemini test skipped; set RUN_LIVE_GEMINI_TESTS=1 (and GEMINI_API_KEY) to run.",
)
class GeminiConnectivityTests(unittest.TestCase):
    """Real round-trip calls against gemini-2.5-flash-lite."""

    def setUp(self):
        # One retry only — this is a reachability probe, not a resilience test,
        # and we don't want a dead key to stall the suite on exponential backoff.
        self.service = GeminiService(
            model=TEST_MODEL,
            max_retries=1,
            retry_delay=0,
        )

    def test_api_key_reaches_model(self):
        """A trivial prompt returns a non-empty response from the live API."""
        config = GeminiConfig(
            response_mime_type="text/plain",
            max_output_tokens=16,
        )
        response = self.service.generate_content(
            "Reply with the single word: pong",
            config=config,
        )

        self.assertIsInstance(response, str)
        self.assertTrue(
            response.strip(),
            "Gemini returned an empty response; key/model reachable but produced no text.",
        )

    def test_structured_json_response(self):
        """The key has access to JSON-schema constrained generation on the model."""
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        }
        config = GeminiConfig(
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=64,
        )
        response = self.service.generate_content(
            'Return JSON {"ok": true}.',
            config=config,
        )

        import json

        parsed = json.loads(response)
        self.assertIn("ok", parsed)


if __name__ == "__main__":
    unittest.main()
