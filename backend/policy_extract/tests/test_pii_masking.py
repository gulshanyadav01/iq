"""PII masking before the LLM — detection, token round-trip, and no-leak.

Masking the OCR markdown once replaces PII with reversible, space-free UPPERCASE
tokens that survive Stage-2 uppercasing and whitespace stripping, so a token
placed before Stage 1 round-trips into the schema value and is restored afterward.

Run from the backend container:
    python manage.py test policy_extract.tests.test_pii_masking -v 2
"""

import re

from django.test import SimpleTestCase

from policy_extract.services import pii_masking as pm

SAMPLE = (
    "Insured: Rohit Deshmukh  Mobile: 9920114477\n"
    "Email: rohit.d@example.in  PAN: ABCDE1234F\n"
    "Aadhaar: 1234 5678 9012\n"
    "Registration No: MH12XY7788\n"
    "Engine No: JF50E1234567  Chassis No: ME4JF505KMT012345\n"
    "Pincode: 411005\n"
    "Total Premium: Rs. 28,470\n"
)


class MaskMarkdownTests(SimpleTestCase):
    def test_structured_pii_is_tokenised_and_removed(self):
        masked, token_map = pm.mask_markdown(SAMPLE, engine="regex")
        # None of the raw PII values survive in the masked text.
        for leaked in [
            "9920114477",
            "rohit.d@example.in",
            "ABCDE1234F",
            "1234 5678 9012",
            "MH12XY7788",
            "JF50E1234567",
            "ME4JF505KMT012345",
        ]:
            self.assertNotIn(leaked, masked, f"{leaked} leaked into masked text")
        self.assertTrue(token_map)

    def test_premium_amount_not_masked(self):
        # The pincode detector is label-anchored, so a bare amount like 28,470
        # (and the "Rs." line) must not be tokenised.
        masked, _ = pm.mask_markdown(SAMPLE, engine="regex")
        self.assertIn("28,470", masked)

    def test_label_preserved_value_masked(self):
        masked, token_map = pm.mask_markdown(SAMPLE, engine="regex")
        self.assertIn("Engine No:", masked)
        self.assertIn("Chassis No:", masked)
        self.assertIn("411005", list(token_map.values()))

    def test_tokens_are_uppercase_and_spaceless(self):
        _, token_map = pm.mask_markdown(SAMPLE, engine="regex")
        for token in token_map:
            self.assertEqual(token, token.upper())
            self.assertNotIn(" ", token)
            self.assertRegex(token, r"^PII[A-Z]+\d{4,}$")

    def test_summary_counts_by_type(self):
        _, token_map = pm.mask_markdown(SAMPLE, engine="regex")
        summary = pm.build_summary(token_map, True)
        self.assertTrue(summary["enabled"])
        self.assertEqual(summary["masked_count"], len(token_map))
        self.assertIn("email", summary["types"])
        self.assertIn("mobile", summary["types"])


class RoundTripTests(SimpleTestCase):
    def test_token_round_trips_through_uppercase_and_space_strip(self):
        masked, token_map = pm.mask_markdown(SAMPLE, engine="regex")
        # Simulate the pipeline mutations: Stage 2 uppercases, _clean_whitespace
        # strips spaces from the reg field.
        reg_token = next(t for t, v in token_map.items() if v == "MH12XY7788")
        mutated = "".join(reg_token.upper().split())
        structured = {
            "reg_num": {"value": mutated},
            "mobileNo": {
                "value": next(
                    t for t, v in token_map.items() if v == "9920114477"
                ).upper()
            },
        }
        pm.unmask_result(structured, token_map)
        self.assertEqual(structured["reg_num"]["value"], "MH12XY7788")
        self.assertEqual(structured["mobileNo"]["value"], "9920114477")

    def test_token_embedded_in_larger_value_restored(self):
        _, token_map = pm.mask_markdown("Insured name: PLACEHOLDER", engine="regex")
        # Build a synthetic map + value where a token sits inside an address.
        token_map = {"PIINAME0001": "ROHIT DESHMUKH"}
        structured = {"cust_address": {"value": "C/O PIINAME0001, PUNE"}}
        pm.unmask_result(structured, token_map)
        self.assertEqual(
            structured["cust_address"]["value"], "C/O ROHIT DESHMUKH, PUNE"
        )

    def test_repeated_value_reuses_one_token(self):
        text = "Mobile: 9920114477 alt 9920114477"
        masked, token_map = pm.mask_markdown(text, engine="regex")
        tokens = re.findall(r"PIIMOBILE\d+", masked)
        self.assertEqual(len(set(tokens)), 1)

    def test_empty_input(self):
        masked, token_map = pm.mask_markdown("", engine="regex")
        self.assertEqual(masked, "")
        self.assertEqual(token_map, {})
