"""The indexer's per-sheet key must be collision-free, and read must match write.

Same defect class as `test_sheet_name_collision.py`, one layer up. The pipeline's
50-char sanitizer collided on a real Chola workbook and swapped one sheet's grid
for another's. The indexer has the identical shape at 100 chars for
``table_splits`` keys and ``sheets/*.md`` filenames — latent today only because no
sheet name in the corpus is that long.

Two things are pinned:

1. the write side (``ContentIndexer._sanitize_sheet_key``) and the read side
   (``IndexedDocument._sanitize_for_lookup``) produce the SAME key — if they ever
   drift, every sheet load raises FileNotFoundError;
2. documents indexed before the change still resolve, via the legacy fallback.
"""

import json
import tempfile
import unittest
from pathlib import Path

from cins.processors.content.indexer import (
    ContentIndexer,
    IndexedDocument,
    legacy_sanitize_sheet_key,
    sanitize_sheet_key,
)

LONG_PREFIX = "Some_Very_Long_Workbook_Name_That_Eats_The_Whole_Budget" * 2


class SheetKeyCollisionTests(unittest.TestCase):
    def test_names_sharing_a_100_char_prefix_do_not_collide(self):
        a = sanitize_sheet_key(LONG_PREFIX + "__CV June 26")
        b = sanitize_sheet_key(LONG_PREFIX + "__TW June 26")
        self.assertNotEqual(a, b)

    def test_legacy_sanitizer_does_collide(self):
        """Documents the bug this replaces — if this ever passes, the shapes drifted."""
        a = legacy_sanitize_sheet_key(LONG_PREFIX + "__CV June 26")
        b = legacy_sanitize_sheet_key(LONG_PREFIX + "__TW June 26")
        self.assertEqual(a, b, "legacy key was expected to be the colliding shape")

    def test_key_is_bounded_and_filesystem_safe(self):
        for name in [LONG_PREFIX, "a/b:c*d?", "image::image001", " spaced "]:
            key = sanitize_sheet_key(name)
            self.assertLessEqual(len(key), 100, f"{name!r} -> {key!r}")
            self.assertRegex(key, r"^[\w\-]+$", f"{name!r} -> {key!r}")

    def test_key_is_deterministic(self):
        self.assertEqual(sanitize_sheet_key("TW july"), sanitize_sheet_key("TW july"))

    def test_degenerate_inputs_still_produce_a_usable_key(self):
        """Empty / None / unicode must not blow up or collide with each other."""
        keys = {name: sanitize_sheet_key(name) for name in ["", None, "ü" * 200, "  "]}
        for name, key in keys.items():
            self.assertTrue(key, f"{name!r} produced an empty key")
            self.assertLessEqual(len(key), 100, f"{name!r} -> {key!r}")
            self.assertRegex(key, r"^[\w\-]+$", f"{name!r} -> {key!r}")
        self.assertEqual(
            len(set(keys.values())), len(keys), f"degenerate inputs collided: {keys}"
        )

    def test_key_stays_within_the_filesystem_name_limit(self):
        """Multi-byte scripts must not push the FILENAME past NAME_MAX.

        ``\\w`` is unicode-aware, so a Devanagari or CJK sheet name keeps 3-byte
        characters. Truncating on character count gave 91*3+17 = 290 bytes, past
        the 255-byte ext4/APFS limit — indexing died with ENAMETOOLONG on a
        legitimately-named workbook. An Indian insurer shipping a Devanagari tab
        is not a hypothetical.
        """
        for name, label in [
            ("ü" * 200, "latin-1 (2-byte)"),
            ("अ" * 200, "Devanagari (3-byte)"),
            ("三" * 200, "CJK (3-byte)"),
            ("𝄞" * 200, "astral plane (4-byte)"),
        ]:
            key = sanitize_sheet_key(name)
            self.assertLessEqual(
                len(key.encode("utf-8")), 255, f"{label} -> {len(key.encode())} bytes"
            )
            # A byte-cut must not leave a mangled partial character behind.
            key.encode("utf-8").decode("utf-8")

    def test_digest_is_wide_enough_to_resist_a_crafted_collision(self):
        """Sheet names come from uploaded workbooks, so 32 bits is not enough."""
        key = sanitize_sheet_key("TW july")
        suffix = key.rsplit("_", 1)[-1]
        self.assertGreaterEqual(
            len(suffix), 16, f"digest suffix too narrow to resist brute force: {key!r}"
        )


class ReadWriteAgreementTests(unittest.TestCase):
    """The two sanitizers must never drift; a mismatch breaks every sheet load."""

    def test_indexer_write_key_matches_document_read_key(self):
        indexer = ContentIndexer.__new__(ContentIndexer)
        doc = IndexedDocument.__new__(IndexedDocument)
        for name in [LONG_PREFIX + "__CV June 26", "TW july", "image::image001"]:
            self.assertEqual(
                indexer._sanitize_sheet_key(name),
                doc._sanitize_for_lookup(name),
                f"read/write key drift on {name!r}",
            )


class LegacyDocumentFallbackTests(unittest.TestCase):
    """Documents indexed at <= 1.8 must keep loading with no re-index."""

    def _make_doc(self, indexed_dir: Path, key: str, content: str):
        (indexed_dir / "sheets").mkdir(parents=True, exist_ok=True)
        (indexed_dir / "sheets" / f"{key}.md").write_text(content, encoding="utf-8")
        metadata = {"source_type": "excel", "content_files": {"sheets": [key]}}
        (indexed_dir / "metadata.json").write_text(json.dumps(metadata))
        doc = IndexedDocument.__new__(IndexedDocument)
        doc.indexed_dir = indexed_dir
        doc.metadata = metadata
        return doc

    def test_legacy_key_on_disk_still_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            sheet = "TW july"
            doc = self._make_doc(
                Path(tmp), legacy_sanitize_sheet_key(sheet), "legacy-content"
            )
            self.assertEqual(doc.get_sheet_content(sheet), "legacy-content")

    def test_current_key_on_disk_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            sheet = "TW july"
            doc = self._make_doc(Path(tmp), sanitize_sheet_key(sheet), "new-content")
            self.assertEqual(doc.get_sheet_content(sheet), "new-content")

    def test_current_key_wins_when_both_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            sheet = "TW july"
            doc = self._make_doc(Path(tmp), sanitize_sheet_key(sheet), "new-content")
            (
                Path(tmp) / "sheets" / f"{legacy_sanitize_sheet_key(sheet)}.md"
            ).write_text("legacy-content", encoding="utf-8")
            self.assertEqual(doc.get_sheet_content(sheet), "new-content")

    def test_missing_sheet_still_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = self._make_doc(Path(tmp), sanitize_sheet_key("TW july"), "x")
            with self.assertRaises(FileNotFoundError):
                doc.get_sheet_content("no such sheet")

    def test_csv_only_legacy_document_resolves(self):
        """Pre-markdown documents stored .csv; the legacy fallback must find those too."""
        with tempfile.TemporaryDirectory() as tmp:
            sheet = "TW july"
            doc = self._make_doc(Path(tmp), sanitize_sheet_key(sheet), "unused")
            (Path(tmp) / "sheets" / f"{sanitize_sheet_key(sheet)}.md").unlink()
            (
                Path(tmp) / "sheets" / f"{legacy_sanitize_sheet_key(sheet)}.csv"
            ).write_text("a,b\n1,2\n", encoding="utf-8")
            self.assertEqual(doc.get_sheet_content(sheet), "a,b\n1,2\n")

    def test_legacy_table_splits_key_resolves(self):
        """A legacy doc keyed only in table_splits must resolve to its legacy file."""
        with tempfile.TemporaryDirectory() as tmp:
            sheet = "TW july"
            legacy = legacy_sanitize_sheet_key(sheet)
            (Path(tmp) / "sheets").mkdir(parents=True)
            (Path(tmp) / "sheets" / f"{legacy}.md").write_text(
                "## Table 1\nsplit-content", encoding="utf-8"
            )
            metadata = {
                "source_type": "excel",
                "content_files": {"sheets": [legacy]},
                "table_splits": {legacy: {"split_applied": True}},
            }
            (Path(tmp) / "metadata.json").write_text(json.dumps(metadata))
            doc = IndexedDocument.__new__(IndexedDocument)
            doc.indexed_dir = Path(tmp)
            doc.metadata = metadata
            self.assertEqual(doc.get_sheet_content(sheet), "## Table 1\nsplit-content")

    def test_split_applied_document_on_the_current_key(self):
        """The split path must work through the new key, not just the unsplit path."""
        with tempfile.TemporaryDirectory() as tmp:
            sheet = "TW july"
            key = sanitize_sheet_key(sheet)
            (Path(tmp) / "sheets").mkdir(parents=True)
            (Path(tmp) / "sheets" / f"{key}.md").write_text(
                "## Table 1\nnew-split", encoding="utf-8"
            )
            metadata = {
                "source_type": "excel",
                "content_files": {"sheets": [key]},
                "table_splits": {key: {"split_applied": True}},
            }
            (Path(tmp) / "metadata.json").write_text(json.dumps(metadata))
            doc = IndexedDocument.__new__(IndexedDocument)
            doc.indexed_dir = Path(tmp)
            doc.metadata = metadata
            self.assertEqual(doc.get_sheet_content(sheet), "## Table 1\nnew-split")


if __name__ == "__main__":
    unittest.main()
