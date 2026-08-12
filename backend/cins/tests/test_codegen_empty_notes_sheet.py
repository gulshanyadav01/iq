"""An empty codegen result is only a failure when the sheet HAS rate data.

Regression cover for the May'26 Payout Grid defect: its ``conditions`` sheet is
three ``[type: notes]`` tables of payout-terms prose with zero rate cells. Pass 3
correctly emitted no rules, but ``_extract_pass3_via_codegen`` treated any empty
``result`` list as a failure — three LLM retries, then ``overall_requires_review``.
That flag makes ``cics_cache.should_cache`` return False, so the document was
walled out of the output cache permanently and re-ran full codegen on every access.

The discriminator already exists: ``count_rate_bearing_rows_markdown`` is what the
completeness gate uses, and ``_markdown_floor_report`` already returns
``passed=True, reason="no rate rows in markdown"`` for this case. The empty branch
just short-circuited before the gate was ever consulted.

Contract pinned here:

- rate-free content + empty result  -> clean empty, requires_review False, NO retries;
- rate-bearing content + empty result -> unchanged: retries to MAX_CODEGEN_ATTEMPTS
  and sets requires_review True (a genuine whole-grid drop must still shout).
"""

import unittest

from cins.processors.core.codegen_coverage import count_rate_bearing_rows_markdown
from cins.processors.core.llm_extract import _extract_pass3_via_codegen
from cins.schema.extraction_passes import MasterDataExtraction, MetadataExtraction

# Three notes tables, no rate cells — the shape of Chola's "conditions" sheet.
NOTES_ONLY_CONTENT = """
## Table 1: Private Car and Commercial Vehicle Payout Conditions [type: notes]

** Please note that the above pay-out grid
-  For Private Car Package policy pay-out is on OD Cover Premium
-  For CV Package & Liability policy CPA should be collected for individual Name Mandatorily
if CPA is not collected for individual name then 1.5% of Net Payout will be deducted

## Table 2: Two Wheeler Payout Conditions [type: notes]

** Please note that the above payout grid
-  If Policy the source through OEM Program and Payout is Not available a Minimum Payout of 22.5% to be given
SAOD TW will not have any payouts,it will be blocked in system
"""

# A real grid: rate cells in the data rows.
RATE_BEARING_CONTENT = """
## Table 1: CV Grid [type: commission]

| Product | State | ACT | PACK |
| --- | --- | --- | --- |
| GCCV 3W | AP | 32% | 45% |
| GCCV 3W | TS | 30% | 42% |
| SCHOOL BUS | ROM | 18% | 25% |
"""


class _FakeConfig:
    def __init__(self):
        self.system_instruction = ""


class _FakeService:
    """LLM service stand-in that always returns code producing zero rules."""

    def __init__(self):
        self.calls = 0

    def config_for_code_generation(self):
        return _FakeConfig()

    def generate_content(self, prompt, config=None):
        self.calls += 1
        return "result = []"


def _metadata():
    return MetadataExtraction(
        provider_name="Chola",
        period_label="May 2026",
        start_date="2026-05-01",
        end_date="2026-05-31",
    )


def _master_data():
    return MasterDataExtraction()


class RateFreeContentIsNotAFailureTests(unittest.TestCase):
    def test_notes_only_content_has_no_rate_rows(self):
        """Guards the discriminator itself — if this drifts, the fix silently stops firing."""
        self.assertEqual(count_rate_bearing_rows_markdown(NOTES_ONLY_CONTENT), 0)
        self.assertGreater(count_rate_bearing_rows_markdown(RATE_BEARING_CONTENT), 0)

    def test_empty_result_on_notes_sheet_is_clean(self):
        service = _FakeService()
        result, _code, _prompt = _extract_pass3_via_codegen(
            NOTES_ONLY_CONTENT,
            service,
            _metadata(),
            _master_data(),
        )
        self.assertEqual(result.products, [])
        self.assertFalse(
            result.overall_requires_review,
            "a rate-free sheet must not block the output-cache pin",
        )

    def test_empty_result_on_notes_sheet_does_not_retry(self):
        """Three LLM calls per rate-free sheet is pure waste; expect at most one."""
        service = _FakeService()
        _extract_pass3_via_codegen(
            NOTES_ONLY_CONTENT,
            service,
            _metadata(),
            _master_data(),
        )
        self.assertLessEqual(
            service.calls,
            1,
            f"expected no retries on a rate-free sheet, got {service.calls} LLM calls",
        )

    def test_reason_is_recorded(self):
        """The empty result must say WHY it is empty, so it is not read as a silent drop."""
        service = _FakeService()
        result, _code, _prompt = _extract_pass3_via_codegen(
            NOTES_ONLY_CONTENT,
            service,
            _metadata(),
            _master_data(),
        )
        joined = " ".join(result.overall_ambiguities).lower()
        self.assertTrue(joined, "expected an explanatory note on the empty extraction")
        self.assertIn("no rate", joined)


class RateBearingContentStillFailsLoudlyTests(unittest.TestCase):
    """The fix must not weaken the genuine whole-grid-drop detection."""

    def test_empty_result_on_real_grid_still_requires_review(self):
        service = _FakeService()
        result, _code, _prompt = _extract_pass3_via_codegen(
            RATE_BEARING_CONTENT,
            service,
            _metadata(),
            _master_data(),
        )
        self.assertEqual(result.products, [])
        self.assertTrue(
            result.overall_requires_review,
            "dropping a grid that HAS rate rows must still be flagged",
        )

    def test_empty_result_on_real_grid_still_retries(self):
        service = _FakeService()
        _extract_pass3_via_codegen(
            RATE_BEARING_CONTENT,
            service,
            _metadata(),
            _master_data(),
        )
        self.assertEqual(
            service.calls, 3, "a real grid must still burn all retry attempts"
        )


class EmptyContentTests(unittest.TestCase):
    def test_completely_empty_content_is_clean(self):
        """No content at all is rate-free by definition — do not retry, do not flag."""
        service = _FakeService()
        result, _code, _prompt = _extract_pass3_via_codegen(
            "",
            service,
            _metadata(),
            _master_data(),
        )
        self.assertFalse(result.overall_requires_review)
        self.assertLessEqual(service.calls, 1)


class DiscriminatorReadsSheetContentNotPromptTests(unittest.TestCase):
    """The rate-row count must ignore the context header.

    ``user_prompt`` is the sheet content PLUS a context header carrying Pass-1
    global settings and the Pass-2 master-data dump. That header is prose, and the
    counter can misread it: a comma splits a line into cells, so
    ``- Deductions: 1.5%, 2%`` yields a cell holding a bare ``2%`` and scores as a
    rate-bearing row. Counting the header would make every notes sheet in such a
    document look rate-bearing and silently send it back to the 3-retry +
    requires_review path — the permanent cache block this fix removes.
    """

    def test_comma_separated_deductions_score_as_a_rate_row(self):
        """Pins the counter behaviour the fix has to route around."""
        self.assertEqual(count_rate_bearing_rows_markdown("- Deductions: 1.5%, 2%"), 1)

    def test_notes_sheet_stays_clean_despite_rate_like_global_settings(self):
        service = _FakeService()
        metadata = MetadataExtraction(
            provider_name="Chola",
            period_label="May 2026",
            start_date="2026-05-01",
            end_date="2026-05-31",
            global_deductions="1.5%, 2%",
            global_gst_percentage="18%",
        )
        result, _code, prompt = _extract_pass3_via_codegen(
            NOTES_ONLY_CONTENT,
            service,
            metadata,
            _master_data(),
        )
        # Guard the premise: the header really is in the prompt and really does
        # look rate-bearing, so this test fails loudly if the fix regresses.
        self.assertIn("1.5%, 2%", prompt)
        self.assertGreater(count_rate_bearing_rows_markdown(prompt), 0)

        self.assertFalse(
            result.overall_requires_review,
            "global settings in the context header must not make a notes sheet "
            "look rate-bearing",
        )
        self.assertLessEqual(service.calls, 1)


class RateCounterBlindSpotsDoNotAcceptADropTests(unittest.TestCase):
    """A commission-tagged grid can count ZERO rate rows — that must not read as empty.

    ``_is_rate`` is a strict ``fullmatch(r"-?\\d+(\\.\\d+)?%?")``, so cells written
    ``22.5 %``, ``Upto 25%``, ``25%-30%`` or ``Rs 500`` score nothing. Deciding
    "this sheet is empty" from that counter alone would accept a genuinely dropped
    payout grid as clean and PIN IT TO CACHE — strictly worse than the retry-and-flag
    behaviour it replaced. The structural ``[type: commission]`` marker is what
    actually holds the line.
    """

    GRID_THE_COUNTER_CANNOT_SEE = """
## Table 1: CV Grid [type: commission]

| Product | State | ACT | PACK |
| --- | --- | --- | --- |
| GCCV 3W | AP | 22.5 % | Upto 25% |
| SCHOOL BUS | ROM | 25%-30% | Rs 500 |
"""

    def test_the_counter_really_is_blind_to_this_grid(self):
        """Pins the premise — if the counter improves, this test should be revisited."""
        self.assertEqual(
            count_rate_bearing_rows_markdown(self.GRID_THE_COUNTER_CANNOT_SEE), 0
        )

    def test_a_commission_tagged_grid_is_never_accepted_as_empty(self):
        service = _FakeService()
        result, _code, _prompt = _extract_pass3_via_codegen(
            self.GRID_THE_COUNTER_CANNOT_SEE,
            service,
            _metadata(),
            _master_data(),
        )
        self.assertTrue(
            result.overall_requires_review,
            "a [type: commission] table returning zero rules is a dropped grid, "
            "however the rate cells happen to be formatted",
        )
        self.assertEqual(service.calls, 3, "a suspected drop must still retry")

    def test_untagged_content_is_treated_as_rate_bearing(self):
        """No table markers at all means no positive evidence — fail safe."""
        service = _FakeService()
        result, _code, _prompt = _extract_pass3_via_codegen(
            "some prose with no table markers at all",
            service,
            _metadata(),
            _master_data(),
        )
        self.assertTrue(result.overall_requires_review)


class IndexTimeShapeOverridesTheMarkdownCountTests(unittest.TestCase):
    """A persisted shape claiming rate cells vetoes the clean-empty path.

    ``rate_cell_count`` is counted from the raw CSV slice at index time, so it is
    an independent witness to the markdown codegen actually read — and the two can
    disagree when the markdown conversion drops or re-bands rows. If the shape says
    the sheet holds rate cells and every attempt still comes back empty, that is a
    whole-grid drop. Accepting it as a notes sheet would not just miss the drop, it
    would PIN it to the output cache, which is strictly worse than the behaviour
    this fix replaced.
    """

    def test_empty_result_is_flagged_when_the_shape_expects_rate_cells(self):
        service = _FakeService()  # every attempt returns an empty result
        table_shape = {
            "tables": [
                {
                    "table_index": 1,
                    "table_name": "CV Grid",
                    "table_type": "commission",
                    "rate_cell_count": 500,
                }
            ]
        }
        result, _code, _prompt = _extract_pass3_via_codegen(
            NOTES_ONLY_CONTENT,
            service,
            _metadata(),
            _master_data(),
            table_shape=table_shape,
        )
        self.assertTrue(
            result.overall_requires_review,
            "the index-time shape expected 500 rate cells; an empty result must "
            "not be accepted as a clean notes sheet",
        )
        self.assertEqual(
            service.calls, 3, "a suspected whole-grid drop must still burn retries"
        )

    def test_a_shape_expecting_nothing_does_not_veto(self):
        """A shape present but reporting zero rate cells must still allow the clean path."""
        service = _FakeService()
        table_shape = {
            "tables": [
                {
                    "table_index": 1,
                    "table_name": "Conditions",
                    "table_type": "commission",
                    "rate_cell_count": 0,
                }
            ]
        }
        result, _code, _prompt = _extract_pass3_via_codegen(
            NOTES_ONLY_CONTENT,
            service,
            _metadata(),
            _master_data(),
            table_shape=table_shape,
        )
        self.assertFalse(result.overall_requires_review)
        self.assertLessEqual(service.calls, 1)

    def test_legacy_shape_without_rate_cell_count_does_not_veto(self):
        """Legacy docs carry no rate_cell_count — fail open, same as the gate does."""
        service = _FakeService()
        table_shape = {"tables": [{"table_index": 1, "table_name": "Old"}]}
        result, _code, _prompt = _extract_pass3_via_codegen(
            NOTES_ONLY_CONTENT,
            service,
            _metadata(),
            _master_data(),
            table_shape=table_shape,
        )
        self.assertFalse(result.overall_requires_review)


class GateForcedRetryKeepsEarlierRowsTests(unittest.TestCase):
    """The early return must never discard rows an earlier attempt extracted.

    The completeness gate can force a retry when ``table_shape`` expects rows but
    the markdown count is 0. If a later attempt then comes back empty, returning a
    clean empty result would delete real rows — the gate manufacturing the very
    drop it exists to prevent.
    """

    def test_empty_retry_after_a_productive_attempt_keeps_the_rows(self):
        entry = {
            "rule_text": "GCCV 3W AP 32%",
            "product_name": "Commercial Vehicle",
            "state_text": "AP",
            "commission_value": "32%",
        }

        class _ThenEmptyService(_FakeService):
            """First attempt yields a row; every later attempt yields nothing."""

            def generate_content(self, prompt, config=None):
                self.calls += 1
                if self.calls == 1:
                    return f"result = [{entry!r}]"
                return "result = []"

        service = _ThenEmptyService()
        # table_shape claims far more rows than were emitted, so the gate forces a
        # retry; the sheet markdown itself carries no countable rate rows.
        table_shape = {
            "tables": [
                {
                    "table_index": 1,
                    "table_name": "CV Grid",
                    "table_type": "commission",
                    "rate_cell_count": 500,
                }
            ]
        }
        result, _code, _prompt = _extract_pass3_via_codegen(
            NOTES_ONLY_CONTENT,
            service,
            _metadata(),
            _master_data(),
            table_shape=table_shape,
        )
        emitted = sum(len(p.rules) for p in result.products)
        self.assertGreater(
            emitted,
            0,
            "a gate-forced retry must not discard rows a prior attempt extracted",
        )


if __name__ == "__main__":
    unittest.main()
