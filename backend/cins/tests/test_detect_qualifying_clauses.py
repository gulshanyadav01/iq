"""
Regression tests for detect_qualifying_clauses — the host-side helper
injected into the Pass 3 codegen sandbox.

The codegen path emits machine-ingestable qualifying_clauses by calling
this helper instead of writing its own regex (the sandbox forbids
`import re`). The clause catalogue is centralised here so extending it
is a one-line change in host code, not a per-document codegen rewrite.

If you add a new clause shape, add a regex above and a builder block in
detect_qualifying_clauses — then add a test here.

Run from the backend container:
    python manage.py test cins.tests.test_detect_qualifying_clauses -v 2
"""

from unittest import TestCase

from cins.processors.core.code_gen_executor import detect_qualifying_clauses


CHOLA_BLOB = (
    "Please note that the above pay-out grid -  "
    "For Private Car Package policy pay-out  is on OD Cover Premium and "
    "Private Car Liability policy  Pay-out is on TP/PA Premium -  "
    "For Private Car Package,SAOD & Liability policy CPA should be "
    "collected for individual Name Mandatorily if CPA is not collected "
    "for individual name then 1.5% of OD Pay-out will be deducted -  "
    "For Private Car Electric Vehicle or LPG/CNG pay-out grid is same "
    "as Private Car Petrol grid"
)


def _by_type(clauses, ctype):
    return [c for c in clauses if c["clause_type"] == ctype]


class DetectQualifyingClausesTests(TestCase):
    def test_empty_input_returns_empty_list(self):
        self.assertEqual(detect_qualifying_clauses(""), [])
        self.assertEqual(detect_qualifying_clauses(None), [])
        self.assertEqual(detect_qualifying_clauses("   \n\t "), [])

    def test_no_matching_clauses_returns_empty(self):
        self.assertEqual(
            detect_qualifying_clauses("Just a generic note with no known clause shape."),
            [],
        )

    def test_chola_blob_detects_all_five_clauses(self):
        clauses = detect_qualifying_clauses(CHOLA_BLOB, provider="Chola")
        clause_types = [c["clause_type"] for c in clauses]
        self.assertIn("applied_on_override", clause_types)
        self.assertIn("conditional_deduction", clause_types)
        self.assertIn("mandatory_requirement", clause_types)
        self.assertIn("grid_alias", clause_types)
        # Two applied_on_override clauses (Package + Liability)
        self.assertEqual(len(_by_type(clauses, "applied_on_override")), 2)

    def test_conditional_deduction_payload_shape(self):
        clauses = detect_qualifying_clauses(CHOLA_BLOB)
        deductions = _by_type(clauses, "conditional_deduction")
        self.assertEqual(len(deductions), 1)
        d = deductions[0]
        self.assertEqual(d["when"]["cpa"], "without-cpa")
        self.assertEqual(d["when"]["school_bus_type"], "on-individual-name")
        self.assertEqual(d["effect"]["deduction"]["value"], 1.5)
        self.assertEqual(d["effect"]["deduction"]["value_type"], "percentage")
        self.assertEqual(d["effect"]["deduction"]["applied_on"], "OD Pay-out")

    def test_conditional_deduction_parses_arbitrary_percentage(self):
        text = (
            "if CPA is not collected for individual name then 2.5% of "
            "OD Pay-out will be deducted"
        )
        clauses = detect_qualifying_clauses(text)
        deductions = _by_type(clauses, "conditional_deduction")
        self.assertEqual(len(deductions), 1)
        self.assertEqual(deductions[0]["effect"]["deduction"]["value"], 2.5)

    def test_conditional_deduction_net_payout_basis(self):
        # Commercial-Vehicle grids word the footnote "Net Payout", not
        # "OD Pay-out". Regression: the regex used to require "OD Pay-out"
        # literally, so CV deductions were silently dropped.
        text = (
            "For CV Package & Liability policy CPA should be collected for "
            "individual Name Mandatorily if CPA is not collected for "
            "individual name then 1.5% of Net Payout will be deducted"
        )
        deductions = _by_type(detect_qualifying_clauses(text), "conditional_deduction")
        self.assertEqual(len(deductions), 1)
        self.assertEqual(deductions[0]["when"]["cpa"], "without-cpa")
        self.assertEqual(deductions[0]["effect"]["deduction"]["value"], 1.5)
        self.assertEqual(deductions[0]["effect"]["deduction"]["applied_on"], "Net Payout")

    def test_conditional_deduction_real_cv_blob(self):
        # The exact other_conditions string persisted on Chola CV rules.
        text = (
            "Please note that the above payout grid -  For Commercial Vehicle "
            "Package/Liability policy is on Net Premium -  For CV Package & "
            "Liability policy CPA should be collected for individual Name "
            "Mandatorily if CPA is not collected for individual name then "
            "1.5% of Net Payout will be deducted -  GCCV EV Exceeding 3.5 "
            "Tonnage & Passanger Carrying EV (except eAUTO) is declined"
        )
        deductions = _by_type(detect_qualifying_clauses(text), "conditional_deduction")
        self.assertEqual(len(deductions), 1)
        self.assertEqual(deductions[0]["effect"]["deduction"]["value"], 1.5)

    def test_applied_on_override_package(self):
        text = "Private Car Package policy pay-out is on OD Cover Premium"
        clauses = detect_qualifying_clauses(text)
        package = _by_type(clauses, "applied_on_override")
        self.assertEqual(len(package), 1)
        self.assertEqual(package[0]["effect"]["applied_on"], "OD Cover Premium")
        self.assertEqual(package[0]["when"]["coverage_type_text"], "comprehensive")

    def test_applied_on_override_liability(self):
        text = "Private Car Liability policy Pay-out is on TP/PA Premium"
        clauses = detect_qualifying_clauses(text)
        liability = _by_type(clauses, "applied_on_override")
        self.assertEqual(len(liability), 1)
        self.assertEqual(liability[0]["effect"]["applied_on"], "TP/PA Premium")
        self.assertEqual(liability[0]["when"]["coverage_type_text"], "third party")

    def test_grid_alias_payload(self):
        text = (
            "Private Car Electric Vehicle or LPG/CNG pay-out grid is same "
            "as Private Car Petrol grid"
        )
        clauses = detect_qualifying_clauses(text)
        aliases = _by_type(clauses, "grid_alias")
        self.assertEqual(len(aliases), 1)
        self.assertEqual(
            sorted(aliases[0]["when"]["fuel_type"]),
            ["cng", "electric", "lpg"],
        )
        self.assertEqual(aliases[0]["effect"]["inherit_from"]["fuel_type"], "petrol")

    def test_mandatory_requirement_payload(self):
        text = (
            "CPA should be collected for individual Name Mandatorily for "
            "Private Car"
        )
        clauses = detect_qualifying_clauses(text)
        mandates = _by_type(clauses, "mandatory_requirement")
        self.assertEqual(len(mandates), 1)
        self.assertEqual(mandates[0]["effect"]["required"], ["cpa"])

    def test_deterministic_same_input_same_output(self):
        a = detect_qualifying_clauses(CHOLA_BLOB)
        b = detect_qualifying_clauses(CHOLA_BLOB)
        self.assertEqual(a, b)

    def test_clauses_validate_against_pydantic_model(self):
        """Every clause emitted must round-trip through QualifyingClause."""
        from cins.schema.cics_model import QualifyingClause

        clauses = detect_qualifying_clauses(CHOLA_BLOB)
        for raw in clauses:
            QualifyingClause.model_validate(raw)


class SandboxInjectionTests(TestCase):
    """Verify detect_qualifying_clauses is callable from inside the codegen
    sandbox WITHOUT the generated code needing to `import re` (which the
    AST validator forbids). This is the load-bearing contract: if the
    helper is not injected, every codegen Pass 3 falls back to empty
    qualifying_clauses and the structured data is silently dropped.
    """

    def test_codegen_can_call_helper_without_importing_re(self):
        from cins.processors.core.code_gen_executor import (
            execute_entry_code,
            validate_code_ast,
        )

        code = (
            "tables = parse_md_tables(commission_markdown)\n"
            "notes_text = extract_notes(tables)\n"
            "qualifying_clauses = detect_qualifying_clauses(notes_text)\n"
            "result = [{\n"
            '    "rule_text": "test", "commission_value": "10%",\n'
            '    "product_name": "Private Car", "product_type": "motor",\n'
            '    "qualifying_clauses": list(qualifying_clauses),\n'
            "}]\n"
        )

        validate_code_ast(code)  # must not raise — no `import re`

        md = (
            "## Notes Section [type: notes]\n"
            "Private Car Package policy pay-out is on OD Cover Premium\n"
            "if CPA is not collected for individual name then 1.5% of OD "
            "Pay-out will be deducted\n"
        )
        result = execute_entry_code(code, commission_markdown=md, timeout_seconds=5)
        self.assertEqual(len(result), 1)
        clauses = result[0]["qualifying_clauses"]
        self.assertTrue(len(clauses) >= 2)
        types = {c["clause_type"] for c in clauses}
        self.assertIn("applied_on_override", types)
        self.assertIn("conditional_deduction", types)

    def test_codegen_attempting_import_re_fails_validation(self):
        """Belt-and-braces — the sandbox guard that motivated host
        injection must remain active."""
        from cins.processors.core.code_gen_executor import (
            validate_code_ast,
            CodeGenValidationError,
        )

        with self.assertRaises(CodeGenValidationError):
            validate_code_ast("import re\nresult = []\n")
