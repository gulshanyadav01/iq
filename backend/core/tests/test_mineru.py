"""Shared MinerU client: retry classification, pre-check, and form fields.

Run from the backend container:
    python manage.py test core.tests.test_mineru -v 2
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from core.services import mineru
from core.services.mineru import (
    PageLimitExceeded,
    PermanentDocumentError,
    base_form,
    is_retryable,
    precheck_pdf,
)
from core.tests.pdf_fixtures import make_pdf


def _http_error(status: int) -> requests.HTTPError:
    response = MagicMock()
    response.status_code = status
    return requests.HTTPError(f"{status}", response=response)


class IsRetryableTests(SimpleTestCase):
    def test_transient_statuses_are_retryable(self):
        for status in (429, 502, 503, 504):
            with self.subTest(status=status):
                self.assertTrue(is_retryable(_http_error(status)))

    def test_permanent_statuses_are_not(self):
        for status in (400, 404, 500):
            with self.subTest(status=status):
                self.assertFalse(is_retryable(_http_error(status)))

    def test_network_errors_are_retryable(self):
        self.assertTrue(is_retryable(requests.Timeout("read timed out")))
        self.assertTrue(is_retryable(requests.ConnectionError("reset")))

    def test_http_error_without_response_is_not(self):
        self.assertFalse(is_retryable(requests.HTTPError("no response")))

    def test_wrapped_cause_is_followed(self):
        # pipelines wraps the requests error: MinervaError(...) from exc
        try:
            try:
                raise _http_error(502)
            except requests.HTTPError as inner:
                raise RuntimeError("Minerva request failed") from inner
        except RuntimeError as outer:
            self.assertTrue(is_retryable(outer))

    def test_unrelated_errors_are_not(self):
        self.assertFalse(is_retryable(ValueError("bad")))
        self.assertFalse(is_retryable(PermanentDocumentError("encrypted")))
        self.assertFalse(is_retryable(None))


class PrecheckPdfTests(SimpleTestCase):
    def test_valid_pdf_returns_page_count(self):
        self.assertEqual(precheck_pdf(make_pdf(3)), 3)

    def test_accepts_a_path(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(make_pdf(2))
        try:
            self.assertEqual(precheck_pdf(tmp.name, max_pages=50), 2)
        finally:
            os.unlink(tmp.name)

    def test_page_cap(self):
        with self.assertRaisesMessage(
            PageLimitExceeded, "PDF has 51 pages; limit is 50"
        ):
            precheck_pdf(make_pdf(51), max_pages=50)

    def test_size_cap(self):
        with self.assertRaisesMessage(PermanentDocumentError, "MB; limit is"):
            precheck_pdf(make_pdf(1), max_bytes=10)

    def test_missing_magic_header(self):
        with self.assertRaisesMessage(PermanentDocumentError, "not a valid PDF"):
            precheck_pdf(b"<html>error page</html>")

    def test_corrupt_pdf(self):
        with self.assertRaisesMessage(
            PermanentDocumentError, "encrypted or unreadable"
        ):
            precheck_pdf(b"%PDF-1.5\n%garbage, no xref or objects\n")

    def test_no_caps_by_default(self):
        self.assertEqual(precheck_pdf(make_pdf(60)), 60)


class BaseFormTests(SimpleTestCase):
    def test_omits_server_url(self):
        # The split MinerU workers supply server_url themselves; the old swagger
        # placeholder "string" would override it under hybrid-http-client.
        self.assertNotIn("server_url", base_form())

    def test_backend_comes_from_env_setting(self):
        with patch.object(mineru, "MINERU_BACKEND", "hybrid-http-client"):
            self.assertEqual(base_form()["backend"], "hybrid-http-client")

    def test_overrides(self):
        form = base_form(return_middle_json="true", start_page_id="50")
        self.assertEqual(form["return_middle_json"], "true")
        self.assertEqual(form["start_page_id"], "50")


class SessionTests(SimpleTestCase):
    def test_session_is_reused_within_a_process(self):
        self.assertIs(mineru.get_session(), mineru.get_session())
