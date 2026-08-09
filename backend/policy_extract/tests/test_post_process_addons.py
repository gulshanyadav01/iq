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


class AddonFlagDerivationTests(unittest.TestCase):
    """_apply_addon_flags is a pure dict->dict helper (uses only class attrs and
    _apply_total_addon_premium), so exercise it on a bare __new__ instance."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _base_structured(self, **overrides):
        # All add-on flags + their paired premiums + addOn/addonList, pre-seeded
        # so we can assert the method overrides them.
        data = {}
        for flag, (premium_field, _name) in MineruGeminiParser.ADDON_PAIRS.items():
            data[flag] = _field("YES")
            data[premium_field] = _field(0)
        data["addOn"] = _field("TRUE")
        data["addonList"] = _field([])
        data.update(overrides)
        return data

    def test_lookalike_line_forced_off(self):
        # Stage 2 false positive: raw has the IMT-28 LL line but Stage 1 left
        # addon_name empty, while Stage 2 emitted YES / a premium / addOn=TRUE.
        sd = self._base_structured(
            isInsurerPaidDriver=_field("YES"),
            paidDriverPremium=_field(100),
            addOn=_field("TRUE"),
        )
        raw = {"coverages": [], "premiums": [
            {"label": "Covers for Lamps Tyres/Tubes Mudguards (IMT-23)", "value": 260.1, "addon_name": None},
            {"label": "Legal Liability to paid driver/Conductor/cleaner", "value": 100.0, "addon_name": None},
        ]}
        self.parser._apply_addon_flags(sd, raw)
        # Every flag off, paired premiums zeroed, top-level presence FALSE.
        for flag, (premium_field, _name) in MineruGeminiParser.ADDON_PAIRS.items():
            self.assertEqual(sd[flag]["value"], "NO", flag)
            self.assertEqual(sd[premium_field]["value"], "0", premium_field)
        self.assertEqual(sd["addOn"]["value"], "FALSE")
        self.assertEqual(sd["addonList"]["value"], [])
        self.assertEqual(sd["totalAddonPremium"]["value"], "0")

    def test_detected_from_premiums_stays_on(self):
        # A genuinely selected add-on tagged by Stage 1 (addon_name + value>0).
        sd = self._base_structured(
            isRSACover=_field("NO"), rSAPremium=_field(500), addOn=_field("FALSE"),
        )
        raw = {"coverages": [], "premiums": [
            {"label": "RSA", "value": 500, "addon_name": "roadside_assistance"},
        ]}
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isRSACover"]["value"], "YES")
        self.assertEqual(sd["rSAPremium"]["value"], "500")   # premium preserved
        self.assertEqual(sd["addOn"]["value"], "TRUE")
        self.assertEqual(sd["addonList"]["value"], ["ROADSIDE_ASSISTANCE"])
        self.assertEqual(sd["totalAddonPremium"]["value"], "500")

    def test_coverage_positive_premium_stays_on(self):
        # coverages[] add-on with a positive premium -> selected -> YES.
        sd = self._base_structured(isZeroDepCover=_field("NO"))
        raw = {"coverages": [
            {"name": "Zero Dep", "is_addon": True, "addon_name": "zero_dep", "premium": 1200},
        ], "premiums": []}
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isZeroDepCover"]["value"], "YES")
        self.assertEqual(sd["addOn"]["value"], "TRUE")

    def test_zero_premium_coverage_not_counted(self):
        # A zero-premium coverages[] entry is indistinguishable from a catalog row
        # -> NOT counted (guards against the lite model's catalog dump).
        sd = self._base_structured(isZeroDepCover=_field("YES"))
        raw = {"coverages": [
            {"name": "Zero Dep", "is_addon": True, "addon_name": "zero_dep", "premium": 0},
        ], "premiums": []}
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isZeroDepCover"]["value"], "NO")
        self.assertEqual(sd["addOn"]["value"], "FALSE")

    def test_catalog_dump_all_zero_premium_ignored(self):
        # gemini-2.5-flash-lite dumps the whole unticked "Add-on Covers" catalog
        # into coverages[] with is_addon=true, premium=0 -> none should count.
        catalog = [
            ("Nil Depreciation Cover", "zero_dep"), ("Tyre Protector", "tyre_protection"),
            ("Key Protect Cover", "key_replacement"), ("Consumable Expenses", "consumables"),
            ("Personal belongings Cover", "personal_belongings"), ("Engine Protector", "engine_protection"),
            ("NCB Retention Cover", "ncb_protection"), ("Return to Invoice", "return_to_invoice"),
        ]
        sd = self._base_structured()
        raw = {"coverages": [
            {"name": None, "value": name, "is_addon": True, "addon_name": an, "premium": 0}
            for name, an in catalog
        ], "premiums": []}
        self.parser._apply_addon_flags(sd, raw)
        for flag in MineruGeminiParser.ADDON_PAIRS:
            self.assertEqual(sd[flag]["value"], "NO", flag)
        self.assertEqual(sd["addOn"]["value"], "FALSE")
        self.assertEqual(sd["addonList"]["value"], [])

    def test_template_slot_dash_skipped(self):
        # coverages[] entry with premium "-" is an unselected template slot -> NO.
        sd = self._base_structured(isTyreProtectionCover=_field("YES"))
        raw = {"coverages": [
            {"name": "Tyre", "is_addon": True, "addon_name": "tyre_protection", "premium": "-"},
        ], "premiums": []}
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isTyreProtectionCover"]["value"], "NO")
        self.assertEqual(sd["addOn"]["value"], "FALSE")

    def test_premium_addon_name_zero_value_skipped(self):
        # premiums[] tagged addon but value 0 (template slot) -> NO.
        sd = self._base_structured(isConsumablesCover=_field("YES"))
        raw = {"coverages": [], "premiums": [
            {"label": "Consumables", "value": 0, "addon_name": "consumables"},
        ]}
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isConsumablesCover"]["value"], "NO")

    def test_stage1_mistag_paid_driver_ll_still_off(self):
        # gemini-2.5-flash-lite sometimes re-tags the IMT-28 statutory LL line with
        # addon_name="paid_driver" despite the prompt. The deterministic label
        # guard must still drop it so the flag/premium/addOn stay off.
        sd = self._base_structured(
            isInsurerPaidDriver=_field("YES"), paidDriverPremium=_field(100),
            addOn=_field("TRUE"),
        )
        raw = {"coverages": [], "premiums": [
            {"label": "Legal Liability to paid driver and/or Conductor and/or cleaner",
             "value": 100.0, "addon_name": "paid_driver"},
        ]}
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isInsurerPaidDriver"]["value"], "NO")
        self.assertEqual(sd["paidDriverPremium"]["value"], "0")
        self.assertEqual(sd["addOn"]["value"], "FALSE")

    def test_stage1_mistag_imt23_tyre_still_off(self):
        # IMT-23 lamps/tyres mis-tagged as tyre_protection in coverages[] -> dropped.
        sd = self._base_structured(isTyreProtectionCover=_field("YES"))
        raw = {"coverages": [
            {"name": None, "value": "Covers for Lamps Tyres/Tubes Mudguards (IMT-23)",
             "is_addon": True, "addon_name": "tyre_protection", "premium": 260.1},
        ], "premiums": []}
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isTyreProtectionCover"]["value"], "NO")
        self.assertEqual(sd["addOn"]["value"], "FALSE")

    def test_genuine_tyre_secure_not_dropped(self):
        # A real "Tyre Secure" add-on names only tyres (no lamp) -> NOT filtered.
        sd = self._base_structured(isTyreProtectionCover=_field("NO"))
        raw = {"coverages": [], "premiums": [
            {"label": "Tyre Secure Cover", "value": 350, "addon_name": "tyre_protection"},
        ]}
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isTyreProtectionCover"]["value"], "YES")

    def test_ep_alias_zeroed_when_not_selected(self):
        # engineProtectionPremium alias must also be zeroed so totalAddonPremium
        # (which coalesces the two) does not resurrect a dropped EP add-on.
        sd = self._base_structured(
            isEPCover=_field("YES"), ePPremium=_field(0),
            engineProtectionPremium=_field(300),
        )
        raw = {"coverages": [], "premiums": []}
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isEPCover"]["value"], "NO")
        self.assertEqual(sd["engineProtectionPremium"]["value"], "0")
        self.assertEqual(sd["totalAddonPremium"]["value"], "0")


class PlanTypeZeroDepSignalTests(unittest.TestCase):
    """plan_type_id ('Comprehensive' vs 'Comprehensive + ZD') keys on whether zero-dep
    is ACTUALLY selected — i.e. 'zero_dep' in _collect_detected_addons — NOT the Stage-2
    isZeroDepCover string, which _apply_addon_flags only corrects later. This guards the
    '+ ZD' false positive on a policy with no zero-dep add-on."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def test_not_detected_without_premium(self):
        raw = {"coverages": [], "premiums": []}
        self.assertNotIn("zero_dep", self.parser._collect_detected_addons(raw))

    def test_not_detected_from_zero_premium_catalog(self):
        raw = {"coverages": [
            {"value": "Nil Depreciation Cover", "is_addon": True, "addon_name": "zero_dep", "premium": 0},
        ], "premiums": []}
        self.assertNotIn("zero_dep", self.parser._collect_detected_addons(raw))

    def test_detected_when_premium_present(self):
        raw = {"coverages": [], "premiums": [
            {"label": "Zero Depreciation", "value": 1200, "addon_name": "zero_dep"},
        ]}
        self.assertIn("zero_dep", self.parser._collect_detected_addons(raw))


class CubicCapacityFallbackTests(unittest.TestCase):
    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, vehicles, current="0"):
        sd = {"cubic_capacity_value": _field(current)}
        self.parser._apply_cubic_capacity_fallback(sd, {"vehicles": vehicles})
        return sd["cubic_capacity_value"]["value"]

    def test_backfills_from_cc_key(self):
        self.assertEqual(self._run([{"cc": 3455}]), "3455")

    def test_backfills_from_cc_hp_watt_key(self):
        self.assertEqual(self._run([{"cc_hp_watt": "3455"}]), "3455")

    def test_first_integer_only(self):
        # A combined "cc/hp" style string -> take the first integer (the CC).
        self.assertEqual(self._run([{"cc_hp_watt": "3455 / 120"}]), "3455")

    def test_does_not_overwrite_existing(self):
        self.assertEqual(self._run([{"cc": 9999}], current="1500"), "1500")

    def test_absent_stays_zero(self):
        self.assertEqual(self._run([{"make": "X"}]), "0")


class NcbPercentTests(unittest.TestCase):
    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, premiums, current="0"):
        sd = {"ncb_percent": _field(current)}
        self.parser._apply_ncb_percent(sd, {"premiums": premiums})
        return sd["ncb_percent"]["value"]

    def test_derives_applied_percent(self):
        self.assertEqual(self._run([{"label": "Deduct 20 % for NCB", "value": -398.82}]), "20")

    def test_ignores_protection_line(self):
        # NCB Protection add-on line has no applied % -> left unchanged.
        self.assertEqual(self._run([{"label": "NCB Protection Cover", "value": 300}]), "0")

    def test_no_ncb_line_unchanged(self):
        self.assertEqual(self._run([{"label": "Basic OD", "value": 1734}]), "0")


if __name__ == "__main__":
    unittest.main()
