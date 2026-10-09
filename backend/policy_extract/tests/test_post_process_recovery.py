"""
Unit tests for the deterministic vehicle/identifier backfills in MineruGeminiParser:
_apply_fuel_type_fallback, _apply_vehicle_weight_fallback, _apply_wheels_fallback,
_apply_identifier_fallback.

Each is a pure dict->dict helper (no DB/SDK), so we exercise it on a bare instance
built via __new__ — bypassing __init__ (which constructs the genai.Client and the
DB-backed MasterDataResolver). Mirrors test_post_process_addons.py.

These recover fields that gemini-2.5-flash-lite captures at Stage 1 but intermittently
drops at Stage 2 (no response_schema, no default) — the fuel/engine/chassis drop
reproduced on the Go Digit EV commercial schedule PBP10707507.
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


class FuelTypeFallbackTests(unittest.TestCase):
    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, vehicles, current=""):
        sd = {"fuel_type": _field(current)}
        self.parser._apply_fuel_type_fallback(sd, {"vehicles": vehicles})
        return sd["fuel_type"]["value"], sd["fuel_type"]["is_extracted"]

    def test_backfills_from_fuel_type_key(self):
        # The reproduced PBP10707507 case: Stage 2 dropped an EV's fuel_type.
        self.assertEqual(self._run([{"fuel_type": "Electric"}]), ("ELECTRIC", "TRUE"))

    def test_backfills_from_bare_fuel_key(self):
        self.assertEqual(self._run([{"fuel": "diesel"}]), ("DIESEL", "TRUE"))

    def test_backfills_from_per_attribute_objects(self):
        veh = [{"reg_num": "MH12AB1234"}, {"fuel_type": "CNG", "context": "fuel"}]
        self.assertEqual(self._run(veh), ("CNG", "TRUE"))

    def test_infers_cng_from_make_model(self):
        self.assertEqual(
            self._run([{"make_model": "MARUTI ERTIGA VXI CNG"}]), ("CNG", "TRUE")
        )

    def test_does_not_overwrite_existing(self):
        self.assertEqual(
            self._run([{"fuel_type": "PETROL"}], current="DIESEL"), ("DIESEL", "TRUE")
        )

    def test_absent_stays_blank(self):
        self.assertEqual(self._run([{"make": "TATA"}]), ("", "TRUE"))


class VehicleWeightFallbackTests(unittest.TestCase):
    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, vehicles, current="0"):
        sd = {"vehicle_weight_value": _field(current)}
        self.parser._apply_vehicle_weight_fallback(sd, {"vehicles": vehicles})
        return sd["vehicle_weight_value"]["value"]

    def test_backfills_from_gross_vehicle_weight(self):
        self.assertEqual(self._run([{"gross_vehicle_weight": "702KG"}]), "702")

    def test_backfills_from_gvw_key(self):
        self.assertEqual(self._run([{"gvw": "3500 KG"}]), "3500")

    def test_backfills_from_per_attribute_objects(self):
        veh = [{"reg_num": "X"}, {"gross_vehicle_weight": "7520KG"}]
        self.assertEqual(self._run(veh), "7520")

    def test_does_not_overwrite_existing(self):
        self.assertEqual(self._run([{"gvw": "9999"}], current="702"), "702")

    def test_seats_wheels_cc_not_mistaken_for_weight(self):
        self.assertEqual(
            self._run([{"seating_capacity": 4, "wheels": 3, "cubic_capacity": 999}]),
            "0",
        )

    def test_absent_stays_zero(self):
        self.assertEqual(self._run([{"make": "X"}]), "0")


class WheelsFallbackTests(unittest.TestCase):
    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, vehicles, current="0"):
        sd = {"no_of_wheels": _field(current)}
        self.parser._apply_wheels_fallback(sd, {"vehicles": vehicles})
        return sd["no_of_wheels"]["value"]

    def test_backfills_three_wheeler(self):
        # The correctness win: a 3-wheeler's printed "3" must survive so the later
        # product-based guess (CV -> 4) does not overwrite it.
        self.assertEqual(self._run([{"wheels": 3}]), "3")

    def test_backfills_from_number_of_wheels_key(self):
        self.assertEqual(self._run([{"number_of_wheels": "6"}]), "6")

    def test_does_not_overwrite_existing(self):
        self.assertEqual(self._run([{"wheels": 4}], current="3"), "3")

    def test_seats_not_mistaken_for_wheels(self):
        self.assertEqual(self._run([{"seating_capacity": 4}]), "0")

    def test_absent_stays_zero(self):
        self.assertEqual(self._run([{"make": "X"}]), "0")


class IdentifierFallbackTests(unittest.TestCase):
    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, identifiers, engine="", chassis="", vehicles=None):
        sd = {"engineNo": _field(engine), "chassisNo": _field(chassis)}
        raw = {"identifiers": identifiers, "vehicles": vehicles or []}
        self.parser._apply_identifier_fallback(sd, raw)
        return sd["engineNo"]["value"], sd["chassisNo"]["value"]

    def test_backfills_engine_and_chassis_by_context(self):
        ids = [
            {"value": "E30WTE77776", "context": "engine number"},
            {"value": "MD2B73309TWE65229", "context": "chassis number"},
        ]
        self.assertEqual(self._run(ids), ("E30WTE77776", "MD2B73309TWE65229"))

    def test_backfills_from_vehicles_bucket(self):
        # The PBP10707507 case: Stage 1 put engine/chassis inside vehicles[], not
        # identifiers[], and Stage 2 dropped both -> recover from vehicles.
        veh = [
            {
                "make": "BAJAJ AUTO",
                "engine_number": "E30WTE77776",
                "chassis_number": "MD2B73309TWE65229",
            }
        ]
        self.assertEqual(
            self._run([], vehicles=veh), ("E30WTE77776", "MD2B73309TWE65229")
        )

    def test_identifiers_win_over_vehicles(self):
        # When both buckets carry a value, the context-tagged identifier is preferred.
        ids = [{"value": "IDENTENGINE", "context": "engine no"}]
        veh = [{"engine_number": "VEHENGINE"}]
        engine, _chassis = self._run(ids, vehicles=veh)
        self.assertEqual(engine, "IDENTENGINE")

    def test_chassis_from_vehicles_vin_key(self):
        veh = [{"vin": "MA1ZC2DE1HD6P01234"}]
        _engine, chassis = self._run([], vehicles=veh)
        self.assertEqual(chassis, "MA1ZC2DE1HD6P01234")

    def test_chassis_matches_vin_alias(self):
        ids = [{"value": "MA1ZC2DE1HD6P01234", "context": "VIN"}]
        _engine, chassis = self._run(ids)
        self.assertEqual(chassis, "MA1ZC2DE1HD6P01234")

    def test_strips_internal_whitespace(self):
        ids = [{"value": "E30 WTE 77776", "context": "engine no"}]
        engine, _chassis = self._run(ids)
        self.assertEqual(engine, "E30WTE77776")

    def test_does_not_overwrite_existing(self):
        ids = [{"value": "NEWENGINE", "context": "engine number"}]
        engine, _chassis = self._run(ids, engine="KEPTENGINE")
        self.assertEqual(engine, "KEPTENGINE")

    def test_absent_stays_blank(self):
        ids = [{"value": "D292417600", "context": "policy number"}]
        self.assertEqual(self._run(ids), ("", ""))


class MarkdownIdentifierFallbackTests(unittest.TestCase):
    """Last-resort recovery from the markdown when Stage 1 dropped engine/chassis
    from EVERY raw bucket (reproduced ~1/3 runs on PBP10707507)."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    # The vehicle-details row as MinerU actually renders it for PBP10707507.
    MD = (
        '<tr><td colspan="2">Engine No.</td><td>E30WTE77776</td>'
        '<td colspan="2">Chassis No. MD2B73309TWE65229</td>'
        "<td>Power</td><td>6.2 KW</td></tr>"
    )

    def _run(self, markdown, engine="", chassis=""):
        sd = {"engineNo": _field(engine), "chassisNo": _field(chassis)}
        self.parser._apply_markdown_identifier_fallback(sd, markdown)
        return sd["engineNo"]["value"], sd["chassisNo"]["value"]

    def test_recovers_both_from_markdown(self):
        self.assertEqual(self._run(self.MD), ("E30WTE77776", "MD2B73309TWE65229"))

    def test_recovers_chassis_from_vin_label(self):
        md = "VIN : MA1ZC2DE1HD6P01234 Power 60 KW"
        _engine, chassis = self._run(md)
        self.assertEqual(chassis, "MA1ZC2DE1HD6P01234")

    def test_does_not_overwrite_existing(self):
        engine, chassis = self._run(self.MD, engine="KEPT", chassis="KEPTCHASSIS")
        self.assertEqual((engine, chassis), ("KEPT", "KEPTCHASSIS"))

    def test_no_markdown_is_noop(self):
        self.assertEqual(self._run(""), ("", ""))

    def test_label_without_value_stays_blank(self):
        # "Engine Number" prose with no following identifier token -> no false capture.
        md = "Please quote the Engine Number when contacting support."
        self.assertEqual(self._run(md), ("", ""))


class BodyTypeClassificationTests(unittest.TestCase):
    """_apply_body_type_classification reclassifies a commercial vehicle the lite
    model mislabelled as Two-Wheeler/Car, using the authoritative Vehicle Body Type."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, body, product="TW", vclass="Two Wheeler", subclass="Scooter"):
        sd = {
            "product_id": _field(product),
            "VehicleClass": _field(vclass),
            "VehicleSubClass": _field(subclass),
        }
        self.parser._apply_body_type_classification(
            sd, {"vehicles": [{"vehicle_body_type": body}]}
        )
        return (
            sd["product_id"]["value"],
            sd["VehicleClass"]["value"],
            sd["VehicleSubClass"]["value"],
        )

    def test_pcv_3w_reclassified_from_two_wheeler(self):
        # The PBP10707507 case: 'PCV -3W' -> CV/PCV, bogus 'Scooter' subclass cleared.
        self.assertEqual(self._run("PCV -3W"), ("CV", "PCV", ""))

    def test_gcv_reclassified(self):
        self.assertEqual(self._run("GCV", subclass="Truck"), ("CV", "GCV", "Truck"))

    def test_bus_reclassified(self):
        self.assertEqual(self._run("Passenger Bus 3W", subclass=""), ("CV", "BUS", ""))

    def test_private_car_body_not_touched(self):
        # A genuine private car (body 'SEDAN') must NOT be promoted to CV.
        self.assertEqual(
            self._run(
                "SEDAN", product="Car", vclass="Private Car", subclass="Private Car"
            ),
            ("Car", "Private Car", "Private Car"),
        )

    def test_already_cv_not_reflagged(self):
        # Body says CV and the model already said CV -> no change.
        out = self._run("PCV -3W", product="CV", vclass="PCV", subclass="Auto Rickshaw")
        self.assertEqual(out, ("CV", "PCV", "Auto Rickshaw"))

    def test_no_body_type_is_noop(self):
        sd = {"product_id": _field("TW"), "VehicleClass": _field("Two Wheeler")}
        self.parser._apply_body_type_classification(sd, {"vehicles": [{"make": "X"}]})
        self.assertEqual(sd["product_id"]["value"], "TW")


class MarkdownContactFallbackTests(unittest.TestCase):
    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, markdown, mobile="", email=""):
        sd = {"mobileNo": _field(mobile), "email": _field(email)}
        self.parser._apply_markdown_contact_fallback(sd, markdown)
        return sd["mobileNo"]["value"], sd["email"]["value"]

    MD = (
        "<td>Mobile</td><td>xxxxxxxxx8575</td>"
        "<td>Partner Mobile No.</td><td>--</td>"
        "<td>Email</td><td>bxxxxxxxxxxy@axxxxxxxnet</td>"
        "<td>Partner Email</td><td>customerservice@pbpartners.com</td>"
        " For instant resolution email hello@godigit.com"
    )

    def test_recovers_masked_mobile(self):
        mob, _email = self._run(self.MD)
        self.assertEqual(mob, "xxxxxxxxx8575")

    def test_recovers_customer_email_not_broker(self):
        _mob, email = self._run(self.MD)
        self.assertEqual(email, "BXXXXXXXXXXY@AXXXXXXXNET")

    def test_overrides_broker_email(self):
        # Stage 2 wrongly filled the insurer footer email -> corrected to customer.
        _mob, email = self._run(self.MD, email="hello@godigit.com")
        self.assertEqual(email, "BXXXXXXXXXXY@AXXXXXXXNET")

    def test_keeps_genuine_customer_email(self):
        _mob, email = self._run(self.MD, email="ROHIT@GMAIL.COM")
        self.assertEqual(email, "ROHIT@GMAIL.COM")

    def test_does_not_overwrite_existing_mobile(self):
        mob, _email = self._run(self.MD, mobile="9998887776")
        self.assertEqual(mob, "9998887776")

    def test_no_markdown_is_noop(self):
        self.assertEqual(self._run(""), ("", ""))

    def test_strips_email_label_bleed(self):
        # MinerU glues the next 'Email' label onto the address with no separator.
        md = "Email ixxxxe@vxxxxxxxxxxxxxx.inEmail Partner Email cs@pbpartners.com"
        _mob, email = self._run(md)
        self.assertEqual(email, "IXXXXE@VXXXXXXXXXXXXXX.IN")


class FinancierBoilerplateTests(unittest.TestCase):
    """_apply_financier rejects document/product boilerplate the LLM grabs from the
    header (e.g. 'CAR POLICY') — a financier is a bank/NBFC, not a policy label."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, current, raw=None):
        sd = {"financialInstitutionName": _field(current)}
        self.parser._apply_financier(sd, raw or {})
        return sd["financialInstitutionName"]["value"]

    def test_clears_car_policy_boilerplate(self):
        self.assertEqual(self._run("CAR POLICY"), "")

    def test_clears_private_car_policy(self):
        self.assertEqual(self._run("Digit Private Car Policy"), "")

    def test_keeps_real_bank(self):
        self.assertEqual(self._run("BANK OF BARODA RAMPUR"), "BANK OF BARODA RAMPUR")

    def test_keeps_nbfc_with_ltd(self):
        self.assertEqual(self._run("BAJAJ AUTO CREDIT LTD"), "BAJAJ AUTO CREDIT LTD")

    def test_boilerplate_then_backfills_from_raw(self):
        raw = {"participants": [{"value": "HDFC BANK LTD", "context": "financier"}]}
        self.assertEqual(self._run("CAR POLICY", raw), "HDFC BANK LTD")


class OdTpDatesFallbackTests(unittest.TestCase):
    """_apply_raw_date_fallbacks recovers OD/TP cover dates from the raw 'dates'
    bucket when Stage 2 dropped or blanked them — the reported "TP/OD dates always
    empty" failure. Values stay dd/mm/yyyy (the DATE_FIELDS block converts later)."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    @staticmethod
    def _mk(value):
        # A genuinely-unextracted field is is_extracted=FALSE; a populated one TRUE.
        return {
            "id": "",
            "value": value,
            "confidence_score": 1.0 if value else 0.0,
            "is_extracted": "TRUE" if value else "FALSE",
            "do_verification": "FALSE",
        }

    def _run(self, dates, current=None):
        current = current or {}
        sd = {
            k: self._mk(current.get(k, ""))
            for k in ("od_startdate", "od_enddate", "tpStartDate", "tpEndDate")
        }
        self.parser._apply_raw_date_fallbacks(sd, {"dates": dates})
        return {k: (sd[k]["value"], sd[k]["is_extracted"]) for k in sd}

    def test_recovers_all_four_from_context(self):
        dates = [
            {"value": "01/04/2024", "context": "OD Start Date"},
            {"value": "31/03/2025", "context": "OD End Date"},
            {"value": "01/04/2024", "context": "TP Start Date"},
            {"value": "31/03/2027", "context": "TP End Date"},
        ]
        out = self._run(dates)
        self.assertEqual(out["od_startdate"], ("01/04/2024", "TRUE"))
        self.assertEqual(out["od_enddate"], ("31/03/2025", "TRUE"))
        self.assertEqual(out["tpStartDate"], ("01/04/2024", "TRUE"))
        self.assertEqual(out["tpEndDate"], ("31/03/2027", "TRUE"))

    def test_matches_spelled_out_cover_names(self):
        dates = [
            {"value": "10/05/2024", "context": "Own Damage From"},
            {"value": "09/05/2025", "context": "Own Damage To"},
            {"value": "10/05/2024", "context": "Third Party From"},
            {"value": "09/05/2027", "context": "Third Party To"},
        ]
        out = self._run(dates)
        self.assertEqual(out["od_startdate"][0], "10/05/2024")
        self.assertEqual(out["od_enddate"][0], "09/05/2025")
        self.assertEqual(out["tpStartDate"][0], "10/05/2024")
        self.assertEqual(out["tpEndDate"][0], "09/05/2027")

    def test_does_not_overwrite_existing(self):
        dates = [{"value": "01/01/2024", "context": "OD Start"}]
        out = self._run(dates, current={"od_startdate": "15/06/2024"})
        self.assertEqual(out["od_startdate"], ("15/06/2024", "TRUE"))

    def test_absent_stays_blank(self):
        out = self._run([{"value": "01/04/2024", "context": "Policy Start Date"}])
        self.assertEqual(out["od_startdate"], ("", "FALSE"))
        self.assertEqual(out["tpEndDate"], ("", "FALSE"))

    def test_no_dates_bucket_is_safe(self):
        sd = {"od_startdate": _field("")}
        self.parser._apply_raw_date_fallbacks(sd, {})
        self.assertEqual(sd["od_startdate"]["value"], "")

    def test_recovers_previous_policy_end_date(self):
        # Its absence is what blanks the derived stp_nstp — recover it from context.
        sd = {"previous_policy_end_date": self._mk("")}
        dates = [
            {"value": "31/03/2024", "context": "Previous Policy Expiry"},
            {"value": "01/04/2024", "context": "Policy Start Date"},
        ]
        self.parser._apply_raw_date_fallbacks(sd, {"dates": dates})
        self.assertEqual(
            (
                sd["previous_policy_end_date"]["value"],
                sd["previous_policy_end_date"]["is_extracted"],
            ),
            ("31/03/2024", "TRUE"),
        )

    def test_prev_policy_end_not_confused_with_current_start(self):
        # Only a "previous policy" context feeds it; a plain policy start must not.
        sd = {"previous_policy_end_date": self._mk("")}
        dates = [{"value": "01/04/2024", "context": "Policy Start Date"}]
        self.parser._apply_raw_date_fallbacks(sd, {"dates": dates})
        self.assertEqual(sd["previous_policy_end_date"]["value"], "")


class PackageCoverDateFallbackTests(unittest.TestCase):
    """_apply_package_cover_date_fallback fills blank OD/TP cover dates from the
    policy period when that cover is priced — the reported "comprehensive Go Digit
    CV leaves cover dates blank" failure (PBP10799078), where the document prints
    one combined "Period of Policy" and no separate OD/TP label, so the context-keyed
    _apply_raw_date_fallbacks finds nothing. The OD cover is always the annual policy
    period, so OD is copied unconditionally. TP is copied too, but guarded: when
    tp_term > 1 the policy is a long-term bundle (1yr OD + 3/5yr TP) whose TP period
    differs from the 1-year policy period, so TP is left to Stage 1/2 rather than
    guessed wrong."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    @staticmethod
    def _mk(value):
        return {
            "id": "",
            "value": value,
            "confidence_score": 1.0 if value else 0.0,
            "is_extracted": "TRUE" if value else "FALSE",
            "do_verification": "FALSE",
        }

    def _run(
        self,
        *,
        od_premium="0",
        tp_premium="0",
        tp_term="",
        dates=None,
        policy=True,
        policy_end=None,
    ):
        current = dates or {}
        sd = {
            k: self._mk(current.get(k, ""))
            for k in ("od_startdate", "od_enddate", "tpStartDate", "tpEndDate")
        }
        sd["policy_start_date"] = self._mk("01/10/2026" if policy else "")
        if policy_end is not None:
            sd["policy_end_date"] = self._mk(policy_end)
        else:
            sd["policy_end_date"] = self._mk("30/09/2027" if policy else "")
        sd["od_premium"] = self._mk(od_premium)
        sd["tp_premium"] = self._mk(tp_premium)
        sd["tp_term"] = self._mk(tp_term)
        self.parser._apply_package_cover_date_fallback(sd)
        return {
            k: (sd[k]["value"], sd[k]["is_extracted"])
            for k in ("od_startdate", "od_enddate", "tpStartDate", "tpEndDate")
        }

    def test_comprehensive_fills_both(self):
        # PBP10799078: OD 4134.37 and TP 7267 both priced, blank OD/TP dates, annual
        # tp_term -> BOTH OD and TP backfilled from the policy period.
        out = self._run(od_premium="4134.37", tp_premium="7267")
        self.assertEqual(out["od_startdate"], ("01/10/2026", "TRUE"))
        self.assertEqual(out["od_enddate"], ("30/09/2027", "TRUE"))
        self.assertEqual(out["tpStartDate"], ("01/10/2026", "TRUE"))
        self.assertEqual(out["tpEndDate"], ("30/09/2027", "TRUE"))

    def test_liability_only_fills_tp(self):
        # No OD cover (od_premium 0) -> OD stays blank; TP is priced with an annual
        # tp_term, so TP is backfilled from the policy period.
        out = self._run(od_premium="0", tp_premium="7267")
        self.assertEqual(out["od_startdate"], ("", "FALSE"))
        self.assertEqual(out["od_enddate"], ("", "FALSE"))
        self.assertEqual(out["tpStartDate"], ("01/10/2026", "TRUE"))
        self.assertEqual(out["tpEndDate"], ("30/09/2027", "TRUE"))

    def test_saod_fills_od(self):
        out = self._run(od_premium="4134.37", tp_premium="0")
        self.assertEqual(out["od_startdate"], ("01/10/2026", "TRUE"))
        self.assertEqual(out["od_enddate"], ("30/09/2027", "TRUE"))
        self.assertEqual(out["tpStartDate"], ("", "FALSE"))
        self.assertEqual(out["tpEndDate"], ("", "FALSE"))

    def test_basic_od_premium_counts_as_priced(self):
        sd = {
            k: self._mk("")
            for k in ("od_startdate", "od_enddate", "tpStartDate", "tpEndDate")
        }
        sd["policy_start_date"] = self._mk("01/10/2026")
        sd["policy_end_date"] = self._mk("30/09/2027")
        sd["basic_od_premium"] = self._mk("4134.37")
        self.parser._apply_package_cover_date_fallback(sd)
        self.assertEqual(sd["od_startdate"]["value"], "01/10/2026")
        self.assertEqual(sd["od_enddate"]["value"], "30/09/2027")

    def test_annual_tp_backfilled(self):
        # Both priced, annual tp_term (blank) -> TP backfilled alongside OD.
        out = self._run(od_premium="806", tp_premium="4351")
        self.assertEqual(out["od_startdate"], ("01/10/2026", "TRUE"))
        self.assertEqual(out["od_enddate"], ("30/09/2027", "TRUE"))
        self.assertEqual(out["tpStartDate"], ("01/10/2026", "TRUE"))
        self.assertEqual(out["tpEndDate"], ("30/09/2027", "TRUE"))

    def test_long_term_tp_bundle_not_backfilled(self):
        # 1yr-OD + 3yr-TP bundle: tp_term 3 means TP runs longer than the printed
        # policy period, so TP dates are left to Stage 1/2; only OD is backfilled.
        out = self._run(od_premium="806", tp_premium="4351", tp_term="3")
        self.assertEqual(out["od_startdate"], ("01/10/2026", "TRUE"))
        self.assertEqual(out["od_enddate"], ("30/09/2027", "TRUE"))
        self.assertEqual(out["tpStartDate"], ("", "FALSE"))
        self.assertEqual(out["tpEndDate"], ("", "FALSE"))

    def test_does_not_overwrite_existing_od_start(self):
        # An OD start already extracted (explicit label) is kept; only the blank
        # OD end is backfilled. TP is left alone here: an explicit tpEndDate is
        # already present, so the pair is not both-blank and TP is not backfilled.
        out = self._run(
            od_premium="4134.37",
            tp_premium="7267",
            dates={"od_startdate": "15/06/2026", "tpEndDate": "14/06/2029"},
        )
        self.assertEqual(out["od_startdate"], ("15/06/2026", "TRUE"))
        self.assertEqual(out["od_enddate"], ("30/09/2027", "TRUE"))
        self.assertEqual(out["tpEndDate"], ("14/06/2029", "TRUE"))
        self.assertEqual(out["tpStartDate"], ("", "FALSE"))

    def test_blank_policy_period_fills_nothing(self):
        out = self._run(od_premium="4134.37", tp_premium="7267", policy=False)
        self.assertEqual(out["od_startdate"], ("", "FALSE"))
        self.assertEqual(out["od_enddate"], ("", "FALSE"))

    def test_atomic_requires_both_policy_dates(self):
        # Atomic: a lone policy_start with no policy_end fills nothing, so OD never
        # ends up with a start but no end.
        out = self._run(od_premium="4134.37", tp_premium="7267", policy_end="")
        self.assertEqual(out["od_startdate"], ("", "FALSE"))
        self.assertEqual(out["od_enddate"], ("", "FALSE"))


class TermDerivationDateFormatTests(unittest.TestCase):
    """_derive_terms_from_dates must parse the dd/mm/yyyy the DATE_FIELDS block
    emits — regression for the %m/%d/%Y bug that silently failed (ValueError)
    whenever a day > 12, leaving terms at their default."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    @staticmethod
    def _mk(value):
        return {
            "id": "",
            "value": value,
            "confidence_score": 1.0 if value else 0.0,
            "is_extracted": "TRUE" if value else "FALSE",
            "do_verification": "FALSE",
        }

    def _terms(self, start, end):
        # Seed the term fields to a sentinel ("9") that differs from any derived
        # value, so the assertions below only hold once the dd/mm/yyyy parse
        # succeeds. Under the old %m/%d/%Y code an end day > 12 raises ValueError
        # and the term is left at "9" — making these genuine red-at-HEAD tests.
        sd = {
            "policy_start_date": self._mk(start),
            "policy_end_date": self._mk(end),
            "od_startdate": self._mk(start),
            "od_enddate": self._mk(end),
            "tpStartDate": self._mk(start),
            "tpEndDate": self._mk(end),
            "policy_term": self._mk("9"),
            "od_term": self._mk("9"),
            "tp_term": self._mk("9"),
        }
        self.parser._derive_terms_from_dates(sd)
        return sd

    def test_one_year_term_with_end_day_gt_12(self):
        # 01/10/2026 -> 30/09/2027, end day 30 > 12 would crash the old %m/%d parse.
        sd = self._terms("01/10/2026", "30/09/2027")
        self.assertEqual(sd["policy_term"]["value"], "1")
        self.assertEqual(sd["od_term"]["value"], "1")
        self.assertEqual(sd["tp_term"]["value"], "1")

    def test_three_year_tp_term(self):
        # A long-term TP span (start/end days both > 12) derives to 3 years.
        sd = self._terms("25/09/2024", "24/09/2027")
        self.assertEqual(sd["tp_term"]["value"], "3")

    def test_blank_dates_leave_default(self):
        sd = {
            "policy_start_date": self._mk(""),
            "policy_end_date": self._mk(""),
            "policy_term": self._mk("1"),
        }
        self.parser._derive_terms_from_dates(sd)
        self.assertEqual(sd["policy_term"]["value"], "1")

    def test_non_positive_span_leaves_term_untouched(self):
        # end == start (0-day span) -> term_years rounds to 0, the `> 0` guard
        # fires, and the seeded term is preserved rather than zeroed.
        sd = self._terms("01/10/2026", "01/10/2026")
        self.assertEqual(sd["policy_term"]["value"], "9")
        self.assertEqual(sd["tp_term"]["value"], "9")

    def test_unparsable_slash_date_leaves_term_untouched(self):
        # A slash-containing but invalid date raises ValueError in strptime; the
        # except swallows it and the seeded term is left unchanged.
        sd = self._terms("01/10/2026", "45/13/2027")
        self.assertEqual(sd["policy_term"]["value"], "9")
        self.assertEqual(sd["tp_term"]["value"], "9")


class EnsureSchemaFieldsTests(unittest.TestCase):
    """_ensure_schema_fields guarantees every schema key is present after mapping,
    so a Stage-2 drop never surfaces as an absent field downstream."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def test_backfills_missing_key_with_false_default(self):
        sd = {}
        self.parser._ensure_schema_fields(sd)
        schema_keys = self.parser._get_output_schema()["properties"].keys()
        # every schema field now present
        self.assertEqual(set(schema_keys) - set(sd), set())
        od = sd["od_startdate"]
        self.assertEqual(od["is_extracted"], "FALSE")
        self.assertEqual(od["value"], "")
        # a field with a schema default gets that default
        self.assertEqual(sd["cgst"]["value"], "0")
        # pay_term now carries an explicit default of "1" (was blank before)
        self.assertEqual(sd["pay_term"]["value"], "1")

    def test_does_not_overwrite_present_field(self):
        sd = {"od_startdate": _field("15/06/2024")}
        self.parser._ensure_schema_fields(sd)
        self.assertEqual(sd["od_startdate"]["value"], "15/06/2024")
        self.assertEqual(sd["od_startdate"]["is_extracted"], "TRUE")

    def test_replaces_non_dict_value(self):
        sd = {"policy_number": "POL123"}  # bare string, wrong shape
        self.parser._ensure_schema_fields(sd)
        self.assertIsInstance(sd["policy_number"], dict)
        self.assertEqual(sd["policy_number"]["is_extracted"], "FALSE")


class Stage2ResponseSchemaTests(unittest.TestCase):
    """_get_stage2_response_json_schema forces every field present, derived from the
    single field source (_get_output_schema)."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def test_all_fields_required_and_object_shaped(self):
        schema = self.parser._get_stage2_response_json_schema()
        field_names = list(self.parser._get_output_schema()["properties"].keys())
        self.assertEqual(schema["type"], "object")
        self.assertEqual(set(schema["required"]), set(field_names))
        self.assertEqual(set(schema["properties"]), set(field_names))
        one = schema["properties"]["od_startdate"]
        self.assertEqual(one["type"], "object")
        self.assertEqual(
            set(one["required"]),
            {"id", "value", "confidence_score", "is_extracted", "do_verification"},
        )


class PincodeFromAddressTests(unittest.TestCase):
    """_apply_pincode_from_address takes the LAST PIN in cust_address (the
    trailing city/branch PIN), falling back to the only PIN when just one is present."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, address, current_pin=""):
        sd = {
            "cust_address": _field(address),
            "pinCode": _field(current_pin),
        }
        self.parser._apply_pincode_from_address(sd)
        return sd["pinCode"]["value"], sd["pinCode"]["is_extracted"]

    def test_last_pin_wins_over_leading(self):
        # Two PINs: leading 244901, trailing 110001 — take the last one.
        addr = "H.No 12, Civil Lines, Moradabad 244901, c/o Delhi Office 110001"
        self.assertEqual(self._run(addr, current_pin="244901"), ("110001", "TRUE"))

    def test_sets_when_model_blank(self):
        self.assertEqual(
            self._run("Sector 5, Noida 201301", current_pin=""), ("201301", "TRUE")
        )

    def test_already_last_is_noop(self):
        addr = "Moradabad 244901, Delhi 110001"
        # Already correct — value unchanged (no spurious rewrite).
        self.assertEqual(self._run(addr, current_pin="110001")[0], "110001")

    def test_no_pin_in_address_keeps_existing(self):
        # PIN came from a labelled field, not the address text — leave it.
        self.assertEqual(
            self._run("Civil Lines, Moradabad", current_pin="244901"),
            ("244901", "TRUE"),
        )

    def test_ignores_phone_and_leading_zero(self):
        # 10-digit phone yields no 6-digit hit; a leading-zero run is not a PIN.
        addr = "Contact 9876543210, Zone 012345, Gate No 4"
        # No PIN found in the address → pinCode value left blank (unchanged).
        self.assertEqual(self._run(addr, current_pin="")[0], "")

    def test_empty_address_is_safe(self):
        self.assertEqual(self._run("", current_pin="500001"), ("500001", "TRUE"))


class IntermediaryCodeFallbackTests(unittest.TestCase):
    """_apply_intermediary_code_fallback recovers the broker/agent/IMD code from the
    raw participants/identifiers/entities buckets when Stage 2 dropped it."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, raw, current=""):
        sd = {"intermediary_code": _field(current)}
        self.parser._apply_intermediary_code_fallback(sd, raw)
        return sd["intermediary_code"]["value"], sd["intermediary_code"]["is_extracted"]

    def test_recovers_from_participants_by_context(self):
        raw = {
            "participants": [{"value": "IMD0001234", "context": "Intermediary Code"}]
        }
        self.assertEqual(self._run(raw), ("IMD0001234", "TRUE"))

    def test_recovers_from_identifiers_broker_code(self):
        raw = {"identifiers": [{"value": "AG-1122", "context": "Broker Code"}]}
        self.assertEqual(self._run(raw), ("AG-1122", "TRUE"))

    def test_recovers_agent_code_from_entities(self):
        raw = {"entities": [{"value": "70003456", "context": "Agent Code"}]}
        self.assertEqual(self._run(raw), ("70003456", "TRUE"))

    def test_strips_internal_whitespace(self):
        raw = {"identifiers": [{"value": "IMD 000 1234", "context": "IMD No"}]}
        self.assertEqual(self._run(raw)[0], "IMD0001234")

    def test_does_not_overwrite_existing(self):
        raw = {"participants": [{"value": "NEWCODE", "context": "intermediary code"}]}
        self.assertEqual(self._run(raw, current="KEPT123"), ("KEPT123", "TRUE"))

    def test_ignores_unrelated_context(self):
        # A broker NAME context must not populate the CODE field → left blank.
        raw = {
            "participants": [
                {"value": "STAR INSURANCE BROKERS", "context": "intermediary name"}
            ]
        }
        self.assertEqual(self._run(raw)[0], "")

    def test_no_raw_data_is_safe(self):
        self.assertEqual(self._run({})[0], "")


class PreviousPolicyNumberFallbackTests(unittest.TestCase):
    """_apply_previous_policy_number_fallback recovers the prior policy number from raw
    identifiers, without copying the current policy_number."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, identifiers, current_prev="", policy_number=""):
        sd = {
            "previous_policy_number": _field(current_prev),
            "policy_number": _field(policy_number),
        }
        self.parser._apply_previous_policy_number_fallback(
            sd, {"identifiers": identifiers}
        )
        return sd["previous_policy_number"]["value"]

    def test_recovers_by_context(self):
        ids = [{"value": "OG-23-1102-9999", "context": "previous_policy_number"}]
        self.assertEqual(self._run(ids), "OG-23-1102-9999")

    def test_recovers_expiring_policy_label(self):
        ids = [{"value": "PREV12345", "context": "Previous Policy No"}]
        self.assertEqual(self._run(ids), "PREV12345")

    def test_does_not_copy_current_policy_number(self):
        ids = [{"value": "CUR-100", "context": "previous policy"}]
        # Same value as the current policy number → must not be used.
        self.assertEqual(self._run(ids, policy_number="CUR-100"), "")

    def test_does_not_overwrite_existing(self):
        ids = [{"value": "NEWPREV", "context": "previous_policy_number"}]
        self.assertEqual(self._run(ids, current_prev="KEPTPREV"), "KEPTPREV")

    def test_absent_stays_blank(self):
        ids = [{"value": "POL999", "context": "policy number"}]
        self.assertEqual(self._run(ids), "")


class BranchFallbackTests(unittest.TestCase):
    """_apply_branch_fallback recovers the issuing/servicing branch from entities/locations."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, raw, current=""):
        sd = {"branch": _field(current)}
        self.parser._apply_branch_fallback(sd, raw)
        return sd["branch"]["value"]

    def test_recovers_from_entities(self):
        raw = {"entities": [{"value": "Andheri Branch", "context": "branch"}]}
        self.assertEqual(self._run(raw), "Andheri Branch")

    def test_recovers_from_locations_issuing_office(self):
        raw = {"locations": [{"value": "Mumbai DO", "context": "Issuing Office"}]}
        self.assertEqual(self._run(raw), "Mumbai DO")

    def test_does_not_overwrite_existing(self):
        raw = {"entities": [{"value": "NewBranch", "context": "branch"}]}
        self.assertEqual(self._run(raw, current="KeptBranch"), "KeptBranch")

    def test_absent_stays_blank(self):
        raw = {"entities": [{"value": "ACME INSURANCE", "context": "insurer"}]}
        self.assertEqual(self._run(raw), "")


class BusinessTypeTests(unittest.TestCase):
    """_apply_business_type normalizes to the NEW/RENEWAL/ROLLOVER enum and infers the
    value from previous-policy signals when the model left it blank."""

    def setUp(self):
        self.parser = MineruGeminiParser.__new__(MineruGeminiParser)

    def _run(self, current="", prev_num="", prev_end=""):
        sd = {
            "business_type": _field(current),
            "previous_policy_number": _field(prev_num),
            "previous_policy_end_date": _field(prev_end),
        }
        self.parser._apply_business_type(sd)
        b = sd["business_type"]
        return b["value"], b["is_extracted"], b["do_verification"]

    def test_normalizes_renewal(self):
        self.assertEqual(self._run(current="Renewal")[0], "RENEWAL")

    def test_normalizes_rollover_variant(self):
        self.assertEqual(self._run(current="Roll Over")[0], "ROLLOVER")

    def test_normalizes_new_business(self):
        self.assertEqual(self._run(current="New Business")[0], "NEW")

    def test_infers_renewal_from_prev_number(self):
        # Blank → inferred RENEWAL, flagged for verification.
        self.assertEqual(self._run(prev_num="PREV123"), ("RENEWAL", "TRUE", "TRUE"))

    def test_infers_renewal_from_prev_end_date(self):
        self.assertEqual(self._run(prev_end="31/03/2024")[0], "RENEWAL")

    def test_infers_new_when_no_previous(self):
        self.assertEqual(self._run(), ("NEW", "TRUE", "TRUE"))


if __name__ == "__main__":
    unittest.main()
