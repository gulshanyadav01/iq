"""Master-data `id` resolution in the Gemini parser's post-processing.

Guards the fix for the bug where a lookup MISS left the LLM's raw (often
UPPERCASE) value sitting in the field's ``id`` instead of clearing it. The
resolver is already case-insensitive; these tests pin the ``_apply`` contract:
match → numeric id, miss → empty id + do_verification="TRUE".

The parser's ``__init__`` builds a Gemini client and runs a Vertex credential
preflight, so we bypass it with ``__new__`` and set only the attributes
``_post_process`` touches (``_resolver`` + the RTO maps).

Run from the backend container:
    python manage.py test policy_extract.tests.test_master_id_resolution -v 2
"""

from django.test import TestCase

from master_data.models import FuelTypeMaster
from master_data.services.resolver import MasterDataResolver
from policy_extract.services.mineru_gemini_parser import MineruGeminiParser


def _bucket(value, _id=""):
    return {
        "id": _id,
        "value": value,
        "confidence_score": 1.0,
        "is_extracted": "TRUE",
        "do_verification": "FALSE",
    }


class MasterDataIdResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Master row: "Petrol" → fuel_id 1. Keys are casefolded at load time.
        FuelTypeMaster.objects.create(name="Petrol", fuel_id=1)

    def setUp(self):
        # Bypass __init__ (Gemini client + Vertex preflight); wire only what
        # _post_process reads.
        parser = MineruGeminiParser.__new__(MineruGeminiParser)
        parser._resolver = MasterDataResolver()
        parser.rto_city_map = {}
        parser.rto_reverse_map = {}
        self.parser = parser

    def test_miss_clears_echoed_uppercase_id(self):
        # LLM echoed the raw UPPERCASE value into `id`; value won't resolve.
        data = {"fuel_type": _bucket("PETROLIUM", _id="PETROLIUM")}
        out = self.parser._post_process(data)
        self.assertEqual(out["fuel_type"]["id"], "")
        self.assertEqual(out["fuel_type"]["do_verification"], "TRUE")

    def test_match_fills_numeric_id_and_canonical(self):
        # UPPERCASE value still resolves (casefold match); id becomes the row id.
        data = {"fuel_type": _bucket("PETROL", _id="PETROL")}
        out = self.parser._post_process(data)
        self.assertEqual(out["fuel_type"]["id"], "1")
        self.assertEqual(out["fuel_type"]["value"], "Petrol")
        self.assertEqual(out["fuel_type"]["do_verification"], "FALSE")

    def test_empty_value_clears_id(self):
        # Nothing extracted → id must not retain a stray value.
        data = {"fuel_type": _bucket("", _id="GARBAGE")}
        out = self.parser._post_process(data)
        self.assertEqual(out["fuel_type"]["id"], "")
