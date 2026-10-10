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
        # Cross-zone RTO: Faridabad is physically Haryana (HR prefix) but the
        # master files it under the delhi-ncr zone slug (its value names "Delhi
        # NCR"), the same slug state=DELHI resolves to. Used to prove the
        # city/state fallback does not flip the extracted state to HARYANA.
        RTOMaster.objects.create(
            rto_id=6,
            code="HR29",
            value="HR29-Faridabad(Delhi NCR)(Group B)",
            state_slug="delhi-ncr",
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

    def test_known_state_unmatched_city_returns_blank(self):
        # Value: protects=_rto_code_from_city_state returns "" for a known state
        # whose city matches no RTOMaster locality;
        # fails_when=the `if not codes: return ""` guard is removed (return codes[0]
        # then raises IndexError);
        # why_new=the other blank-return tests exit before the DB lookup, this one
        # exercises match_rto_localities returning [];
        # seam=none
        #
        # The state resolves (Maharashtra -> MH) but the city is in no RTOMaster
        # locality for it. The lookup must return "" rather than a spurious code
        # (e.g. the state's first RTO), so a NEW-vehicle policy is never pinned
        # to a wrong RTO just because the model misread the city.
        self.assertEqual(
            self.parser._rto_code_from_city_state("ATLANTIS", "MAHARASHTRA"), ""
        )

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

    def test_raw_data_rto_location_wins_over_city_state(self):
        # Value: protects=the rto_location path (path 1) takes precedence and the
        # city/state fallback does not run once path 1 yields a code;
        # fails_when=the `if not rto_code` guard before the fallback is dropped so
        # the city/state lookup overwrites the location-derived code;
        # why_new=every other test leaves rto_reverse_map empty and passes no
        # raw_data, so path 1 and the path-1-wins precedence are otherwise unexercised;
        # seam=none
        self.parser.rto_reverse_map = {("MAHARASHTRA", "PUNE"): "MH12"}
        data = {
            "reg_num": {"value": "NEW"},
            "rto_num": {"value": ""},
            "rto_city": {"value": "NAGPUR"},
            "rto_state": {"value": "MAHARASHTRA"},
        }
        raw = {"vehicles": [{"rto_location": "MAHARASHTRA - Pune"}]}
        out = self.parser._post_process(data, raw_data=raw)
        # Path 1 (rto_location → MH12) wins; the Nagpur city/state fallback (MH31)
        # must not run.
        self.assertEqual(out["rto_num"]["value"], "MH12")

    def test_falls_back_to_city_state_when_rto_location_unresolved(self):
        # Value: protects=the city/state fallback runs when path 1 is present but
        # yields no code;
        # fails_when=path 1 swallows the branch or the fallback is gated on raw_data
        # being absent;
        # why_new=exercises path1-miss -> path2-hit, distinct from the no-raw_data case;
        # seam=none
        self.parser.rto_reverse_map = {}  # location cannot resolve
        data = {
            "reg_num": {"value": "NEW"},
            "rto_num": {"value": ""},
            "rto_city": {"value": "NAGPUR"},
            "rto_state": {"value": "MAHARASHTRA"},
        }
        raw = {"vehicles": [{"rto_location": "SOMEWHERE - Nowhere"}]}
        out = self.parser._post_process(data, raw_data=raw)
        self.assertEqual(out["rto_num"]["value"], "MH31")

    def test_cross_zone_fallback_fills_code_without_clobbering_state(self):
        # Value: protects=the city/state fallback fills rto_num but leaves the
        # authoritative extracted rto_state untouched for a cross-zone RTO whose
        # registration prefix differs from the zone state (Faridabad is HR, zone
        # delhi-ncr, extracted state DELHI);
        # fails_when=the fallback re-derives rto_state from the matched code's
        # prefix, flipping DELHI -> HARYANA;
        # why_new=all other fallback tests use same-prefix states (MH/UK/UP) so the
        # cross-zone clobber is invisible to them;
        # seam=none
        data = {
            "reg_num": {"value": "NEW"},
            "rto_num": {"value": ""},
            "rto_city": {"value": "FARIDABAD"},
            "rto_state": {"value": "DELHI"},
        }
        out = self.parser._post_process(data)
        # HR29 is the correct RTO for Faridabad (filed under the delhi-ncr zone).
        self.assertEqual(out["rto_num"]["value"], "HR29")
        # ...but the extracted state must survive — not be rewritten to HARYANA.
        self.assertEqual(out["rto_state"]["value"], "DELHI")

    def test_fallback_does_not_override_model_extracted_rto_num(self):
        # Value: protects=a NEW vehicle that already carries an rto_num keeps it;
        # the city/state fallback only fills a blank rto_num;
        # fails_when=the fallback overwrites a non-blank rto_num at confidence 0.90;
        # why_new=guards the "fill only when blank" rule added for the broadened
        # is_new_vehicle branch;
        # seam=none
        data = {
            "reg_num": {"value": "NEW"},
            "rto_num": {"value": "MH12"},  # model read a code off the document
            "rto_city": {"value": "NAGPUR"},
            "rto_state": {"value": "MAHARASHTRA"},
        }
        out = self.parser._post_process(data)
        self.assertEqual(out["rto_num"]["value"], "MH12")
