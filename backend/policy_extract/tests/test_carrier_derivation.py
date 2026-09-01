"""Carrier / permit-type derivation in the Gemini parser's post-processing.

Guards the fix for the bug where the goods/passenger permit printed on a policy
("Policy Type" / "Permit Type" = Public Carrier / Private Carrier / Private and
Public Carrier) was never read, so every commercial vehicle collapsed to the
``product == CV -> COMMERCIAL`` guess. The permit type now grounds the value from
the Stage-1 raw data and beats an ungrounded Stage-2 enum.

The parser's ``__init__`` builds a Gemini client and runs a Vertex credential
preflight, so we bypass it with ``__new__`` and wire only what ``_post_process``
touches (``_resolver`` + the RTO maps), mirroring test_master_id_resolution.

Run from the backend container:
    python manage.py test policy_extract.tests.test_carrier_derivation -v 2
"""

from django.test import TestCase

from master_data.models import CarrierMaster
from master_data.services.resolver import MasterDataResolver
from policy_extract.services.mineru_gemini_parser import MineruGeminiParser


def _bucket(value="", _id=""):
    return {
        "id": _id,
        "value": value,
        "confidence_score": 1.0,
        "is_extracted": "TRUE",
        "do_verification": "FALSE",
    }


class CarrierDerivationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # The three seeded carriers (ids as in production).
        CarrierMaster.objects.create(name="Public", carrier_id=1)
        CarrierMaster.objects.create(name="Private", carrier_id=2)
        CarrierMaster.objects.create(name="Commercial", carrier_id=3)

    def setUp(self):
        parser = MineruGeminiParser.__new__(MineruGeminiParser)
        parser._resolver = MasterDataResolver()
        parser.rto_city_map = {}
        parser.rto_reverse_map = {}
        self.parser = parser

    def _carrier(self, structured, raw=None):
        out = self.parser._post_process(structured, raw)
        return out["vechicle_carrier_id"]

    def test_public_carrier_from_raw_permit_type(self):
        # PCV whose document says "Public Carrier" — grounded value, resolves to Public/#1.
        data = {
            "vechicle_carrier_id": _bucket(),
            "VehicleClass": _bucket("PCV"),
            "product_id": _bucket("CV"),
        }
        raw = {"vehicles": [{"permit_type": "Public Carrier"}]}
        car = self._carrier(data, raw)
        self.assertEqual(car["value"], "Public")
        self.assertEqual(car["id"], "1")
        self.assertEqual(car["do_verification"], "FALSE")

    def test_private_carrier_from_raw_permit_type(self):
        data = {
            "vechicle_carrier_id": _bucket(),
            "VehicleClass": _bucket("Misc D"),
            "product_id": _bucket("CV"),
        }
        raw = {"vehicles": [{"permit_type": "Private Carrier"}]}
        car = self._carrier(data, raw)
        self.assertEqual(car["value"], "Private")
        self.assertEqual(car["id"], "2")

    def test_combined_permit_maps_to_public_private_and_flags_review(self):
        # "Private and Public Carrier" has no master row: keep the value, flag for review.
        data = {
            "vechicle_carrier_id": _bucket(),
            "VehicleClass": _bucket("Misc D"),
            "product_id": _bucket("CV"),
        }
        raw = {"vehicles": [{"permit_type": "Private and Public Carrier"}]}
        car = self._carrier(data, raw)
        self.assertEqual(car["value"], "PUBLIC_PRIVATE")
        self.assertEqual(car["id"], "")
        self.assertEqual(car["do_verification"], "TRUE")

    def test_grounded_permit_overrides_ungrounded_stage2_value(self):
        # Stage-2 hallucinated COMMERCIAL, but the document states a Public Carrier permit.
        data = {
            "vechicle_carrier_id": _bucket("COMMERCIAL"),
            "VehicleClass": _bucket("PCV"),
            "product_id": _bucket("CV"),
        }
        raw = {"vehicles": [{"permit_type": "Public Carrier"}]}
        car = self._carrier(data, raw)
        self.assertEqual(car["value"], "Public")

    def test_broker_private_limited_is_not_read_as_private_carrier(self):
        # A "... PRIVATE LIMITED" entity must NOT be mistaken for a Private carrier;
        # with no permit anywhere, a CV falls back to COMMERCIAL.
        data = {
            "vechicle_carrier_id": _bucket(),
            "VehicleClass": _bucket("GCV"),
            "product_id": _bucket("CV"),
        }
        raw = {
            "entities": [
                {
                    "value": "POLICYBAZAAR INSURANCE BROKERS PRIVATE LIMITED",
                    "context": "partner name",
                }
            ],
            "vehicles": [{"make": "TATA"}],
        }
        car = self._carrier(data, raw)
        self.assertEqual(car["value"], "Commercial")
        self.assertEqual(car["id"], "3")

    def test_fallback_when_document_is_silent(self):
        # No permit and no Stage-2 value: a CV still defaults to COMMERCIAL.
        data = {
            "vechicle_carrier_id": _bucket(),
            "VehicleClass": _bucket(""),
            "product_id": _bucket("CV"),
        }
        car = self._carrier(data, {})
        self.assertEqual(car["value"], "Commercial")

    def test_two_wheeler_defaults_to_private(self):
        # Two-wheelers carry no permit; Permute records them as Private, so we match.
        data = {
            "vechicle_carrier_id": _bucket(),
            "VehicleClass": _bucket("Two Wheeler"),
            "product_id": _bucket("TWO WHEELER"),
        }
        car = self._carrier(data, {})
        self.assertEqual(car["value"], "Private")
        self.assertEqual(car["id"], "2")

    def test_private_car_defaults_to_private(self):
        data = {
            "vechicle_carrier_id": _bucket(),
            "VehicleClass": _bucket("Private Car"),
            "product_id": _bucket("CAR"),
        }
        car = self._carrier(data, {})
        self.assertEqual(car["value"], "Private")
