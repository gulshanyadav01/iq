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
from policy_extract.services.content_type import _sniff, resolve_content_type


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
