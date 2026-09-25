from django.test import SimpleTestCase

from policy_extract.services.content_type import storage_error_message
from policy_extract.services.error_classification import classify_error


class ClassifyErrorTests(SimpleTestCase):
    def test_expired_link_from_storage_helper(self):
        # Use the real message the fetch path raises for an expired presigned link.
        msg = storage_error_message(b'{"statusMsg": "expired", "statusCode": 24}')
        self.assertTrue(msg)  # sanity: the helper produced an envelope message
        self.assertEqual(classify_error(msg), "expired_link")

    def test_unsupported_file_task_message(self):
        msg = "Unsupported file type for 'policy.txt'. Only PDF files can be extracted."
        self.assertEqual(classify_error(msg), "unsupported_file")

    def test_unsupported_file_serializer_message(self):
        msg = "The uploaded file is not a valid PDF. Only .pdf files are supported."
        self.assertEqual(classify_error(msg), "unsupported_file")

    def test_missing_source(self):
        self.assertEqual(
            classify_error("No source_url and no file_link in metadata"),
            "missing_source",
        )

    def test_timeout(self):
        self.assertEqual(
            classify_error("SoftTimeLimitExceeded()"),
            "timeout",
        )

    def test_download_failed(self):
        self.assertEqual(
            classify_error(
                "HTTPSConnectionPool(host='x'): Max retries exceeded (ConnectionError)"
            ),
            "download_failed",
        )

    def test_parser_failed(self):
        self.assertEqual(
            classify_error("MinerU parse failed: 500 from Gemini"),
            "parser_failed",
        )

    def test_blank_and_none_are_other(self):
        self.assertEqual(classify_error(""), "other")
        self.assertEqual(classify_error(None), "other")

    def test_unrecognised_is_other(self):
        self.assertEqual(classify_error("something completely unexpected"), "other")

    def test_returns_valid_model_choice(self):
        # Every category the classifier can emit must be a real model choice.
        from policy_extract.models import PolicyExtractionRequest

        valid = set(PolicyExtractionRequest.ErrorCategory.values)
        for msg in [
            "link is no longer valid",
            "Unsupported file type",
            "No source_url and no file_link",
            "time limit exceeded",
            "ConnectionError",
            "mineru failed",
            "",
        ]:
            self.assertIn(classify_error(msg), valid)
