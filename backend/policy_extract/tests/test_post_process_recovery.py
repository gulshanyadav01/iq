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


if __name__ == "__main__":
    unittest.main()
