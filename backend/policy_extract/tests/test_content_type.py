"""Unit tests for MIME resolution of stored policy documents.

`resolve_content_type` is the single source of truth the `document` stream action
and the `content_type` serializer field both use to decide a document's type — the
fix for extensionless `file_link` uploads (e.g. ``PBP10356218``) that would
otherwise mis-serve as ``application/octet-stream`` and never preview inline.

Pure logic, no storage/DB: instances are unsaved and `head` bytes are passed
directly, so these run under SimpleTestCase.

Run from the backend container:
    python manage.py test policy_extract.tests.test_content_type -v 2
"""

from django.test import SimpleTestCase

from policy_extract.models import PolicyExtractionRequest
from policy_extract.serializers import PolicyExtractionRequestSerializer
from policy_extract.services.content_type import (
    _sniff,
    is_extractable,
    resolve_content_type,
    storage_error_message,
)


def _req(original_filename="doc", metadata=None):
    return PolicyExtractionRequest(
        original_filename=original_filename, metadata=metadata
    )


class SniffTests(SimpleTestCase):
    def test_pdf_signature(self):
        self.assertEqual(_sniff(b"%PDF-1.7 ..."), "application/pdf")

    def test_png_signature(self):
        self.assertEqual(_sniff(b"\x89PNG\r\n\x1a\nrest"), "image/png")

    def test_jpeg_signature(self):
        self.assertEqual(_sniff(b"\xff\xd8\xff\xe0\x00\x10JFIF"), "image/jpeg")

    def test_gif87_and_gif89_signatures(self):
        self.assertEqual(_sniff(b"GIF87a...."), "image/gif")
        self.assertEqual(_sniff(b"GIF89a...."), "image/gif")

    def test_webp_signature(self):
        # RIFF....WEBP — bytes 0-3 "RIFF", bytes 8-11 "WEBP".
        self.assertEqual(_sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "image/webp")

    def test_unknown_bytes_return_none(self):
        self.assertIsNone(_sniff(b"just some text"))

    def test_empty_head_returns_none(self):
        self.assertIsNone(_sniff(b""))


class IsExtractableTests(SimpleTestCase):
    """The PDF-only gate shared by the upload serializer and the parse task."""

    def test_pdf_name_and_pdf_bytes_ok(self):
        self.assertTrue(is_extractable("policy.pdf", b"%PDF-1.7 ..."))

    def test_pdf_name_but_non_pdf_bytes_rejected(self):
        # Magic bytes are authoritative — a file renamed to .pdf is caught.
        self.assertFalse(is_extractable("policy.pdf", b"not a pdf at all"))

    def test_disallowed_extension_rejected(self):
        self.assertFalse(is_extractable("data.jsonl", b'{"a": 1}\n'))

    def test_disallowed_extension_rejected_even_with_pdf_bytes(self):
        # A present, non-allowed extension is disqualifying regardless of content.
        self.assertFalse(is_extractable("data.jsonl", b"%PDF-1.7"))

    def test_extensionless_file_link_pdf_ok(self):
        # The file_link route stores the bare policy id (no extension); a valid
        # PDF fetched by link must still pass on its content.
        self.assertTrue(is_extractable("PBP10356218", b"%PDF-1.4"))

    def test_extensionless_non_pdf_rejected(self):
        # e.g. an HTML error page returned by a bad file_link.
        self.assertFalse(is_extractable("PBP10356218", b"<!DOCTYPE html>"))

    def test_empty_file_rejected(self):
        self.assertFalse(is_extractable("policy.pdf", b""))

    def test_name_only_pdf_extension_ok(self):
        # No bytes to sniff: a declared .pdf extension is accepted.
        self.assertTrue(is_extractable("policy.pdf"))

    def test_name_only_extensionless_rejected(self):
        # No bytes and no supported extension → cannot confirm → reject.
        self.assertFalse(is_extractable("PBP10356218"))


class StorageErrorMessageTests(SimpleTestCase):
    """Detecting the storage service's error envelope (an expired presigned link)."""

    _EXPIRED = (
        b'{"statusCode":24,"statusMsg":"The validity of the URL you are '
        b'requesting has expired."}'
    )

    def test_expired_envelope_returns_generic_message(self):
        msg = storage_error_message(self._EXPIRED)
        self.assertTrue(msg)
        self.assertIn("expired", msg.lower())
        # The untrusted remote statusMsg is NOT reflected into the message
        # (it reaches the API client and the logs).
        self.assertNotIn("The validity of the URL", msg)

    def test_complete_envelope_under_cap_detected(self):
        # A complete envelope whose full JSON fits within the 2048-byte cap is
        # detected (real error envelopes are ~90 bytes, well under the cap).
        body = b'{"statusMsg":"expired","pad":"' + b"y" * 1980 + b'"}'
        self.assertLess(len(body), 2048)
        self.assertTrue(storage_error_message(body))

    def test_body_larger_than_cap_not_detected(self):
        # Best-effort by design: a body whose JSON does not fully fit within the
        # 2048-byte cap fails json.loads on the truncated slice and returns "".
        body = b'{"statusMsg":"expired","pad":"' + b"x" * 4000 + b'"}'
        self.assertEqual(storage_error_message(body), "")

    def test_deeply_nested_body_does_not_raise(self):
        # A hostile deeply nested body must never propagate (RecursionError etc.);
        # the helper degrades to "" rather than raising.
        body = b"[" * 4000 + b"]" * 4000
        self.assertEqual(storage_error_message(body), "")

    def test_status_code_only_envelope_still_detected(self):
        # No statusMsg, but a statusCode present → still an error envelope.
        self.assertTrue(storage_error_message(b'{"statusCode":403}'))

    def test_message_key_envelope_detected(self):
        self.assertTrue(storage_error_message(b'{"message":"access denied"}'))

    def test_json_content_type_header_triggers_detection(self):
        # Body doesn't start with '{' (leading whitespace) but header says JSON.
        body = b'  {"statusMsg":"expired"}'
        self.assertTrue(storage_error_message(body, "application/json"))

    def test_pdf_bytes_are_never_an_envelope(self):
        self.assertEqual(storage_error_message(b"%PDF-1.4 body"), "")

    def test_plain_json_without_error_keys_is_not_an_envelope(self):
        # A benign JSON body lacking statusMsg/statusCode/message is not an error.
        self.assertEqual(storage_error_message(b'{"data": 1}'), "")

    def test_non_json_bytes_ignored(self):
        self.assertEqual(storage_error_message(b"<!DOCTYPE html>"), "")

    def test_empty_bytes_ignored(self):
        self.assertEqual(storage_error_message(b""), "")

    def test_malformed_json_ignored(self):
        self.assertEqual(storage_error_message(b'{"statusMsg": '), "")

    def test_json_array_is_not_an_envelope(self):
        # Looks like JSON, parses, but isn't an object carrying the error keys.
        self.assertEqual(storage_error_message(b'["statusMsg"]'), "")


class ResolveContentTypeTests(SimpleTestCase):
    def test_extension_wins_over_everything(self):
        # A real extension is the strongest signal — used before metadata/sniff.
        req = _req("policy.pdf", metadata={"file_type": "image/png"})
        self.assertEqual(resolve_content_type(req, head=b"GIF89a"), "application/pdf")

    def test_metadata_file_type_hint_when_no_extension(self):
        req = _req("PBP10356218", metadata={"file_type": "application/pdf"})
        self.assertEqual(resolve_content_type(req), "application/pdf")

    def test_metadata_present_but_no_file_type_falls_through_to_sniff(self):
        # metadata dict exists but lacks file_type → the block is entered, no hint
        # found, so resolution continues to the head sniff.
        req = _req("PBP10356218", metadata={"other": "x"})
        self.assertEqual(resolve_content_type(req, head=b"%PDF-1.4"), "application/pdf")

    def test_sniff_used_when_no_extension_and_no_metadata(self):
        req = _req("PBP10356432", metadata=None)
        self.assertEqual(
            resolve_content_type(req, head=b"\x89PNG\r\n\x1a\n"), "image/png"
        )

    def test_octet_stream_when_nothing_resolves(self):
        req = _req("PBP10356432", metadata=None)
        self.assertEqual(
            resolve_content_type(req, head=b"garbage"), "application/octet-stream"
        )

    def test_no_head_leaves_octet_stream_for_extensionless_no_metadata(self):
        # The serializer path passes head=None; without any other signal an
        # extensionless file resolves to octet-stream (no sniff possible).
        req = _req("PBP10356432", metadata=None)
        self.assertEqual(resolve_content_type(req), "application/octet-stream")


class SerializerContentTypeFieldTests(SimpleTestCase):
    """The frontend picks its preview renderer from serializer `content_type`."""

    def test_extensionless_with_metadata_hint_serializes_pdf(self):
        req = _req("PBP10356218", metadata={"file_type": "application/pdf"})
        data = PolicyExtractionRequestSerializer(req).data
        self.assertEqual(data["content_type"], "application/pdf")

    def test_extension_serializes_from_filename(self):
        req = _req("policy.pdf", metadata=None)
        data = PolicyExtractionRequestSerializer(req).data
        self.assertEqual(data["content_type"], "application/pdf")

    def test_extensionless_no_hint_serializes_octet_stream(self):
        # Serializer resolves name+metadata only (no storage IO / head sniff).
        req = _req("PBP10356218", metadata=None)
        data = PolicyExtractionRequestSerializer(req).data
        self.assertEqual(data["content_type"], "application/octet-stream")
