"""
Regression tests for Gemini client backend selection.

Guards the bug where the client was always built with ``vertexai=True`` and an
API key (Vertex AI Express mode), which is region-locked and 404s on models
absent from the express region (e.g. ``gemini-2.5-flash-lite`` in
``asia-southeast1``). The default backend must be the public Gemini Developer
API; Vertex is opt-in via ``GOOGLE_GENAI_USE_VERTEXAI``.

These tests mock the SDK client — no network calls.
"""

import unittest
from unittest import mock

from cins.processors.llm_services.gemini import GeminiService


class GeminiClientBackendTests(unittest.TestCase):
    def _build_client_with_env(self, env: dict):
        """Construct a client under a patched env and a mocked genai.Client.

        Returns the kwargs the SDK Client was constructed with.
        """
        fake_client_cls = mock.MagicMock()
        fake_genai = mock.MagicMock()
        fake_genai.Client = fake_client_cls

        service = GeminiService(api_key="test-key", model="gemini-2.5-flash-lite")

        # `from google import genai` resolves `google.genai`; patch that.
        with mock.patch.dict("os.environ", env, clear=False), mock.patch.dict(
            "sys.modules", {"google.genai": fake_genai, "google": mock.MagicMock(genai=fake_genai)}
        ):
            service._get_client()

        self.assertEqual(fake_client_cls.call_count, 1)
        return fake_client_cls.call_args.kwargs

    def test_default_uses_public_developer_api(self):
        """No env flag -> public Gemini API: api_key passed, vertexai NOT set."""
        kwargs = self._build_client_with_env({"GOOGLE_GENAI_USE_VERTEXAI": ""})
        self.assertEqual(kwargs.get("api_key"), "test-key")
        self.assertNotIn("vertexai", kwargs)

    def test_flag_opts_into_vertex(self):
        """GOOGLE_GENAI_USE_VERTEXAI=true -> Vertex: vertexai=True, api_key forwarded.

        A Vertex platform API key with vertexai=True reaches the global model
        endpoints, so the key IS forwarded (not stripped). Public-API mode is
        the no-vertexai branch, covered by test_default_uses_public_developer_api.
        """
        kwargs = self._build_client_with_env({"GOOGLE_GENAI_USE_VERTEXAI": "true"})
        self.assertTrue(kwargs.get("vertexai"))
        self.assertEqual(kwargs.get("api_key"), "test-key")

    def test_flag_accepts_truthy_variants(self):
        for val in ("1", "TRUE", "Yes", " true "):
            with self.subTest(val=val):
                kwargs = self._build_client_with_env({"GOOGLE_GENAI_USE_VERTEXAI": val})
                self.assertTrue(kwargs.get("vertexai"))


if __name__ == "__main__":
    unittest.main()
