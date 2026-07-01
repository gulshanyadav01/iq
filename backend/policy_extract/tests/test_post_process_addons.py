"""
Unit tests for totalAddonPremium computation in MineruGeminiParser.

`_apply_total_addon_premium` is a pure dict->dict helper (no DB/SDK), so we exercise
it on a bare instance built via __new__ — bypassing __init__ (which constructs the
genai.Client and the DB-backed MasterDataResolver). ADDON_TOTAL_FIELDS is a class
attribute, so it is available without construction.
"""

import unittest

from policy_extract.services.mineru_gemini_parser import MineruGeminiParser


def _field(value):
    return {
        "id": "", "value": str(value), "confidence_score": 1.0,
        "is_extracted": "TRUE", "do_verification": "FALSE",
    }


class TotalAddonPremiumTests(unittest.TestCase):
    def setUp(self):
        # Bare instance — no __init__, no SDK/DB. Class attrs (ADDON_TOTAL_FIELDS) still resolve.
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, **premiums):
        data = {k: _field(v) for k, v in premiums.items()}
        self.parser._apply_total_addon_premium(data)
        return data

    def test_sum_of_nine_components(self):
        data = self._run(
            zeroDepPremium=100, rtiPremium=200, ePPremium=300,
            tyreProtectionPremium=50, nCBProtection=80, consumablesPremium=70,
            lPBPremium=40, rSAPremium=60, keyReplacement=25,
        )
        # 100+200+300+50+80+70+40+60+25 = 925
        self.assertEqual(data["totalAddonPremium"]["value"], "925")
        self.assertEqual(data["totalAddonPremium"]["is_extracted"], "TRUE")
        self.assertEqual(data["totalAddonPremium"]["confidence_score"], 0.95)

    def test_excludes_pa_cng_paid_driver_and_new_fields(self):
        data = self._run(
            zeroDepPremium=100,
            pa_cover=500, cngAmount=400, paidDriverPremium=300,
            bifuelKitTPPremium=60, paUnnamedPassengerPremium=70,
        )
        # Only zeroDepPremium counts; the rest are deliberately excluded.
        self.assertEqual(data["totalAddonPremium"]["value"], "100")

    def test_ep_coalesce_alt_field_only(self):
        # LLM filled the alt field and left the primary at 0 -> EP must still count once.
        data = self._run(ePPremium=0, engineProtectionPremium=300, rSAPremium=50)
        self.assertEqual(data["totalAddonPremium"]["value"], "350")

    def test_ep_coalesce_no_double_count(self):
        # Both fields populated (same add-on) -> counted once via max(), not summed.
        data = self._run(ePPremium=300, engineProtectionPremium=300, rSAPremium=50)
        self.assertEqual(data["totalAddonPremium"]["value"], "350")

    def test_all_zero(self):
        data = self._run(
            zeroDepPremium=0, rtiPremium=0, ePPremium=0, tyreProtectionPremium=0,
            nCBProtection=0, consumablesPremium=0, lPBPremium=0, rSAPremium=0,
            keyReplacement=0,
        )
        self.assertEqual(data["totalAddonPremium"]["value"], "0")

    def test_field_auto_created_when_absent(self):
        # No components at all -> field is created with the full 5-key dict shape.
        data = {}
        self.parser._apply_total_addon_premium(data)
        field = data["totalAddonPremium"]
        self.assertEqual(field["value"], "0")
        self.assertEqual(field["is_extracted"], "TRUE")
        self.assertEqual(
            set(field.keys()),
            {"id", "value", "confidence_score", "is_extracted", "do_verification"},
        )

    def test_overwrites_existing_llm_value(self):
        # If the LLM wrongly emitted a value, the computed sum overwrites it.
        data = {k: _field(v) for k, v in {"rSAPremium": 60, "nCBProtection": 40}.items()}
        data["totalAddonPremium"] = _field(99999)
        self.parser._apply_total_addon_premium(data)
        self.assertEqual(data["totalAddonPremium"]["value"], "100")

    def test_tolerates_messy_numeric_strings(self):
        # Commas / currency artifacts are stripped before summing.
        data = {
            "rSAPremium": _field("1,200"),
            "nCBProtection": _field("Rs 300"),
        }
        self.parser._apply_total_addon_premium(data)
        self.assertEqual(data["totalAddonPremium"]["value"], "1500")


if __name__ == "__main__":
    unittest.main()
