"""Sheet filename sanitization must be collision-free.

Regression cover for the June PO Grid defect: four Chola commission sheets shared
a 60-character prefix, so ``get_safe_sheet_name`` truncated all four to the same
50-character key. ``_process_one_sheet`` wrote ``pass3_content_{safe_name}.txt``
into a document-shared output directory and read it straight back while four
threads ran concurrently, so the last writer won and three sheets were handed the
same Pass-3 content. The CV sheet received the Two Wheeler grid and correctly
parsed it as Two Wheeler: the document's whole CV book (1,552 rules across
GCV/PCV/Buses/MISC-D) was replaced by 2,633 phantom TW rules.

Two independent guarantees are pinned here:

1. distinct sheet names always yield distinct safe names (no truncation collision);
2. the per-sheet Pass-3 content each sheet is extracted with is its OWN content,
   even when every name collapses to the same truncated prefix and the sheets are
   processed concurrently.
"""

import ast
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cins.processors.core import pipeline
from cins.processors.core.pipeline import build_pass_contents, get_safe_sheet_name

# The four real sheet names from "June PO Grid - Chola MS.eml". They share the
# 60-char prefix "Broker_june_26_Grid-for_Circulation-_Retail_Broking_-Final__".
JUNE_SHEET_NAMES = [
    "Broker_june_26_Grid-for_Circulation-_Retail_Broking_-Final__ CV June 26",
    "Broker_june_26_Grid-for_Circulation-_Retail_Broking_-Final__TW June 26",
    "Broker_june_26_Grid-for_Circulation-_Retail_Broking_-Final__conditions",
    "Broker_june_26_Grid-for_Circulation-_Retail_Broking_-Final__Private car June-26",
]


class SafeSheetNameTests(unittest.TestCase):
    def test_june_sheets_do_not_collide(self):
        """The four June sheets must map to four distinct filename keys."""
        safe = [get_safe_sheet_name(n) for n in JUNE_SHEET_NAMES]
        self.assertEqual(
            len(set(safe)),
            len(JUNE_SHEET_NAMES),
            f"sheet names collapsed to the same key: {safe}",
        )

    def test_names_differing_only_past_the_truncation_point_do_not_collide(self):
        """A shared prefix longer than the length budget must still disambiguate."""
        prefix = "x" * 200
        a = get_safe_sheet_name(prefix + "_alpha")
        b = get_safe_sheet_name(prefix + "_beta")
        self.assertNotEqual(a, b)

    def test_output_is_filename_safe_and_bounded(self):
        """Keys stay filesystem-safe and within the historical 50-char budget."""
        for name in JUNE_SHEET_NAMES + ["a/b:c*d?", "  spaced  ", "unicode-ü-name"]:
            safe = get_safe_sheet_name(name)
            self.assertLessEqual(len(safe), 50, f"{name!r} -> {safe!r}")
            self.assertRegex(safe, r"^[\w\-]+$", f"{name!r} -> {safe!r}")

    def test_is_deterministic_across_calls(self):
        """The same name must produce the same key on every run (cache stability)."""
        for name in JUNE_SHEET_NAMES:
            self.assertEqual(get_safe_sheet_name(name), get_safe_sheet_name(name))


class ConcurrentPassContentTests(unittest.TestCase):
    """Each sheet must be extracted with its own Pass-3 content, concurrently.

    This is the actual client-visible defect: not the key collision itself, but
    one sheet's grid reaching another sheet's extraction.
    """

    def test_each_sheet_keeps_its_own_pass3_content(self):
        # Distinct, recognisable content per sheet — mirrors the real shapes
        # (a CV grid, a TW grid, a notes sheet, a private-car grid).
        sheet_contents = {
            name: f"## Commission Data Sheet: {name}\n\n| Product | ACT |\n| --- | --- |\n| row-{i} | {i}% |"
            for i, name in enumerate(JUNE_SHEET_NAMES)
        }
        commission_sheets = list(JUNE_SHEET_NAMES)

        def _pass3_for(sheet_name):
            _, _, pass3_content, _ = build_pass_contents(
                sheet_name=sheet_name,
                sheet_content=sheet_contents[sheet_name],
                master_context="",
                email_body="",
                commission_sheets=commission_sheets,
            )
            return sheet_name, pass3_content

        with ThreadPoolExecutor(max_workers=len(JUNE_SHEET_NAMES)) as pool:
            results = dict(pool.map(_pass3_for, JUNE_SHEET_NAMES))

        for i, name in enumerate(JUNE_SHEET_NAMES):
            self.assertIn(
                f"row-{i}",
                results[name],
                f"{name!r} was extracted with another sheet's content",
            )
            # And no other sheet's marker leaked in.
            for j in range(len(JUNE_SHEET_NAMES)):
                if j != i:
                    self.assertNotIn(f"row-{j}", results[name])

    def test_artifact_paths_are_unique_per_sheet(self):
        """Debug artifacts and output JSON must not clobber each other either."""
        paths = {
            f"pass3_content_{get_safe_sheet_name(n)}.txt" for n in JUNE_SHEET_NAMES
        }
        self.assertEqual(len(paths), len(JUNE_SHEET_NAMES), sorted(paths))


class PassContentNeverRoundTripsThroughDiskTests(unittest.TestCase):
    """Pass content must reach the LLM from memory, never re-read from a file.

    This is the actual client-visible fix, and it is the one part a unit test
    cannot reach behaviourally: ``_process_one_sheet`` is a closure inside
    ``_process_sheets`` and needs a full indexed document to run. So guard it
    structurally instead. Unique filenames alone are not enough — the whole class
    of bug is "sheet content travels via a shared mutable directory", and the only
    way to make that unreachable is for nothing to read the files back.

    If this test fails, someone reintroduced the write-then-read hop.
    """

    def test_process_sheets_reads_nothing_from_disk(self):
        """The per-sheet path must not read ANY file.

        Scoping the check to the function rather than to a filename pattern is
        deliberate: a filename-matching assertion misses the same defect
        reintroduced through a local variable (``p = output_dir / f"pass3_content_
        {safe_name}.txt"`` then ``p.read_text()``), and pipeline.py already uses
        exactly that indirection elsewhere for ``extracted_content.txt``. Inside
        ``_process_sheets`` there is nothing legitimate to read — the content
        arrives from ``build_pass_contents`` — so "zero reads" is both the true
        invariant and the one a revert cannot slip past.
        """
        tree = ast.parse(Path(pipeline.__file__).read_text(encoding="utf-8"))
        target = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "_process_sheets"
            ),
            None,
        )
        self.assertIsNotNone(target, "_process_sheets not found — did it get renamed?")

        readers = {
            "read_text",
            "read_bytes",
            "open",
            "prepare_extraction_request",
            "get_document_content",
        }
        offenders = []
        for node in ast.walk(target):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if name in readers:
                offenders.append(f"{name}() at line {node.lineno}")
        self.assertEqual(
            offenders,
            [],
            "_process_sheets reads from disk; per-sheet content must come from "
            f"build_pass_contents in memory: {offenders}",
        )

    def test_process_one_sheet_assigns_prompts_from_memory(self):
        """Each prompt is bound from the in-memory build_pass_contents output.

        Allows a pure transform of that value (newline normalization) but not a
        different source — the binding must name ``pass{n}_content``.
        """
        source = Path(pipeline.__file__).read_text(encoding="utf-8")
        for n in (1, 2, 3):
            self.assertRegex(
                source,
                rf"pass{n}_prompt = [\w.]*\(?pass{n}_content\)?",
                f"pass{n}_prompt is no longer bound from the in-memory content",
            )


class LegacySanitizerShapeTests(unittest.TestCase):
    """The sanitizer keeps a readable prefix so artifacts stay greppable."""

    def test_prefix_remains_human_readable(self):
        safe = get_safe_sheet_name(JUNE_SHEET_NAMES[0])
        self.assertTrue(
            safe.startswith("Broker_june_26_Grid"),
            f"expected a readable prefix, got {safe!r}",
        )
        self.assertEqual(re.sub(r"[^\w\-]", "", safe), safe)


if __name__ == "__main__":
    unittest.main()
