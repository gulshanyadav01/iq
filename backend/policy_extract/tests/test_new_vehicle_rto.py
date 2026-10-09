"""RTO-code derivation for NEW vehicles in the Gemini parser's post-processing.

A NEW vehicle carries no registration number, so the usual "slice the code off
the reg number" path yields nothing and the RTO code came back blank even when
the document named the RTO city/state (e.g. "Nagpur"/"Maharashtra" → MH31).
These pin the city/state → code reverse lookup, which resolves against the
DB-backed ``RTOMaster`` (the same table the export's RTO cell is filled from).

The parser's ``__init__`` builds a Gemini client and runs a Vertex credential
preflight, so we bypass it with ``__new__`` and set only the attributes
``_post_process`` touches.

Run from the backend container:
    python manage.py test policy_extract.tests.test_new_vehicle_rto -v 2
"""

from django.test import TestCase

from master_data.models import RTOMaster
from master_data.services.resolver import MasterDataResolver
from policy_extract.services.mineru_gemini_parser import MineruGeminiParser


class NewVehicleRtoCodeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Nagpur: one primary office (MH31) plus "Nagpur East" (MH49), whose
        # longer locality must NOT match the bare city "NAGPUR".
        RTOMaster.objects.create(
            rto_id=1,
            code="MH31",
            value="MH31-Nagpur(Maharashtra)(Group A)",
            state_slug="maharashtra",
        )
        RTOMaster.objects.create(
            rto_id=2,
            code="MH49",
            value="MH49-Nagpur East(Maharashtra)(Group A)",
            state_slug="maharashtra",
        )
        # Pithoragarh is three rows: UA05/UK05 (Uttarakhand) and UP03 (Uttar
        # Pradesh). The Uttarakhand lookup must pick the canonical prefix UK05,
        # not the alphabetically-lower UA05, and must not leak the UP03 row.
        RTOMaster.objects.create(
            rto_id=3,
            code="UA05",
            value="UA05-Pithoragarh(Uttarakhand)(Group A)",
            state_slug="uttarakhand",
        )
        RTOMaster.objects.create(
            rto_id=4,
            code="UK05",
            value="UK05-Pithoragarh(Uttarakhand)(Group A)",
            state_slug="uttarakhand",
        )
        RTOMaster.objects.create(
            rto_id=5,
            code="UP03",
            value="UP03-Pithoragarh(Uttar Pradesh)(Group B)",
            state_slug="uttar-pradesh",
        )

    def setUp(self):
        parser = MineruGeminiParser.__new__(MineruGeminiParser)
        parser._resolver = MasterDataResolver()
        parser.rto_city_map = {}
        parser.rto_reverse_map = {}
        self.parser = parser

    # --- the direct reverse lookup -------------------------------------------

    def test_city_state_resolves_to_code(self):
        self.assertEqual(
            self.parser._rto_code_from_city_state("NAGPUR", "MAHARASHTRA"), "MH31"
        )

    def test_ambiguous_city_prefers_canonical_prefix(self):
        # UA05 sorts before UK05 but UK is the canonical Uttarakhand prefix.
        self.assertEqual(
            self.parser._rto_code_from_city_state("PITHORAGARH", "UTTARAKHAND"), "UK05"
        )

    def test_same_city_other_state_resolves_to_that_state(self):
        self.assertEqual(
            self.parser._rto_code_from_city_state("PITHORAGARH", "UTTAR PRADESH"),
            "UP03",
        )

    def test_unknown_state_returns_blank(self):
        self.assertEqual(self.parser._rto_code_from_city_state("NAGPUR", "WAKANDA"), "")

    def test_blank_inputs_return_blank(self):
        self.assertEqual(self.parser._rto_code_from_city_state("", "MAHARASHTRA"), "")
        self.assertEqual(self.parser._rto_code_from_city_state("NAGPUR", ""), "")

    # --- end-to-end through _post_process ------------------------------------

    def test_new_vehicle_fills_rto_num_from_extracted_city_state(self):
        data = {
            "reg_num": {"value": "NEW"},
            "rto_num": {"value": ""},
            "rto_city": {"value": "NAGPUR"},
            "rto_state": {"value": "MAHARASHTRA"},
        }
        out = self.parser._post_process(data)
        self.assertEqual(out["rto_num"]["value"], "MH31")
        self.assertEqual(out["rto_num"]["is_extracted"], "TRUE")
        # The city/state the model read stay intact.
        self.assertEqual(out["rto_city"]["value"], "NAGPUR")
        self.assertEqual(out["rto_state"]["value"], "MAHARASHTRA")

    def test_used_vehicle_rto_num_left_untouched(self):
        # A used vehicle has a reg number; the LLM already filled rto_num and the
        # NEW-vehicle reverse lookup must not run.
        data = {
            "reg_num": {"value": "UP16DL0043"},
            "rto_num": {"value": "UP16"},
            "rto_city": {"value": ""},
            "rto_state": {"value": ""},
        }
        out = self.parser._post_process(data)
        self.assertEqual(out["rto_num"]["value"], "UP16")

    def test_new_vehicle_without_city_leaves_rto_num_blank(self):
        data = {
            "reg_num": {"value": "NEW"},
            "rto_num": {"value": ""},
            "rto_city": {"value": ""},
            "rto_state": {"value": ""},
        }
        out = self.parser._post_process(data)
        self.assertEqual(out["rto_num"]["value"], "")
