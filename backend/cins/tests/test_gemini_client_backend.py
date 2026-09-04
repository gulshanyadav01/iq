"""
Regression tests for Gemini client backend selection.

Guards two contracts:

1. Backend selection. The default backend must be the public Gemini Developer
   API (api_key forwarded, no ``vertexai``); Vertex is opt-in via
   ``GOOGLE_GENAI_USE_VERTEXAI``.
2. Vertex auth via ADC. On the Vertex path the SDK authenticates through
   Application Default Credentials — no API key is forwarded, and the service
   constructs without one. A misconfigured Vertex env (no ``GOOGLE_CLOUD_PROJECT``)
   fails fast at client-init with ``GeminiServiceError`` rather than deferring an
   opaque auth failure into the generate_content retry loop.

3. The same contract in ``pipelines.steps.llm_extract``, which builds its own
   client. It used to pass ``vertexai=use_vertex`` and ``api_key=`` together,
   which on the Vertex path is Vertex AI *Express* mode — region-locked, and it
   404s on models not provisioned in the express region.

These tests mock the SDK client — no network calls.
"""

import tempfile
import unittest
from unittest import mock

from cins.processors.llm_services.gemini import GeminiService, GeminiServiceError


class GeminiClientBackendTests(unittest.TestCase):
    def _build_client_with_env(self, env: dict, api_key="test-key"):
        """Construct a client under a patched env and a mocked genai.Client.

        ``__init__`` reads GOOGLE_GENAI_USE_VERTEXAI at construction time (to
        decide whether an API key is required), so the env is patched around
        BOTH construction and ``_get_client()``.

        Returns the kwargs the SDK Client was constructed with.
        """
        fake_client_cls = mock.MagicMock()
        fake_genai = mock.MagicMock()
        fake_genai.Client = fake_client_cls

        # Pin GOOGLE_APPLICATION_CREDENTIALS empty so an ambient value (e.g. the
        # docker-compose default /secrets/gcp/sa-key.json) can't leak into the
        # preflight file check. Default GOOGLE_CLOUD_LOCATION to a valid value so
        # the Vertex region guard passes; tests override either explicitly.
        env = {
            "GOOGLE_APPLICATION_CREDENTIALS": "",
            "GOOGLE_CLOUD_LOCATION": "global",
            **env,
        }

        with (
            mock.patch.dict("os.environ", env, clear=False),
            mock.patch.dict(
                "sys.modules",
                {
                    "google.genai": fake_genai,
                    "google": mock.MagicMock(genai=fake_genai),
                },
            ),
        ):
            service = GeminiService(api_key=api_key, model="gemini-2.5-flash-lite")
            service._get_client()

        self.assertEqual(fake_client_cls.call_count, 1)
        return fake_client_cls.call_args.kwargs

    def test_default_uses_public_developer_api(self):
        """No env flag -> public Gemini API: api_key passed, vertexai NOT set."""
        kwargs = self._build_client_with_env({"GOOGLE_GENAI_USE_VERTEXAI": ""})
        self.assertEqual(kwargs.get("api_key"), "test-key")
        self.assertNotIn("vertexai", kwargs)

    def test_flag_opts_into_vertex(self):
        """GOOGLE_GENAI_USE_VERTEXAI=true -> Vertex: vertexai=True, NO api_key.

        Vertex authenticates via ADC, so the key is NOT forwarded even when one
        was provided to the constructor. Public-API mode (key forwarded) is the
        no-vertexai branch, covered by test_default_uses_public_developer_api.
        """
        kwargs = self._build_client_with_env(
            {"GOOGLE_GENAI_USE_VERTEXAI": "true", "GOOGLE_CLOUD_PROJECT": "proj"}
        )
        self.assertTrue(kwargs.get("vertexai"))
        self.assertNotIn("api_key", kwargs)

    def test_flag_accepts_truthy_variants(self):
        for val in ("1", "TRUE", "Yes", " true "):
            with self.subTest(val=val):
                kwargs = self._build_client_with_env(
                    {"GOOGLE_GENAI_USE_VERTEXAI": val, "GOOGLE_CLOUD_PROJECT": "proj"}
                )
                self.assertTrue(kwargs.get("vertexai"))

    def test_vertex_requires_no_api_key(self):
        """Vertex mode constructs with NO api_key (ADC) and builds a keyless client."""
        fake_client_cls = mock.MagicMock()
        fake_genai = mock.MagicMock()
        fake_genai.Client = fake_client_cls

        with (
            mock.patch.dict(
                "os.environ",
                {
                    "GOOGLE_GENAI_USE_VERTEXAI": "true",
                    "GOOGLE_CLOUD_PROJECT": "proj",
                    "GOOGLE_CLOUD_LOCATION": "global",
                    "GOOGLE_APPLICATION_CREDENTIALS": "",
                },
                clear=False,
            ),
            mock.patch.dict(
                "sys.modules",
                {
                    "google.genai": fake_genai,
                    "google": mock.MagicMock(genai=fake_genai),
                },
            ),
        ):
            # Must NOT raise GeminiAPIKeyMissingError despite no key anywhere.
            service = GeminiService(model="gemini-2.5-flash-lite")
            self.assertIsNone(service._api_key)
            service._get_client()

        kwargs = fake_client_cls.call_args.kwargs
        self.assertTrue(kwargs.get("vertexai"))
        self.assertNotIn("api_key", kwargs)

    def test_vertex_missing_project_raises(self):
        """Vertex mode without GOOGLE_CLOUD_PROJECT fails fast at client-init."""
        fake_client_cls = mock.MagicMock()
        fake_genai = mock.MagicMock()
        fake_genai.Client = fake_client_cls

        with (
            mock.patch.dict(
                "os.environ",
                {"GOOGLE_GENAI_USE_VERTEXAI": "true", "GOOGLE_CLOUD_PROJECT": ""},
                clear=False,
            ),
            mock.patch.dict(
                "sys.modules",
                {
                    "google.genai": fake_genai,
                    "google": mock.MagicMock(genai=fake_genai),
                },
            ),
        ):
            service = GeminiService(model="gemini-2.5-flash-lite")
            with self.assertRaisesRegex(GeminiServiceError, "GOOGLE_CLOUD_PROJECT"):
                service._get_client()

        fake_client_cls.assert_not_called()

    def test_vertex_missing_cred_file_raises(self):
        """Vertex mode with GOOGLE_APPLICATION_CREDENTIALS pointing at a missing file
        fails fast at client-init (offline, before any network call)."""
        fake_client_cls = mock.MagicMock()
        fake_genai = mock.MagicMock()
        fake_genai.Client = fake_client_cls

        with (
            mock.patch.dict(
                "os.environ",
                {
                    "GOOGLE_GENAI_USE_VERTEXAI": "true",
                    "GOOGLE_CLOUD_PROJECT": "proj",
                    "GOOGLE_CLOUD_LOCATION": "global",
                    "GOOGLE_APPLICATION_CREDENTIALS": "/nonexistent/sa-key.json",
                },
                clear=False,
            ),
            mock.patch.dict(
                "sys.modules",
                {
                    "google.genai": fake_genai,
                    "google": mock.MagicMock(genai=fake_genai),
                },
            ),
        ):
            service = GeminiService(model="gemini-2.5-flash-lite")
            with self.assertRaisesRegex(GeminiServiceError, "missing file"):
                service._get_client()

        fake_client_cls.assert_not_called()

    def test_vertex_existing_cred_file_builds_client(self):
        """Vertex mode with a cred file that exists passes the preflight and builds a
        keyless client (the file-present success branch)."""
        fake_client_cls = mock.MagicMock()
        fake_genai = mock.MagicMock()
        fake_genai.Client = fake_client_cls

        with tempfile.NamedTemporaryFile(suffix=".json") as cred_file:
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "GOOGLE_GENAI_USE_VERTEXAI": "true",
                        "GOOGLE_CLOUD_PROJECT": "proj",
                        "GOOGLE_CLOUD_LOCATION": "global",
                        "GOOGLE_APPLICATION_CREDENTIALS": cred_file.name,
                    },
                    clear=False,
                ),
                mock.patch.dict(
                    "sys.modules",
                    {
                        "google.genai": fake_genai,
                        "google": mock.MagicMock(genai=fake_genai),
                    },
                ),
            ):
                service = GeminiService(model="gemini-2.5-flash-lite")
                service._get_client()

        kwargs = fake_client_cls.call_args.kwargs
        self.assertTrue(kwargs.get("vertexai"))
        self.assertNotIn("api_key", kwargs)

    def test_vertex_missing_location_raises(self):
        """Vertex mode without GOOGLE_CLOUD_LOCATION fails fast at client-init."""
        fake_client_cls = mock.MagicMock()
        fake_genai = mock.MagicMock()
        fake_genai.Client = fake_client_cls

        with (
            mock.patch.dict(
                "os.environ",
                {
                    "GOOGLE_GENAI_USE_VERTEXAI": "true",
                    "GOOGLE_CLOUD_PROJECT": "proj",
                    "GOOGLE_CLOUD_LOCATION": "",
                },
                clear=False,
            ),
            mock.patch.dict(
                "sys.modules",
                {
                    "google.genai": fake_genai,
                    "google": mock.MagicMock(genai=fake_genai),
                },
            ),
        ):
            service = GeminiService(model="gemini-2.5-flash-lite")
            with self.assertRaisesRegex(GeminiServiceError, "GOOGLE_CLOUD_LOCATION"):
                service._get_client()

        fake_client_cls.assert_not_called()

    def test_vertex_bare_multiregion_location_raises(self):
        """Bare 'us'/'eu' multi-region values 404 with this client and are rejected."""
        for loc in ("us", "eu", "US", "Eu"):
            with self.subTest(loc=loc):
                fake_client_cls = mock.MagicMock()
                fake_genai = mock.MagicMock()
                fake_genai.Client = fake_client_cls

                with (
                    mock.patch.dict(
                        "os.environ",
                        {
                            "GOOGLE_GENAI_USE_VERTEXAI": "true",
                            "GOOGLE_CLOUD_PROJECT": "proj",
                            "GOOGLE_CLOUD_LOCATION": loc,
                        },
                        clear=False,
                    ),
                    mock.patch.dict(
                        "sys.modules",
                        {
                            "google.genai": fake_genai,
                            "google": mock.MagicMock(genai=fake_genai),
                        },
                    ),
                ):
                    service = GeminiService(model="gemini-2.5-flash-lite")
                    with self.assertRaisesRegex(GeminiServiceError, "multi-region"):
                        service._get_client()

                fake_client_cls.assert_not_called()


class PipelineStepClientBackendTests(unittest.TestCase):
    """``pipelines.steps.llm_extract`` builds its own client — same contract.

    An API key must never be forwarded alongside ``vertexai=True``: that is
    Vertex AI Express mode, not the ADC path this repo means by "Vertex".
    """

    def _build_client_with_env(self, env: dict):
        from pipelines.steps.llm_extract import LlmExtractExecutor

        fake_client_cls = mock.MagicMock()
        fake_genai = mock.MagicMock()
        fake_genai.Client = fake_client_cls
        fake_genai.Client.return_value.models.generate_content.side_effect = (
            RuntimeError("stop")
        )

        with (
            mock.patch.dict("os.environ", env, clear=False),
            mock.patch.dict(
                "sys.modules",
                {
                    "google.genai": fake_genai,
                    "google.genai.errors": mock.MagicMock(),
                    "google.genai.types": mock.MagicMock(),
                    "google": mock.MagicMock(genai=fake_genai),
                },
            ),
        ):
            step = LlmExtractExecutor.__new__(LlmExtractExecutor)
            with self.assertRaises(Exception):
                step._call_gemini("gemini-3.1-flash-lite", "hi")

        self.assertGreaterEqual(fake_client_cls.call_count, 1)
        return fake_client_cls.call_args.kwargs

    def test_default_uses_public_developer_api(self):
        kwargs = self._build_client_with_env(
            {"GOOGLE_GENAI_USE_VERTEXAI": "", "GOOGLE_API_KEY": "test-key"}
        )
        self.assertEqual(kwargs.get("api_key"), "test-key")
        self.assertNotIn("vertexai", kwargs)

    def test_vertex_never_forwards_an_api_key(self):
        kwargs = self._build_client_with_env(
            {"GOOGLE_GENAI_USE_VERTEXAI": "true", "GOOGLE_API_KEY": "test-key"}
        )
        self.assertTrue(kwargs.get("vertexai"))
        self.assertNotIn("api_key", kwargs)


if __name__ == "__main__":
    unittest.main()
