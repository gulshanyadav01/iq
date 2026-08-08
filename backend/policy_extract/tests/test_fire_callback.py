"""Callback response persistence in `_fire_callback`.

After POSTing an extraction event to the configured callback URL, we persist the
receiver's reply (status code + body) onto ``PolicyExtractionRequest.callback_response``
so it can be inspected in the UI. These tests pin that contract for both the
success and the transport-failure paths.

Run from the backend container:
    python manage.py test policy_extract.tests.test_fire_callback -v 2
"""

import json
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase

from policy_extract.models import PolicyExtractionRequest
from policy_extract.tasks import _fire_callback


class FireCallbackTests(TestCase):
    def setUp(self):
        self.req = PolicyExtractionRequest.objects.create(
            original_filename="policy.pdf",
            status=PolicyExtractionRequest.Status.COMPLETED,
        )

    @patch("policy_extract.tasks.requests.post")
    def test_saves_json_body_on_success(self, mock_post):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"received": true, "id": "abc"}'
        resp.json.return_value = {"received": True, "id": "abc"}
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        _fire_callback("http://example.com/cb", self.req, "policy_extract.completed")

        self.req.refresh_from_db()
        cb = self.req.callback_response
        self.assertIsNotNone(cb)
        self.assertTrue(cb["ok"])
        self.assertEqual(cb["status_code"], 200)
        self.assertEqual(cb["body"], {"received": True, "id": "abc"})
        self.assertIsNone(cb["error"])
        self.assertIsNotNone(cb["received_at"])

    @patch("policy_extract.tasks.requests.post")
    def test_falls_back_to_text_body(self, mock_post):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("no json")
        resp.text = "OK"
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        _fire_callback("http://example.com/cb", self.req, "policy_extract.completed")

        self.req.refresh_from_db()
        self.assertEqual(self.req.callback_response["body"], "OK")
        self.assertTrue(self.req.callback_response["ok"])

    @patch("policy_extract.tasks.requests.post")
    def test_records_error_when_post_raises(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("connection refused")

        _fire_callback("http://example.com/cb", self.req, "policy_extract.failed")

        self.req.refresh_from_db()
        cb = self.req.callback_response
        self.assertIsNotNone(cb)
        self.assertFalse(cb["ok"])
        self.assertIsNone(cb["status_code"])
        self.assertIn("connection refused", cb["error"])

    @patch("policy_extract.tasks.requests.post")
    def test_records_status_and_body_on_http_error(self, mock_post):
        # Receiver replied, but with a non-2xx status: keep status_code + body,
        # record the error, and leave ok False.
        resp = MagicMock()
        resp.status_code = 500
        resp.text = '{"detail": "boom"}'
        resp.json.return_value = {"detail": "boom"}
        resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        mock_post.return_value = resp

        _fire_callback("http://example.com/cb", self.req, "policy_extract.completed")

        self.req.refresh_from_db()
        cb = self.req.callback_response
        self.assertIsNotNone(cb)
        self.assertFalse(cb["ok"])
        self.assertEqual(cb["status_code"], 500)
        self.assertEqual(cb["body"], {"detail": "boom"})
        self.assertIn("500 Server Error", cb["error"])

    @patch("policy_extract.tasks.requests.post")
    def test_truncates_oversized_body(self, mock_post):
        # A hostile/oversized receiver response must be capped before it is stored
        # (DB bloat) or rendered in the UI (browser freeze).
        big = "x" * (100 * 1024)
        resp = MagicMock()
        resp.status_code = 200
        resp.text = big
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        _fire_callback("http://example.com/cb", self.req, "policy_extract.completed")

        self.req.refresh_from_db()
        cb = self.req.callback_response
        self.assertTrue(cb["ok"])
        self.assertTrue(cb["body_truncated"])
        self.assertLess(len(cb["body"]), len(big))
        self.assertTrue(cb["body"].startswith("x"))
        # resp.json() must NOT be consulted once we've decided to truncate.
        resp.json.assert_not_called()

    @patch("policy_extract.tasks.requests.post")
    def test_neutralizes_non_finite_json(self, mock_post):
        # NaN/Infinity from the receiver would make Postgres jsonb reject the row
        # and silently drop the whole callback_response. They must be neutralized.
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"score": NaN, "ok": true}'
        resp.json.side_effect = lambda **kw: json.loads(resp.text, **kw)
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        _fire_callback("http://example.com/cb", self.req, "policy_extract.completed")

        self.req.refresh_from_db()
        cb = self.req.callback_response
        self.assertTrue(cb["ok"])
        self.assertEqual(cb["body"], {"score": None, "ok": True})
