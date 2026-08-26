#!/usr/bin/env python3
"""
Excel to AI-Ready CSV Converter

Converts complex Excel files with merged cells, multi-level hierarchies, and scattered
content into clean CSV files optimized for LLM consumption.

Solution: Dual Output Strategy
- Output 1: Clean CSV (expanded merged cells, filled empty cells, clean formatting)
- Output 2: Metadata JSON (merged ranges, bold cells, detected headers, excluded content)

Usage:
    from cins.processors.excel_to_csv import ExcelToCSVConverter

    converter = ExcelToCSVConverter(output_dir='./output')
    results = converter.process_workbook('file.xlsx')

    # Or process single sheet
    result = converter.process_sheet('file.xlsx', 'Sheet1')
"""

import openpyxl
import csv
import json
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass
import re

import logging

from cins.processors.core.sheet_visibility import VISIBLE_SHEET_STATE
from cins.processors.extractors.indicative_markers import (
    IndicativeMarkerMatch,
    scan_csv_file,
)

logger = logging.getLogger("excel_to_csv")

#: Written by :func:`_apply_indicative_override`. Kept as a literal rather than
#: importing ``SheetType`` because the classifier is an optional dependency
#: here (see the guarded import below) and this path must work without it.
INDICATIVE_DATA = "indicative_data"


def _apply_indicative_override(
    classification: Optional[Dict[str, Any]],
    sheet_name: str,
    marker: IndicativeMarkerMatch,
) -> Dict[str, Any]:
    """Relabel a sheet that declares it is not the authoritative payout grid.

    Overrides ``commission_data`` AND ``master_data``, uniformly. Not just
    commission: a self-declared non-authoritative rate grid labelled master
    still has its full content concatenated into ``master_context`` and
    injected into every sibling sheet's Pass 1 and Pass 2 prompt, which is the
    pollution this exists to prevent. One rule — if the marker fires the label
    is ``indicative_data`` — is one test rather than a matrix.

    The prior verdict is preserved under ``override``, together with the
    sentence that triggered this. ``sheet_metadata`` is free-form JSONB passed
    through verbatim by the serializer, so that evidence reaches the frontend
    with no extra plumbing — and when a sheet's whole output disappears, a
    reviewer has to be able to read the reason without opening a shell.
    """
    prior = classification or {}
    prior_type = prior.get("sheet_type")
    prior_confidence = prior.get("confidence")

    logger.warning(
        "Sheet '%s': self-declared non-authoritative grid; classification "
        "overridden %s(%s) -> %s. Matched '%s' + '%s' on CSV line %d: %r",
        sheet_name,
        prior_type or "unclassified",
        prior_confidence if prior_confidence is not None else "n/a",
        INDICATIVE_DATA,
        marker.self_ref,
        marker.deferral,
        marker.line_number,
        marker.excerpt,
    )

    indicators = [
        f"disclaimer_marker: '{marker.self_ref}' + '{marker.deferral}'",
        f"excerpt: {marker.excerpt}",
    ]
    indicators.extend(prior.get("indicators") or [])

    return {
        "sheet_name": sheet_name,
        "sheet_type": INDICATIVE_DATA,
        "confidence": 1.0,
        "reasoning": (
            "Sheet self-declares it is not the authoritative payout grid. "
            "Deterministic disclaimer marker matched; LLM verdict "
            f"'{prior_type or 'none'}' "
            f"(confidence {prior_confidence if prior_confidence is not None else 'n/a'}) "
            "overridden."
        ),
        "indicators": indicators,
        "override": {
            "source": "disclaimer_marker",
            "self_ref": marker.self_ref,
            "deferral": marker.deferral,
            "excerpt": marker.excerpt,
            "line": marker.line_number,
            "overridden_sheet_type": prior_type,
            "overridden_confidence": prior_confidence,
        },
    }


# Import sheet classifier (optional dependency)
try:
    from cins.processors.extractors.sheet_classifier import (
        SheetClassifier,
        SheetClassification,
    )

    CLASSIFIER_AVAILABLE = True
except ImportError:
    CLASSIFIER_AVAILABLE = False
    SheetClassification = None


# Configuration
DEFAULT_FILL_EMPTY = ""  # Marker for empty cells (empty string for clean CSV output)


@dataclass
class MergedRange:
    """Represents a merged cell range in Excel."""

    start: Tuple[int, int]  # (row, col) - 1-indexed
    end: Tuple[int, int]  # (row, col) - 1-indexed
    value: Any  # Value from top-left cell
    range_str: str  # Excel notation (e.g., "BE3:BF3")

    @property
    def span(self) -> Dict[str, int]:
        """Calculate row and column span."""
        return {
            "rows": self.end[0] - self.start[0] + 1,
            "cols": self.end[1] - self.start[1] + 1,
        }

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "range": self.range_str,
            "start": list(self.start),
            "end": list(self.end),
            "value": str(self.value) if self.value is not None else None,
            "span": self.span,
        }


@dataclass
class SheetMetadata:
    """Essential metadata for one Excel sheet."""

    sheet_name: str
    dimensions: Dict[str, int]  # rows, cols, original_rows, original_cols
    merged_ranges: List[Dict]  # List of MergedRange.to_dict()
    formatting: Dict[str, Any]  # bold_cells, detected_header_rows
    excluded_content: Dict[str, Any]  # hidden_rows, hidden_columns
    classification: Optional[Dict[str, Any]] = None  # Sheet type classification

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        base_dict = {
            "sheet_name": self.sheet_name,
            "dimensions": self.dimensions,
            "merged_ranges": self.merged_ranges,
            "formatting": self.formatting,
            "excluded_content": self.excluded_content,
        }
        if self.classification is not None:
            base_dict["classification"] = self.classification
        return base_dict


class ExcelToCSVConverter:
    """
    Converts Excel files to AI-ready CSV format with metadata.

    Features:
    - Expands merged cells (copy top-left value to all cells)
    - Fills empty cells with explicit marker
    - Excludes hidden rows/columns
    - Generates metadata JSON with structural information
    - Maintains exact grid alignment with source Excel
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        fill_empty: str = DEFAULT_FILL_EMPTY,
        enable_classification: bool = True,
    ):
        """
        Initialize converter.

        Args:
            output_dir: Directory for output files (default: ./output/csv_converted)
            fill_empty: String to use for empty cells (default: 'NA')
            enable_classification: Enable AI-based sheet classification (default: True)
        """
        self.output_dir = (
            Path(output_dir) if output_dir else Path("./output/csv_converted")
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.fill_empty = fill_empty
        self.enable_classification = enable_classification and CLASSIFIER_AVAILABLE

        # Initialize classifier if enabled
        self.classifier = SheetClassifier() if self.enable_classification else None

    def detect_hidden_rows(self, sheet) -> List[int]:
        """
        Detect hidden rows in Excel sheet.

        Args:
            sheet: openpyxl worksheet object

        Returns:
            List of hidden row numbers (1-indexed)
        """
        hidden_rows = []

        for row_idx in range(1, sheet.max_row + 1):
            if row_idx in sheet.row_dimensions:
                if sheet.row_dimensions[row_idx].hidden:
                    hidden_rows.append(row_idx)

        return hidden_rows

    def detect_hidden_columns(self, sheet) -> List[int]:
        """
        Detect hidden columns in Excel sheet.

        Args:
            sheet: openpyxl worksheet object

        Returns:
            List of hidden column numbers (1-indexed)
        """
        hidden_cols = []

        for col_idx in range(1, sheet.max_column + 1):
            col_letter = openpyxl.utils.get_column_letter(col_idx)

            if col_letter in sheet.column_dimensions:
                if sheet.column_dimensions[col_letter].hidden:
                    hidden_cols.append(col_idx)

        return hidden_cols

    def get_visible_ranges(self, sheet) -> Tuple[List[int], List[int]]:
        """
        Get lists of visible (non-hidden) rows and columns.

        Args:
            sheet: openpyxl worksheet object

        Returns:
            Tuple of (visible_rows, visible_cols) - both 1-indexed
        """
        hidden_rows = set(self.detect_hidden_rows(sheet))
        hidden_cols = set(self.detect_hidden_columns(sheet))

        visible_rows = [r for r in range(1, sheet.max_row + 1) if r not in hidden_rows]
        visible_cols = [
            c for c in range(1, sheet.max_column + 1) if c not in hidden_cols
        ]

        return visible_rows, visible_cols

    def detect_merged_ranges(self, sheet) -> List[MergedRange]:
        """
        Extract all merged cell ranges from Excel sheet.

        Args:
            sheet: openpyxl worksheet object

        Returns:
            List of MergedRange objects
        """
        merged_ranges = []

        for merged_range in sheet.merged_cells.ranges:
            start_row = merged_range.min_row
            start_col = merged_range.min_col
            end_row = merged_range.max_row
            end_col = merged_range.max_col

            # Get value from top-left cell (only cell with value in merged range)
            top_left_value = sheet.cell(start_row, start_col).value

            # Excel notation (e.g., "BE3:BF3")
            range_str = str(merged_range)

            merged_ranges.append(
                MergedRange(
                    start=(start_row, start_col),
                    end=(end_row, end_col),
                    value=top_left_value,
                    range_str=range_str,
                )
            )

        return merged_ranges

    def expand_merged_cells(
        self,
        sheet,
        merged_ranges: List[MergedRange],
        visible_rows: List[int],
        visible_cols: List[int],
    ) -> Dict[Tuple[int, int], Any]:
        """
        Build expanded grid where merged cells are filled with source value.

        Key insight: In openpyxl, only top-left cell of merged range has value.
        This function copies that value to all cells in the merged range.

        Args:
            sheet: openpyxl worksheet object
            merged_ranges: List of MergedRange objects
            visible_rows: List of visible row indices (1-indexed)
            visible_cols: List of visible column indices (1-indexed)

        Returns:
            Dictionary mapping (row, col) -> value for all visible cells
        """
        expanded_grid = {}

        # Build visible sets for O(1) lookup
        visible_rows_set = set(visible_rows)
        visible_cols_set = set(visible_cols)

        # Create O(1) lookup for merged ranges.
        #
        # Propagate the value to the VISIBLE cells of each range, regardless of
        # whether the range's top-left cell is itself visible. A merged group
        # header routinely starts in a hidden column while its siblings stay
        # visible: Chola's TW sheet hides AX and BB, which are exactly the
        # top-left cells of `AX4:AZ4 -> "Hero vs Bajaj"` and `BB4:BE4 -> "Bajaj"`.
        # Skipping the whole range dropped the make from the visible AY/AZ/BC/BD/BE
        # columns, so their rules carried no vehicle_make and two different
        # SCOOTER columns collapsed onto byte-identical conditions with different
        # payouts (54 CRITICAL duplicate findings on doc 733711bd).
        #
        # A range that is entirely hidden still contributes nothing — no visible
        # cell exists to write to — so genuinely hidden scratch content cannot
        # leak in.
        merged_lookup = {}
        for mr in merged_ranges:
            for r in range(mr.start[0], mr.end[0] + 1):
                if r not in visible_rows_set:
                    continue
                for c in range(mr.start[1], mr.end[1] + 1):
                    if c not in visible_cols_set:
                        continue
                    merged_lookup[(r, c)] = mr.value

        for row in visible_rows:
            for col in visible_cols:
                # Check if cell is part of merged range
                if (row, col) in merged_lookup:
                    expanded_grid[(row, col)] = merged_lookup[(row, col)]
                else:
                    # Regular cell - get value directly
                    expanded_grid[(row, col)] = sheet.cell(row, col).value

        return expanded_grid

    def grid_to_csv(
        self,
        expanded_grid: Dict[Tuple[int, int], Any],
        visible_rows: List[int],
        visible_cols: List[int],
        output_path: Path,
    ) -> None:
        """
        Write expanded grid to CSV file.

        Key features:
        - Empty cells filled with 'NA' (explicit marking)
        - Newlines replaced with single space (clean LLM parsing)
        - Proper UTF-8 encoding and CSV escaping
        - Exact grid alignment with Excel (excluding hidden content)

        Args:
            expanded_grid: Dictionary mapping (row, col) -> value
            visible_rows: List of visible row indices (1-indexed)
            visible_cols: List of visible column indices (1-indexed)
            output_path: Path to output CSV file
        """
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            for row in visible_rows:
                row_data = []

                for col in visible_cols:
                    value = expanded_grid.get((row, col))

                    # Handle empty cells
                    if value is None or value == "":
                        row_data.append(self.fill_empty)
                    # Handle newlines (from merged text blocks)
                    elif isinstance(value, str) and "\n" in value:
                        # Replace newlines with single space for clean LLM parsing
                        cleaned_value = value.replace("\n", " ").replace("\r", "")
                        row_data.append(cleaned_value)
                    else:
                        row_data.append(str(value))

                writer.writerow(row_data)

    def extract_bold_cells(
        self, sheet, visible_rows: List[int], visible_cols: List[int]
    ) -> List[List[int]]:
        """
        Scan for bold cells (used for header detection).

        Args:
            sheet: openpyxl worksheet object
            visible_rows: List of visible row indices
            visible_cols: List of visible column indices

        Returns:
            List of [row, col] coordinates with bold formatting
        """
        bold_cells = []

        for row in visible_rows:
            for col in visible_cols:
                cell = sheet.cell(row, col)
                if cell.font and cell.font.bold:
                    bold_cells.append([row, col])

        return bold_cells

    def detect_header_rows(
        self, sheet, merged_ranges: List[MergedRange], visible_cols: List[int]
    ) -> List[int]:
        """
        Heuristic header detection using two signals:

        1. Horizontal merged cells (multi-column spans in a single row) — these are
           the structural column-group title rows (e.g. "Hero OEM" spanning 4 columns).
           Vertical merges (data cells grouped across rows) are excluded.

        2. Non-numeric rows that immediately follow a merged-cell header row — these
           are the label rows (e.g. "150cc", "SCOOTER") that have no merge but also
           carry no commission numbers.

        The bold-cell heuristic is intentionally omitted: many commission grids format
        the entire table (including data rows) as bold, making it an unreliable signal.

        Args:
            sheet: openpyxl worksheet object
            merged_ranges: List of MergedRange objects
            visible_cols: List of visible column indices

        Returns:
            List of 1-indexed row numbers detected as headers, sorted ascending.
        """
        import re

        header_candidates = set()

        # Signal 1: horizontal merged cells in the first 10 rows
        for mr in merged_ranges:
            if (
                mr.start[0] <= 10
                and mr.start[0] == mr.end[0]
                and mr.end[1] > mr.start[1]
            ):
                header_candidates.add(mr.start[0])

        if not header_candidates:
            return []

        # Signal 2: rows following the last merged-cell header that contain only
        # non-numeric values (label rows like "150cc", "ACT", "SCOOTER").
        # Empty rows are skipped (they appear between merged title rows and label rows).
        # Scanning stops when numeric data is found or we've gone 8 rows past the last merge.
        last_merged_header = max(header_candidates)
        number_re = re.compile(r"^\s*-?\d+(\.\d+)?\s*$")
        row = last_merged_header + 1
        scan_limit = min(last_merged_header + 8, sheet.max_row)
        while row <= scan_limit:
            values = [
                sheet.cell(row, col).value
                for col in visible_cols
                if sheet.cell(row, col).value is not None
            ]
            if not values:
                # Empty row — skip it, don't stop scanning
                row += 1
                continue
            all_non_numeric = all(not number_re.match(str(v)) for v in values)
            if all_non_numeric:
                header_candidates.add(row)
                row += 1
            else:
                break

        return sorted(list(header_candidates))

    def generate_metadata(
        self,
        sheet,
        sheet_name: str,
        merged_ranges: List[MergedRange],
        visible_rows: List[int],
        visible_cols: List[int],
        hidden_rows: List[int],
        hidden_cols: List[int],
    ) -> SheetMetadata:
        """
        Generate essential metadata for sheet.

        Args:
            sheet: openpyxl worksheet object
            sheet_name: Name of the sheet
            merged_ranges: List of MergedRange objects
            visible_rows: List of visible row indices
            visible_cols: List of visible column indices
            hidden_rows: List of hidden row indices
            hidden_cols: List of hidden column indices

        Returns:
            SheetMetadata object
        """
        # Extract bold cells
        bold_cells = self.extract_bold_cells(sheet, visible_rows, visible_cols)

        # Detect header rows
        header_rows = self.detect_header_rows(sheet, merged_ranges, visible_cols)

        # Build metadata
        metadata = SheetMetadata(
            sheet_name=sheet_name,
            dimensions={
                "rows": len(visible_rows),
                "cols": len(visible_cols),
                "original_rows": sheet.max_row,
                "original_cols": sheet.max_column,
            },
            merged_ranges=[mr.to_dict() for mr in merged_ranges],
            formatting={"bold_cells": bold_cells, "detected_header_rows": header_rows},
            excluded_content={
                "hidden_rows": hidden_rows,
                "hidden_columns": hidden_cols,
                "note": "Hidden rows/columns were excluded from CSV",
            },
        )

        return metadata

    @staticmethod
    def sanitize_filename(name: str) -> str:
        """
        Sanitize filename for safe filesystem usage.

        - Replace spaces with underscores
        - Remove special characters except _-
        - Keep alphanumeric characters

        Args:
            name: Original filename or sheet name

        Returns:
            Sanitized filename
        """
        # Replace spaces with underscores
        sanitized = name.replace(" ", "_")

        # Remove special characters (keep only alphanumeric, underscore, hyphen, period)
        sanitized = re.sub(r"[^a-zA-Z0-9_\-.]", "", sanitized)

        # Remove multiple consecutive underscores
        sanitized = re.sub(r"_+", "_", sanitized)

        return sanitized

    def _read_csv_preview(self, csv_path: Path, lines: int = 20) -> str:
        """
        Read first N lines of CSV for classification preview.

        Args:
            csv_path: Path to CSV file
            lines: Number of lines to read (default: 20)

        Returns:
            CSV preview as string
        """
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                preview_lines = []
                for i, line in enumerate(f):
                    if i >= lines:
                        break
                    preview_lines.append(line)
                return "".join(preview_lines)
        except Exception as e:
            return f"Error reading CSV preview: {str(e)}"

    def process_sheet(
        self, excel_path: str, sheet_name: str, output_dir: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        Complete pipeline for processing one sheet.

        Steps:
        1. Detect hidden content
        2. Get visible ranges
        3. Detect merged ranges
        4. Expand merged cells
        5. Generate CSV
        6. Classify sheet (if enabled)
        7. Generate metadata JSON

        Args:
            excel_path: Path to Excel file
            sheet_name: Name of the sheet
            output_dir: Directory for output files (default: self.output_dir)

        Returns:
            Dictionary with processing results
        """
        excel_path = Path(excel_path)
        if output_dir is None:
            output_dir = self.output_dir

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Load workbook
        wb = openpyxl.load_workbook(excel_path, data_only=True)
        sheet = wb[sheet_name]

        # Tab visibility ('visible' | 'hidden' | 'veryHidden'). Captured here
        # rather than from a separate workbook open so it survives every call
        # path into process_sheet, including an explicit sheet_names list.
        # Downstream (indexer -> sheet_classifications -> routing) uses it to
        # keep hidden sheets out of extraction; see
        # cins.processors.core.sheet_visibility.
        sheet_state = (
            getattr(sheet, "sheet_state", VISIBLE_SHEET_STATE) or VISIBLE_SHEET_STATE
        )

        # Step 1: Detect hidden content
        hidden_rows = self.detect_hidden_rows(sheet)
        hidden_cols = self.detect_hidden_columns(sheet)

        # Step 2: Get visible ranges
        visible_rows, visible_cols = self.get_visible_ranges(sheet)

        # Step 3: Detect merged ranges
        merged_ranges = self.detect_merged_ranges(sheet)

        # Step 4: Expand merged cells
        expanded_grid = self.expand_merged_cells(
            sheet, merged_ranges, visible_rows, visible_cols
        )

        # Step 5: Generate output filenames
        sanitized_filename = self.sanitize_filename(excel_path.stem)
        sanitized_sheetname = self.sanitize_filename(sheet_name)
        base_name = f"{sanitized_filename}_{sanitized_sheetname}"

        csv_path = output_dir / f"{base_name}.csv"
        metadata_path = output_dir / f"{base_name}_metadata.json"

        # Step 6: Write CSV
        self.grid_to_csv(expanded_grid, visible_rows, visible_cols, csv_path)

        # Step 7: Generate metadata
        metadata = self.generate_metadata(
            sheet,
            sheet_name,
            merged_ranges,
            visible_rows,
            visible_cols,
            hidden_rows,
            hidden_cols,
        )

        # Step 8: Classify sheet (if enabled)
        classification = None
        if self.classifier:
            try:
                csv_preview = self._read_csv_preview(csv_path, lines=20)
                result_obj = self.classifier.classify_sheet(
                    sheet_name=sheet_name,
                    csv_preview=csv_preview,
                    metadata=metadata.to_dict(),
                )
                classification = result_obj.to_dict()
            except Exception as e:
                print(
                    f"Warning: Sheet classification failed for '{sheet_name}': {str(e)}"
                )

        # Step 8b: deterministic self-declared-indicative override.
        #
        # Runs AFTER the LLM, never instead of it. The LLM verdict is
        # diagnostic evidence — "said commission_data at 1.0, overridden" is
        # what tells a future reader the prompt change did not take — so it is
        # preserved rather than short-circuited. And it runs independently of
        # `self.classifier`, because a sheet whose classify call threw is
        # exactly the one that most needs a label: with none, it lands in no
        # bucket and the empty-commission fallback resurrects it.
        marker = scan_csv_file(csv_path)
        if marker:
            classification = _apply_indicative_override(
                classification, sheet_name, marker
            )

        if classification:
            metadata.classification = classification

        # Step 9: Write metadata JSON
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata.to_dict(), f, indent=2, ensure_ascii=False)

        wb.close()

        # Return results
        result = {
            "sheet_name": sheet_name,
            "sheet_state": sheet_state,
            "csv_path": str(csv_path),
            "metadata_path": str(metadata_path),
            "rows": len(visible_rows),
            "cols": len(visible_cols),
            "merged_ranges": len(merged_ranges),
            "hidden_rows": len(hidden_rows),
            "hidden_cols": len(hidden_cols),
        }

        # Add classification to result if available
        if classification:
            result["classification"] = classification

        return result

    def process_workbook(
        self,
        excel_path: str,
        output_dir: Optional[Path] = None,
        sheet_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Process entire Excel workbook - all sheets or specified sheets.

        Args:
            excel_path: Path to Excel file
            output_dir: Directory for output files (default: self.output_dir)
            sheet_names: List of sheet names to process (default: all sheets)

        Returns:
            Dictionary with processing results for all sheets
        """
        excel_path = Path(excel_path)
        if output_dir is None:
            output_dir = self.output_dir

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Load workbook to get sheet names
        wb = openpyxl.load_workbook(excel_path, data_only=True, read_only=True)

        if sheet_names is None:
            sheet_names = wb.sheetnames

        wb.close()

        # Process each sheet
        results = {
            "input_file": str(excel_path),
            "num_sheets": len(sheet_names),
            "sheets": [],
        }

        for sheet_name in sheet_names:
            sheet_result = self.process_sheet(excel_path, sheet_name, output_dir)
            results["sheets"].append(sheet_result)

        return results


def main():
    """CLI entry point for Excel to CSV conversion."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python excel_to_csv.py <excel_file> [output_dir]")
        print("\nExample:")
        print("  python excel_to_csv.py input.xlsx")
        print("  python excel_to_csv.py input.xlsx ./custom_output")
        sys.exit(1)

    excel_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    if not Path(excel_path).exists():
        print(f"Error: File not found: {excel_path}")
        sys.exit(1)

    # Process workbook
    converter = ExcelToCSVConverter(output_dir=output_dir)

    print(f"Processing: {excel_path}")
    print(f"Output directory: {converter.output_dir.absolute()}")
    print("=" * 80)

    results = converter.process_workbook(excel_path)

    print(f"\n{'=' * 80}")
    print(f"✓ Processing complete: {results['num_sheets']} sheets processed")
    print(f"{'=' * 80}\n")

    for i, sheet_result in enumerate(results["sheets"], 1):
        print(f"{i}. Sheet: {sheet_result['sheet_name']}")
        print(
            f"   Dimensions: {sheet_result['rows']} rows × {sheet_result['cols']} cols"
        )
        print(f"   Merged ranges: {sheet_result['merged_ranges']}")
        print(
            f"   Hidden content: {sheet_result['hidden_rows']} rows, {sheet_result['hidden_cols']} cols"
        )
        print(f"   CSV: {Path(sheet_result['csv_path']).name}")
        print(f"   Metadata: {Path(sheet_result['metadata_path']).name}")
        print()


if __name__ == "__main__":
    main()
