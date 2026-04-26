"""
CSV to Markdown Table Converter

Converts CSV content to GitHub-Flavored Markdown (GFM) table format for better
readability and LLM consumption.

This utility is used in the CINS indexing pipeline to convert Excel sheets
(via CSV) into markdown tables for storage in the input-indexed/ directory.

Multi-level header collapsing
-------------------------------
Excel commission grids often have 2–4 header rows that form a hierarchy:

    Row 1:  | Hero OEM | Hero OEM | Hero OEM | TVS OEM | TVS OEM |
    Row 2:  | 150cc    | SCOOTER  | 350cc    | 150cc   | SCOOTER |

When header_row_count > 1, these rows are collapsed into a single synthetic
header row by building a compound label per column, joining non-redundant
ancestor labels top-down with " > ":

    Collapsed: | Hero OEM > 150cc | Hero OEM > SCOOTER | Hero OEM > 350cc | ...

This makes every column fully self-describing so the LLM does not need to
reconstruct the hierarchy from multiple header rows.
"""

import csv
import io
from typing import List, Optional


def _build_compound_headers(header_rows: List[List[str]]) -> List[str]:
    """
    Collapse multiple header rows into one compound label per column.

    Algorithm per column:
    - Walk top-down through header rows.
    - A cell is a *new* label if it is non-empty AND differs from the cell
      immediately above it in the same column (i.e. it is not just the
      merged-cell repetition of its parent group).
    - Collect new labels and join with " > ".
    - If no labels were collected for a column (all empty / all repeated),
      the column keeps an empty label.

    Args:
        header_rows: List of rows (each row is a list of cell strings),
                     already padded to the same length.

    Returns:
        List of compound label strings, one per column.
    """
    if not header_rows:
        return []

    num_cols = len(header_rows[0])
    compound = []

    for col in range(num_cols):
        parts = []
        prev = None
        for row in header_rows:
            val = row[col].strip() if col < len(row) else ''
            # Include this value if non-empty and not a repeat of the row above
            if val and val != prev:
                parts.append(val)
            prev = val if val else prev  # blank cell doesn't reset the group
        compound.append(' > '.join(parts))

    return compound


def csv_to_markdown(csv_content: str, header_row_count: int = 0) -> str:
    """
    Convert CSV string to markdown table format.

    When header_row_count > 1, multi-level header rows are collapsed into a
    single compound header row (e.g. "AP/TS > ACT", "Hero OEM > 150cc").
    When header_row_count <= 1, the table is rendered as-is with no
    deduplication of data rows.

    Args:
        csv_content: Raw CSV content as string.
        header_row_count: Number of leading rows that are Excel header rows.
            - 0: no special header handling, all rows rendered as data.
            - 1: single header row, rendered normally (no collapsing needed,
                 but deduplication of merged-cell repeats is applied).
            - 2+: multi-level headers are collapsed into one compound row.

    Returns:
        Markdown formatted table string.
    """
    if not csv_content or not csv_content.strip():
        return "[Empty table]"

    csv_reader = csv.reader(io.StringIO(csv_content))
    rows = list(csv_reader)

    if not rows:
        return "[Empty table]"

    max_cols = max(len(row) for row in rows) if rows else 0
    if max_cols == 0:
        return "[Empty table]"

    # Pad all rows to max_cols
    padded = [row + [''] * (max_cols - len(row)) for row in rows]

    # ── Single or no header: simple rendering ──────────────────────────────
    if header_row_count <= 1:
        markdown_lines = []
        for row_idx, row in enumerate(padded):
            is_header = row_idx < header_row_count
            if is_header:
                # Deduplicate merged-cell repeats in the single header row
                deduped = []
                prev = None
                for cell in row:
                    val = cell.strip()
                    if val and val == prev:
                        deduped.append('')
                    else:
                        deduped.append(val)
                        prev = val
                cells = deduped
            else:
                cells = [cell.strip() for cell in row]

            escaped = [c.replace('|', '\\|') for c in cells]
            markdown_lines.append('| ' + ' | '.join(escaped) + ' |')
            if row_idx == 0:
                markdown_lines.append('| ' + ' | '.join(['---'] * max_cols) + ' |')

        return '\n'.join(markdown_lines)

    # ── Multi-level headers: collapse into compound labels ─────────────────
    header_rows = padded[:header_row_count]
    data_rows = padded[header_row_count:]

    compound_labels = _build_compound_headers(header_rows)

    markdown_lines = []

    # Synthetic single header row
    escaped_header = [label.replace('|', '\\|') for label in compound_labels]
    markdown_lines.append('| ' + ' | '.join(escaped_header) + ' |')
    markdown_lines.append('| ' + ' | '.join(['---'] * max_cols) + ' |')

    # Data rows — no deduplication, values are preserved as-is
    for row in data_rows:
        cells = [cell.strip() for cell in row]
        escaped = [c.replace('|', '\\|') for c in cells]
        markdown_lines.append('| ' + ' | '.join(escaped) + ' |')

    return '\n'.join(markdown_lines)


def csv_file_to_markdown(csv_file_path: str) -> str:
    """Convert CSV file to markdown table format."""
    with open(csv_file_path, 'r', encoding='utf-8') as f:
        csv_content = f.read()
    return csv_to_markdown(csv_content)


def format_wide_table(csv_content: str, max_width: int = 200) -> str:
    """Convert CSV to markdown with optional width warning for very wide tables."""
    markdown_table = csv_to_markdown(csv_content)
    lines = markdown_table.split('\n')
    max_line_length = max(len(line) for line in lines) if lines else 0
    if max_line_length > max_width:
        warning = (
            f"**Note**: This table is very wide ({max_line_length} characters).\n\n"
        )
        return warning + markdown_table
    return markdown_table


# ── Self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Example: two-level header (state → ACT/PACK)
    csv_multi = (
        ",,AP/TS,AP/TS,TN,TN\n"
        ",,ACT,PACK,ACT,PACK\n"
        "PC,UPTO 1000 CC,,0.3,,0.25\n"
        "PC,1000 TO 1500 CC,0.2,0.3,0.2,0.25\n"
    )
    print("Multi-level header (2 header rows):")
    print(csv_to_markdown(csv_multi, header_row_count=2))
    print()

    # Example: three-level header (title → OEM → cc)
    csv_three = (
        "Title,Title,Title,Title,Title\n"
        "State,Type,Hero OEM,Hero OEM,TVS OEM\n"
        "State,Type,150cc,350cc,150cc\n"
        "AP,NEW,0.425,0,0.35\n"
        "AP,ANNUAL,0.1,0,0.1\n"
    )
    print("Three-level header (3 header rows):")
    print(csv_to_markdown(csv_three, header_row_count=3))
    print()

    # Example: single header (no collapsing)
    csv_simple = "Name,Age,City\nAlice,30,NYC\nBob,25,LA\n"
    print("Single header:")
    print(csv_to_markdown(csv_simple, header_row_count=1))
