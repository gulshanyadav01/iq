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
        "id": "",
        "value": str(value),
        "confidence_score": 1.0,
        "is_extracted": "TRUE",
        "do_verification": "FALSE",
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
            zeroDepPremium=100,
            rtiPremium=200,
            ePPremium=300,
            tyreProtectionPremium=50,
            nCBProtection=80,
            consumablesPremium=70,
            lPBPremium=40,
            rSAPremium=60,
            keyReplacement=25,
        )
        # 100+200+300+50+80+70+40+60+25 = 925
        self.assertEqual(data["totalAddonPremium"]["value"], "925")
        self.assertEqual(data["totalAddonPremium"]["is_extracted"], "TRUE")
        self.assertEqual(data["totalAddonPremium"]["confidence_score"], 0.95)

    def test_excludes_pa_cng_paid_driver_and_new_fields(self):
        data = self._run(
            zeroDepPremium=100,
            pa_cover=500,
            cngAmount=400,
            paidDriverPremium=300,
            bifuelKitTPPremium=60,
            paUnnamedPassengerPremium=70,
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
            zeroDepPremium=0,
            rtiPremium=0,
            ePPremium=0,
            tyreProtectionPremium=0,
            nCBProtection=0,
            consumablesPremium=0,
            lPBPremium=0,
            rSAPremium=0,
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
        data = {
            k: _field(v) for k, v in {"rSAPremium": 60, "nCBProtection": 40}.items()
        }
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
        raw = {
            "coverages": [],
            "premiums": [
                {
                    "label": "Covers for Lamps Tyres/Tubes Mudguards (IMT-23)",
                    "value": 260.1,
                    "addon_name": None,
                },
                {
                    "label": "Legal Liability to paid driver/Conductor/cleaner",
                    "value": 100.0,
                    "addon_name": None,
                },
            ],
        }
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
            isRSACover=_field("NO"),
            rSAPremium=_field(500),
            addOn=_field("FALSE"),
        )
        raw = {
            "coverages": [],
            "premiums": [
                {"label": "RSA", "value": 500, "addon_name": "roadside_assistance"},
            ],
        }
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isRSACover"]["value"], "YES")
        self.assertEqual(sd["rSAPremium"]["value"], "500")  # premium preserved
        self.assertEqual(sd["addOn"]["value"], "TRUE")
        self.assertEqual(sd["addonList"]["value"], ["ROADSIDE_ASSISTANCE"])
        self.assertEqual(sd["totalAddonPremium"]["value"], "500")

    def test_coverage_positive_premium_stays_on(self):
        # coverages[] add-on with a positive premium -> selected -> YES.
        sd = self._base_structured(isZeroDepCover=_field("NO"))
        raw = {
            "coverages": [
                {
                    "name": "Zero Dep",
                    "is_addon": True,
                    "addon_name": "zero_dep",
                    "premium": 1200,
                },
            ],
            "premiums": [],
        }
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isZeroDepCover"]["value"], "YES")
        self.assertEqual(sd["addOn"]["value"], "TRUE")

    def test_zero_premium_coverage_not_counted(self):
        # A zero-premium coverages[] entry is indistinguishable from a catalog row
        # -> NOT counted (guards against the lite model's catalog dump).
        sd = self._base_structured(isZeroDepCover=_field("YES"))
        raw = {
            "coverages": [
                {
                    "name": "Zero Dep",
                    "is_addon": True,
                    "addon_name": "zero_dep",
                    "premium": 0,
                },
            ],
            "premiums": [],
        }
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isZeroDepCover"]["value"], "NO")
        self.assertEqual(sd["addOn"]["value"], "FALSE")

    def test_catalog_dump_all_zero_premium_ignored(self):
        # gemini-2.5-flash-lite dumps the whole unticked "Add-on Covers" catalog
        # into coverages[] with is_addon=true, premium=0 -> none should count.
        catalog = [
            ("Nil Depreciation Cover", "zero_dep"),
            ("Tyre Protector", "tyre_protection"),
            ("Key Protect Cover", "key_replacement"),
            ("Consumable Expenses", "consumables"),
            ("Personal belongings Cover", "personal_belongings"),
            ("Engine Protector", "engine_protection"),
            ("NCB Retention Cover", "ncb_protection"),
            ("Return to Invoice", "return_to_invoice"),
        ]
        sd = self._base_structured()
        raw = {
            "coverages": [
                {
                    "name": None,
                    "value": name,
                    "is_addon": True,
                    "addon_name": an,
                    "premium": 0,
                }
                for name, an in catalog
            ],
            "premiums": [],
        }
        self.parser._apply_addon_flags(sd, raw)
        for flag in MineruGeminiParser.ADDON_PAIRS:
            self.assertEqual(sd[flag]["value"], "NO", flag)
        self.assertEqual(sd["addOn"]["value"], "FALSE")
        self.assertEqual(sd["addonList"]["value"], [])

    def test_opted_zero_premium_coverage_counted(self):
        # A bundled add-on with premium 0 but an explicit opted=true marker (Stage 1
        # saw it under an "opted"/"Optional Cover"/UIN table) IS selected -> YES.
        sd = self._base_structured(isZeroDepCover=_field("NO"))
        raw = {
            "coverages": [
                {
                    "name": "Zero Dep",
                    "is_addon": True,
                    "addon_name": "zero_dep",
                    "premium": 0,
                    "opted": True,
                },
            ],
            "premiums": [],
        }
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isZeroDepCover"]["value"], "YES")
        self.assertEqual(sd["addOn"]["value"], "TRUE")
        self.assertEqual(sd["addonList"]["value"], ["ZERO_DEP"])

    def test_opted_false_zero_premium_still_off(self):
        # opted explicitly false + no premium -> unselected template slot -> NO.
        sd = self._base_structured(isZeroDepCover=_field("YES"))
        raw = {
            "coverages": [
                {
                    "name": "Zero Dep",
                    "is_addon": True,
                    "addon_name": "zero_dep",
                    "premium": 0,
                    "opted": False,
                },
            ],
            "premiums": [],
        }
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isZeroDepCover"]["value"], "NO")
        self.assertEqual(sd["addOn"]["value"], "FALSE")

    def test_opted_string_truthy_counted(self):
        # The LLM may emit opted as the string "true"/"yes" -> still counts.
        sd = self._base_structured(isRTICover=_field("NO"))
        raw = {
            "coverages": [
                {
                    "name": "Return to Invoice",
                    "is_addon": True,
                    "addon_name": "return_to_invoice",
                    "premium": 0,
                    "opted": "true",
                },
            ],
            "premiums": [],
        }
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isRTICover"]["value"], "YES")

    def test_opted_lookalike_still_dropped(self):
        # opted=true must NOT bypass the statutory/standard look-alike guard:
        # an IMT-23 lamps/tyres line tagged tyre_protection stays OFF.
        sd = self._base_structured(isTyreProtectionCover=_field("YES"))
        raw = {
            "coverages": [
                {
                    "name": None,
                    "value": "Covers for Lamps Tyres/Tubes Mudguards (IMT-23)",
                    "is_addon": True,
                    "addon_name": "tyre_protection",
                    "premium": 0,
                    "opted": True,
                },
            ],
            "premiums": [],
        }
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isTyreProtectionCover"]["value"], "NO")
        self.assertEqual(sd["addOn"]["value"], "FALSE")

    def test_godigit_optional_cover_table_all_opted(self):
        # Go Digit Private Car "Optional Cover / Coverage Details / UIN" table: 7 opted
        # covers, each premium 0 (cost bundled into OD "Others"), opted=true. All 7 flag on.
        covers = [
            (
                "Digit Private Car Parts Depreciation Protect",
                "zero_dep",
                "isZeroDepCover",
            ),
            (
                "Digit Private Car Engine and Gear Box Protect",
                "engine_protection",
                "isEPCover",
            ),
            ("Digit Private Car Consumable Cover", "consumables", "isConsumablesCover"),
            (
                "Digit Private Car Key & Lock Protect",
                "key_replacement",
                "isKeyReplacementCover",
            ),
            (
                "Digit Private Car Breakdown Assistance",
                "roadside_assistance",
                "isRSACover",
            ),
            ("Digit Private Car Return to Invoice", "return_to_invoice", "isRTICover"),
            (
                "Digit Private Car Loss of Personal Belongings",
                "personal_belongings",
                "isLPBCover",
            ),
        ]
        sd = self._base_structured(**{flag: _field("NO") for _v, _an, flag in covers})
        raw = {
            "coverages": [
                {
                    "name": None,
                    "value": v,
                    "is_addon": True,
                    "addon_name": an,
                    "premium": 0,
                    "opted": True,
                }
                for v, an, _flag in covers
            ],
            "premiums": [],
        }
        self.parser._apply_addon_flags(sd, raw)
        for _v, _an, flag in covers:
            self.assertEqual(sd[flag]["value"], "YES", flag)
        # NCB protection and tyre are NOT in this table -> stay off.
        self.assertEqual(sd["isNCBCover"]["value"], "NO")
        self.assertEqual(sd["isTyreProtectionCover"]["value"], "NO")
        self.assertEqual(sd["addOn"]["value"], "TRUE")
        self.assertEqual(len(sd["addonList"]["value"]), 7)

    def test_template_slot_dash_skipped(self):
        # coverages[] entry with premium "-" is an unselected template slot -> NO.
        sd = self._base_structured(isTyreProtectionCover=_field("YES"))
        raw = {
            "coverages": [
                {
                    "name": "Tyre",
                    "is_addon": True,
                    "addon_name": "tyre_protection",
                    "premium": "-",
                },
            ],
            "premiums": [],
        }
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isTyreProtectionCover"]["value"], "NO")
        self.assertEqual(sd["addOn"]["value"], "FALSE")

    def test_premium_addon_name_zero_value_skipped(self):
        # premiums[] tagged addon but value 0 (template slot) -> NO.
        sd = self._base_structured(isConsumablesCover=_field("YES"))
        raw = {
            "coverages": [],
            "premiums": [
                {"label": "Consumables", "value": 0, "addon_name": "consumables"},
            ],
        }
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isConsumablesCover"]["value"], "NO")

    def test_stage1_mistag_paid_driver_ll_still_off(self):
        # gemini-2.5-flash-lite sometimes re-tags the IMT-28 statutory LL line with
        # addon_name="paid_driver" despite the prompt. The deterministic label
        # guard must still drop it so the flag/premium/addOn stay off.
        sd = self._base_structured(
            isInsurerPaidDriver=_field("YES"),
            paidDriverPremium=_field(100),
            addOn=_field("TRUE"),
        )
        raw = {
            "coverages": [],
            "premiums": [
                {
                    "label": "Legal Liability to paid driver and/or Conductor and/or cleaner",
                    "value": 100.0,
                    "addon_name": "paid_driver",
                },
            ],
        }
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isInsurerPaidDriver"]["value"], "NO")
        self.assertEqual(sd["paidDriverPremium"]["value"], "0")
        self.assertEqual(sd["addOn"]["value"], "FALSE")

    def test_glyph_split_paid_driver_ll_kept_out_of_addonlist(self):
        # MinerU injects a currency glyph mid-label; the hardened look-alike guard
        # must still recognize the statutory LL line and keep it out of the add-on list.
        sd = self._base_structured(
            isInsurerPaidDriver=_field("YES"), paidDriverPremium=_field(50)
        )
        raw = {
            "coverages": [],
            "premiums": [
                {
                    "label": "Legal Liability to Paid (₹) Driver (Persons:1)",
                    "value": 50.0,
                    "addon_name": "paid_driver",
                },
            ],
        }
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isInsurerPaidDriver"]["value"], "NO")  # not an add-on
        self.assertNotIn("PAID_DRIVER", sd["addonList"]["value"])

    def test_paid_cleaner_mistagged_paid_driver_kept_out(self):
        # The model tags the statutory 'Legal Liability for Paid Cleaner' line with
        # addon_name='paid_driver'; the liability-keyed guard must still drop it.
        sd = self._base_structured(
            isInsurerPaidDriver=_field("YES"), paidDriverPremium=_field(50)
        )
        raw = {
            "coverages": [],
            "premiums": [
                {
                    "label": "Legal Liability for Paid Cleaner (Persons:1)(₹)",
                    "value": 50.0,
                    "addon_name": "paid_driver",
                },
            ],
        }
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isInsurerPaidDriver"]["value"], "NO")
        self.assertNotIn("PAID_DRIVER", sd["addonList"]["value"])

    def test_stage1_mistag_imt23_tyre_still_off(self):
        # IMT-23 lamps/tyres mis-tagged as tyre_protection in coverages[] -> dropped.
        sd = self._base_structured(isTyreProtectionCover=_field("YES"))
        raw = {
            "coverages": [
                {
                    "name": None,
                    "value": "Covers for Lamps Tyres/Tubes Mudguards (IMT-23)",
                    "is_addon": True,
                    "addon_name": "tyre_protection",
                    "premium": 260.1,
                },
            ],
            "premiums": [],
        }
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isTyreProtectionCover"]["value"], "NO")
        self.assertEqual(sd["addOn"]["value"], "FALSE")

    def test_genuine_tyre_secure_not_dropped(self):
        # A real "Tyre Secure" add-on names only tyres (no lamp) -> NOT filtered.
        sd = self._base_structured(isTyreProtectionCover=_field("NO"))
        raw = {
            "coverages": [],
            "premiums": [
                {
                    "label": "Tyre Secure Cover",
                    "value": 350,
                    "addon_name": "tyre_protection",
                },
            ],
        }
        self.parser._apply_addon_flags(sd, raw)
        self.assertEqual(sd["isTyreProtectionCover"]["value"], "YES")

    def test_ep_alias_zeroed_when_not_selected(self):
        # engineProtectionPremium alias must also be zeroed so totalAddonPremium
        # (which coalesces the two) does not resurrect a dropped EP add-on.
        sd = self._base_structured(
            isEPCover=_field("YES"),
            ePPremium=_field(0),
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
        raw = {
            "coverages": [
                {
                    "value": "Nil Depreciation Cover",
                    "is_addon": True,
                    "addon_name": "zero_dep",
                    "premium": 0,
                },
            ],
            "premiums": [],
        }
        self.assertNotIn("zero_dep", self.parser._collect_detected_addons(raw))

    def test_detected_when_premium_present(self):
        raw = {
            "coverages": [],
            "premiums": [
                {"label": "Zero Depreciation", "value": 1200, "addon_name": "zero_dep"},
            ],
        }
        self.assertIn("zero_dep", self.parser._collect_detected_addons(raw))

    def test_detected_when_opted_bundled(self):
        # Bundled zero-dep (premium 0) explicitly opted -> plan-type sees it selected.
        raw = {
            "coverages": [
                {
                    "value": "Parts Depreciation Protect",
                    "is_addon": True,
                    "addon_name": "zero_dep",
                    "premium": 0,
                    "opted": True,
                },
            ],
            "premiums": [],
        }
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

    def test_backfills_from_cubic_capacity_key(self):
        # The backhoe/Misc-D case: CC captured under 'cubic_capacity' (4765).
        self.assertEqual(self._run([{"cubic_capacity": 4765}]), "4765")

    def test_backfills_from_per_attribute_vehicle_objects(self):
        # Stage-1 sometimes emits one attribute per object; scan each.
        veh = [
            {"reg_num": "RJ07EA1546"},
            {"cubic_capacity": 4765, "context": "vehicle cubic capacity"},
            {"gross_vehicle_weight": "7520KG"},
        ]
        self.assertEqual(self._run(veh), "4765")

    def test_backfills_from_invented_cubic_key(self):
        # Generic net: any key whose NAME contains 'cubic' (e.g. 'cubicCapacityCC').
        self.assertEqual(self._run([{"cubicCapacityCC": "4765 CC"}]), "4765")

    def test_weight_and_wheels_not_mistaken_for_cc(self):
        # GVW/wheels keys neither known nor 'cubic'-named -> must NOT backfill CC.
        self.assertEqual(
            self._run([{"gross_vehicle_weight": "7520KG", "wheels": 4}]), "0"
        )


class SeatingCapacityFallbackTests(unittest.TestCase):
    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, vehicles, current="0"):
        sd = {"no_of_seats": _field(current)}
        self.parser._apply_seating_capacity_fallback(sd, {"vehicles": vehicles})
        return sd["no_of_seats"]["value"]

    def test_backfills_from_seating_capacity_key(self):
        self.assertEqual(self._run([{"seating_capacity": 4}]), "4")

    def test_backfills_backhoe_single_seat(self):
        self.assertEqual(self._run([{"seating_capacity": 1}]), "1")

    def test_backfills_from_per_attribute_objects(self):
        veh = [
            {"reg_num": "MH12WR1768"},
            {"seating_capacity": 2, "context": "licensed"},
        ]
        self.assertEqual(self._run(veh), "2")

    def test_backfills_from_invented_seat_key(self):
        self.assertEqual(self._run([{"licensedSeating": "5 seats"}]), "5")

    def test_wheels_cc_gvw_not_mistaken_for_seats(self):
        # None of these are seat-named -> must not backfill the seat count.
        self.assertEqual(
            self._run(
                [
                    {
                        "wheels": 4,
                        "cubic_capacity": 2956,
                        "gross_vehicle_weight": "7520KG",
                    }
                ]
            ),
            "0",
        )

    def test_does_not_overwrite_existing(self):
        self.assertEqual(self._run([{"seating_capacity": 9}], current="5"), "5")

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
        self.assertEqual(
            self._run([{"label": "Deduct 20 % for NCB", "value": -398.82}]), "20"
        )

    def test_ignores_protection_line(self):
        # NCB Protection add-on line has no applied % -> left unchanged.
        self.assertEqual(
            self._run([{"label": "NCB Protection Cover", "value": 300}]), "0"
        )

    def test_no_ncb_line_unchanged(self):
        self.assertEqual(self._run([{"label": "Basic OD", "value": 1734}]), "0")


class TotalIdvBackstopTests(unittest.TestCase):
    """_apply_total_idv raises sum_insured to the labeled grand 'Total IDV' when the
    mapper picked a smaller component (e.g. Vehicle IDV, dropping the CNG-kit value)."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, limits, current):
        sd = {"sum_insured": _field(current)}
        self.parser._apply_total_idv(sd, {"limits": limits})
        return sd["sum_insured"]["value"]

    def test_raises_to_total_over_vehicle_component(self):
        # Mapper grabbed Vehicle IDV (141322); total (191322) must win.
        limits = [
            {"context": "vehicle IDV", "value": 141322},
            {"context": "CNG/LPG KIT IDV", "value": 50000},
            {"context": "total IDV", "value": 191322},
        ]
        self.assertEqual(self._run(limits, "141322"), "191322")

    def test_leaves_correct_total_untouched(self):
        # Already the total -> no change (never lowers, equal is a no-op).
        limits = [
            {"context": "vehicle IDV", "value": 165348},
            {"context": "total IDV", "value": 215348},
        ]
        self.assertEqual(self._run(limits, "215348"), "215348")

    def test_never_lowers(self):
        # A stray smaller "total" line must never pull a larger sum_insured down.
        limits = [{"context": "total IDV", "value": 100}]
        self.assertEqual(self._run(limits, "827753"), "827753")

    def test_no_total_line_unchanged(self):
        # No labeled total -> leave the mapped value alone.
        limits = [{"context": "vehicle IDV", "value": 59624}]
        self.assertEqual(self._run(limits, "59624"), "59624")

    def test_pa_sum_insured_not_treated_as_total(self):
        # A PA cover sum insured (no 'idv' token) must not be mistaken for total IDV.
        limits = [
            {"context": "P.A. Cover for Owner Driver sum insured", "value": 1500000}
        ]
        self.assertEqual(self._run(limits, "191322"), "191322")


class FinancierBackfillTests(unittest.TestCase):
    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, raw, current=""):
        sd = {"financialInstitutionName": _field(current)}
        self.parser._apply_financier(sd, raw)
        return sd["financialInstitutionName"]["value"]

    def test_backfills_from_financier_participant(self):
        raw = {
            "participants": [
                {"value": "GANESHA RAM DHAKA", "context": "insured name"},
                {"value": "CHOLA. INV. AND FIN. COM. LTD.", "context": "financier"},
            ]
        }
        self.assertEqual(self._run(raw), "CHOLA. INV. AND FIN. COM. LTD.")

    def test_backfills_from_hypothecation_entity(self):
        raw = {
            "entities": [{"value": "BAJAJ AUTO CREDIT LTD", "label": "Hypothecation"}]
        }
        self.assertEqual(self._run(raw), "BAJAJ AUTO CREDIT LTD")

    def test_does_not_overwrite_existing(self):
        raw = {"participants": [{"value": "OTHER BANK", "context": "financier"}]}
        self.assertEqual(self._run(raw, current="AXIS BANK LTD"), "AXIS BANK LTD")

    def test_absent_stays_blank(self):
        raw = {"participants": [{"value": "SOMEONE", "context": "insured name"}]}
        self.assertEqual(self._run(raw), "")


class NomineeGuardTests(unittest.TestCase):
    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, nominee, customer, relation="SELF"):
        sd = {
            "nomineeName": _field(nominee),
            "cust_name": _field(customer),
            "nomineeRelationWithCustomer": _field(relation),
        }
        self.parser._apply_nominee_guard(sd, {})
        return sd["nomineeName"]["value"], sd["nomineeRelationWithCustomer"]["value"]

    def test_blanks_nominee_equal_to_insured(self):
        # The observed hallucination: nominee copied from the insured -> cleared.
        self.assertEqual(self._run("GANESHA RAM DHAKA", "GANESHA RAM DHAKA"), ("", ""))

    def test_blanks_ignoring_case_and_punctuation(self):
        self.assertEqual(self._run("kuldeep .", "KULDEEP"), ("", ""))

    def test_keeps_genuine_nominee(self):
        # A real, different nominee is preserved along with the relation.
        self.assertEqual(
            self._run("V AJITHA .", "VOLETY SREE RAMA RATAN", "WIFE"),
            ("V AJITHA .", "WIFE"),
        )

    def test_blank_nominee_unchanged(self):
        self.assertEqual(self._run("", "GANESHA RAM DHAKA"), ("", "SELF"))


class PaidDriverTests(unittest.TestCase):
    """_apply_paid_driver sets the STANDALONE isInsurerPaidDriver / paidDriverPremium
    from the priced 'Legal Liability to Paid Driver' line — without adding it to the
    add-on list (that exclusion is _apply_addon_flags' job)."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, raw):
        sd = {"isInsurerPaidDriver": _field("NO"), "paidDriverPremium": _field(0)}
        self.parser._apply_paid_driver(sd, raw)
        return sd["isInsurerPaidDriver"]["value"], sd["paidDriverPremium"]["value"]

    def test_sets_yes_and_premium_from_ll_line(self):
        raw = {
            "premiums": [
                {
                    "label": "Legal Liability to Paid Driver (Persons:1)",
                    "value": 50.0,
                    "addon_name": "paid_driver",
                }
            ]
        }
        self.assertEqual(self._run(raw), ("YES", "50"))

    def test_matches_despite_glyph_split(self):
        raw = {
            "premiums": [
                {
                    "label": "Legal Liability to Paid (₹) Driver (Persons:1)",
                    "value": 50.0,
                }
            ]
        }
        self.assertEqual(self._run(raw), ("YES", "50"))

    def test_paid_cleaner_line_ignored(self):
        raw = {
            "premiums": [
                {"label": "Legal Liability for Paid Cleaner (Persons:1)", "value": 50.0}
            ]
        }
        self.assertEqual(self._run(raw), ("NO", "0"))

    def test_pa_for_paid_driver_not_matched(self):
        # A PA cover (not a legal-liability line) must not set the LL paid-driver field.
        raw = {"premiums": [{"label": "PA cover for Paid Driver", "value": 200.0}]}
        self.assertEqual(self._run(raw), ("NO", "0"))

    def test_absent_stays_no(self):
        raw = {"premiums": [{"label": "Basic Third-Party Liability", "value": 5833}]}
        self.assertEqual(self._run(raw), ("NO", "0"))

    def test_from_coverages_bucket(self):
        raw = {
            "coverages": [
                {
                    "value": "Legal Liability to Paid Driver",
                    "premium": 50,
                    "opted": True,
                }
            ],
            "premiums": [],
        }
        self.assertEqual(self._run(raw), ("YES", "50"))


class CoverTermsTests(unittest.TestCase):
    """_apply_cover_terms zeros od_term/tp_term for a cover not priced on the policy."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, **fields):
        sd = {k: _field(v) for k, v in fields.items()}
        sd.setdefault("od_term", _field("1"))
        sd.setdefault("tp_term", _field("1"))
        self.parser._apply_cover_terms(sd)
        return sd["od_term"]["value"], sd["tp_term"]["value"]

    def test_liability_only_zeros_od_term(self):
        # TP-only policy: OD premium 0 -> od_term 0; tp_term kept.
        self.assertEqual(
            self._run(
                basic_od_premium="0",
                od_premium="0",
                basic_tp_premium="16049",
                tp_premium="16149",
            ),
            ("0", "1"),
        )

    def test_saod_zeros_tp_term(self):
        # Standalone OD: TP premium 0 -> tp_term 0; od_term kept.
        self.assertEqual(
            self._run(
                basic_od_premium="458",
                od_premium="458",
                basic_tp_premium="0",
                tp_premium="0",
                od_term="1",
                tp_term="5",
            ),
            ("1", "0"),
        )

    def test_comprehensive_untouched(self):
        # Both covers priced -> both terms kept.
        self.assertEqual(
            self._run(
                basic_od_premium="9984",
                od_premium="9984",
                basic_tp_premium="2094",
                tp_premium="3024",
            ),
            ("1", "1"),
        )

    def test_only_basic_premium_present_counts(self):
        # OD priced only via basic_od_premium (od_premium blank) still counts as present.
        self.assertEqual(
            self._run(
                basic_od_premium="300",
                od_premium="",
                basic_tp_premium="0",
                tp_premium="0",
                od_term="1",
                tp_term="1",
            ),
            ("1", "0"),
        )


if __name__ == "__main__":
    unittest.main()
