"""The net under adding a fourth `SheetType`.

Before this file, NOTHING in the test suite imported `SheetType`. Adding a
member was a silent-failure change in three separate ways, each of which loses
data rather than raising:

* `derive_sheet_sets` drops an unrecognised `sheet_type` into NO bucket, and
  the empty-commission fallback in `derive_pipeline_routing` then resurrects it.
* `sheet_classifier` coerces an unrecognised string back to `COMMISSION_DATA`,
  so a label the prompt never taught silently becomes "extract it".
* The export's coverage sheet reports an unknown label as "unclassified", which
  reads to a reviewer as a pipeline failure rather than a deliberate skip.

Each test below fails on a member that was added in one place and forgotten in
another. None of them assert a member COUNT — that would just have to be edited
alongside the enum, which is the opposite of a net.
"""

from pathlib import Path

from django.test import TestCase

from cins.processors.core import sheet_visibility
from cins.processors.core.sheet_visibility import SheetSets, derive_sheet_sets
from cins.processors.extractors.sheet_classifier import SheetType
from commission_intake.views.commission_document import used_for_label


class SheetTypeRolloutTests(TestCase):
    def test_every_sheet_type_has_a_visibility_constant(self):
        constants = {
            value
            for name, value in vars(sheet_visibility).items()
            if name.isupper() and isinstance(value, str)
        }
        for member in SheetType:
            with self.subTest(sheet_type=member.value):
                self.assertIn(
                    member.value,
                    constants,
                    f"{member.name} has no constant in sheet_visibility, so "
                    f"derive_sheet_sets cannot route it.",
                )

    def test_every_sheet_type_lands_in_exactly_one_bucket(self):
        """The real net.

        A member `derive_sheet_sets` does not know about is dropped on the
        floor — it appears in no bucket, which is invisible until the routing
        fallback resurrects it.
        """
        classifications = {
            member.value: {"sheet_type": member.value, "visibility": "visible"}
            for member in SheetType
        }
        sets = derive_sheet_sets(classifications)
        buckets = {
            "commission": sets.commission,
            "master": sets.master,
            "indicative": sets.indicative,
        }

        for member in SheetType:
            with self.subTest(sheet_type=member.value):
                holding = [
                    name for name, names in buckets.items() if member.value in names
                ]
                self.assertEqual(
                    len(holding),
                    1,
                    f"{member.name} landed in {holding or 'NO bucket'}; "
                    f"expected exactly one.",
                )

        self.assertEqual(sets.hidden, [])

    def test_sheet_sets_field_order_is_pinned(self):
        """`indicative` is appended LAST on purpose.

        Production sites used to unpack this positionally as a 3-tuple. With the
        new field last, a stale unpack raises ValueError loudly. Inserted
        earlier, `hidden` would silently bind to the wrong list and hidden-sheet
        reporting would break everywhere with no error at all. Do not "tidy"
        this by grouping the classification buckets together.
        """
        self.assertEqual(
            SheetSets._fields, ("commission", "master", "hidden", "indicative")
        )

    def test_every_sheet_type_is_documented_in_the_classification_prompt(self):
        """A member the prompt never teaches can never be emitted by the LLM."""
        prompt = (
            Path(sheet_visibility.__file__).resolve().parents[2]
            / "prompts"
            / "sheet_classification_prompt.md"
        ).read_text(encoding="utf-8")

        for member in SheetType:
            with self.subTest(sheet_type=member.value):
                self.assertIn(
                    member.name,
                    prompt,
                    f"{member.name} is not described in "
                    f"sheet_classification_prompt.md.",
                )

    def test_every_non_extracted_sheet_type_has_its_own_export_label(self):
        """An unknown label reports as 'unclassified', which reads as a bug.

        COMMISSION_DATA is excluded because it reaches its label through the
        routed set ("Extracted"), asserted separately below — an unrouted
        commission sheet genuinely is an unclassified leftover. Every OTHER
        member is only ever described by its own arm, so a member added without
        one is silently reported as a pipeline failure rather than a deliberate
        skip.
        """
        for member in SheetType:
            if member is SheetType.COMMISSION_DATA:
                continue
            with self.subTest(sheet_type=member.value):
                label = used_for_label(
                    "Sheet1",
                    {"sheet_type": member.value},
                    routed_set=set(),
                    hidden_set=set(),
                )
                self.assertNotIn(
                    "unclassified",
                    label,
                    f"{member.name} falls through to the unclassified label.",
                )

    def test_routed_and_hidden_still_win_over_the_classification_label(self):
        """Fate comes from routing, not from the label — including for a new one."""
        for member in SheetType:
            with self.subTest(sheet_type=member.value):
                info = {"sheet_type": member.value}
                self.assertEqual(used_for_label("S", info, {"S"}, set()), "Extracted")
                self.assertEqual(
                    used_for_label("S", info, set(), {"S"}), "Skipped — hidden sheet"
                )
