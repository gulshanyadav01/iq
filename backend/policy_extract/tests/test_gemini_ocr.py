"""PDF → Markdown via the Gemini OCR engine (POLICY_EXTRACT_OCR_ENGINE=gemini).

When the OCR engine is `gemini`, `_convert_pdf_to_markdown` sends the PDF bytes
inline to the configured Gemini model instead of posting to the MinerU server, and
returns the transcribed markdown. The parser's __init__ builds a Gemini client and a
master-data resolver, so we bypass it with __new__ and wire only what the OCR method
touches (mirroring test_carrier_derivation / test_master_id_resolution).

Run from the backend container:
    python manage.py test policy_extract.tests.test_gemini_ocr -v 2
"""

import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from policy_extract.services.mineru_gemini_parser import MineruGeminiParser


def _parser(engine, response_text):
    p = MineruGeminiParser.__new__(MineruGeminiParser)
    p._ocr_engine = engine
    p.model_name = "gemini-2.5-flash-lite"
    p._gen_config = None  # the mocked client ignores it
    p.total_input_tokens = 0
    p.total_output_tokens = 0
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(
        text=response_text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=1200, candidates_token_count=800
        ),
    )
    p._client = client
    return p, client


class GeminiOcrTests(SimpleTestCase):
    def test_gemini_engine_transcribes_pdf_bytes(self):
        p, client = _parser("gemini", "# Policy\n\nPolicy No: X123\n")
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(b"%PDF-1.4 fake bytes")
            f.flush()
            md = p._convert_pdf_to_markdown(f.name, page_count=3)

        self.assertIn("Policy No: X123", md)
        # The PDF reached Gemini (not the MinerU server).
        client.models.generate_content.assert_called_once()
        _, kwargs = client.models.generate_content.call_args
        contents = kwargs["contents"]
        # First content part carries the application/pdf bytes.
        part = contents[0]
        self.assertEqual(
            getattr(part.inline_data, "mime_type", None), "application/pdf"
        )
        # Token usage is accumulated for cost tracking.
        self.assertEqual(p.total_input_tokens, 1200)
        self.assertEqual(p.total_output_tokens, 800)

    def test_gemini_engine_strips_wrapping_code_fence(self):
        p, _ = _parser("gemini", "```markdown\n# Policy\n```")
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(b"%PDF-1.4")
            f.flush()
            md = p._convert_pdf_to_markdown_gemini(f.name)
        self.assertNotIn("```", md)
        self.assertIn("# Policy", md)
