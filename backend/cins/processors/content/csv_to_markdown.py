"""
CSV to Markdown Table Converter

Converts CSV content to GitHub-Flavored Markdown (GFM) table format for better
readability and LLM parsing.

This utility is used in the CINS indexing pipeline to convert Excel sheets
(via CSV) into markdown tables for storage in the input-indexed/ directory.
"""

import csv
import io
from typing import List


def csv_to_markdown(csv_content: str) -> str:
    """
    Convert CSV string to markdown table format.

    Features:
    - Standard GFM table syntax with pipe separators
    - Escapes pipe characters in cell content (| → \\|)
    - Handles empty cells (rendered as empty)
    - Preserves all rows including empty ones
    - No header detection (treats all rows equally)

    Args:
        csv_content: Raw CSV content as string

    Returns:
        Markdown formatted table string

    Example:
        >>> csv_content = "Name,Age,City\\nAlice,30,NYC\\nBob,25,LA"
        >>> print(csv_to_markdown(csv_content))
        | Name | Age | City |
        | --- | --- | --- |
        | Alice | 30 | NYC |
        | Bob | 25 | LA |
    """
    if not csv_content or not csv_content.strip():
        return "[Empty table]"

    # Parse CSV content
    csv_reader = csv.reader(io.StringIO(csv_content))
    rows = list(csv_reader)

    if not rows:
        return "[Empty table]"

    # Determine column count from first row
    max_cols = max(len(row) for row in rows) if rows else 0

    if max_cols == 0:
        return "[Empty table]"

    # Build markdown table
    markdown_lines = []

    for row_idx, row in enumerate(rows):
        # Pad row to match max columns (in case of jagged rows)
        padded_row = row + [''] * (max_cols - len(row))

        # Deduplicate consecutive identical non-empty cells (Excel merged cells expand
        # by repeating the same value across all covered columns — collapse them to one).
        deduped = []
        prev = None
        for cell in padded_row:
            val = str(cell) if cell else ''
            if val and val == prev:
                deduped.append('')
            else:
                deduped.append(val)
                prev = val if val else prev

        # Escape pipe characters in cell content
        escaped_cells = [
            cell.replace('|', '\\|') for cell in deduped
        ]

        # Create markdown row
        md_row = '| ' + ' | '.join(escaped_cells) + ' |'
        markdown_lines.append(md_row)

        # Add separator after first row (header separator)
        if row_idx == 0:
            separator = '| ' + ' | '.join(['---'] * max_cols) + ' |'
            markdown_lines.append(separator)

    return '\n'.join(markdown_lines)


def csv_file_to_markdown(csv_file_path: str) -> str:
    """
    Convert CSV file to markdown table format.

    Args:
        csv_file_path: Path to CSV file

    Returns:
        Markdown formatted table string

    Example:
        >>> markdown = csv_file_to_markdown('data.csv')
    """
    with open(csv_file_path, 'r', encoding='utf-8') as f:
        csv_content = f.read()

    return csv_to_markdown(csv_content)


def format_wide_table(csv_content: str, max_width: int = 200) -> str:
    """
    Convert CSV to markdown with optional width warning for very wide tables.

    Args:
        csv_content: Raw CSV content as string
        max_width: Maximum recommended character width (warning only)

    Returns:
        Markdown table with optional width warning
    """
    markdown_table = csv_to_markdown(csv_content)

    # Check if any line exceeds max width
    lines = markdown_table.split('\n')
    max_line_length = max(len(line) for line in lines) if lines else 0

    if max_line_length > max_width:
        warning = (
            f"⚠️ **Note**: This table is very wide ({max_line_length} characters). "
            f"Consider viewing in a wide viewport.\n\n"
        )
        return warning + markdown_table

    return markdown_table


# Example usage and testing
if __name__ == "__main__":
    # Example 1: Simple table
    csv1 = """Name,Age,City
Alice,30,NYC
Bob,25,LA
Charlie,35,SF"""

    print("Example 1: Simple Table")
    print(csv_to_markdown(csv1))
    print()

    # Example 2: Table with empty cells
    csv2 = """Product,Price,Stock
Widget,19.99,
Gadget,,100
Tool,29.99,50"""

    print("Example 2: Table with Empty Cells")
    print(csv_to_markdown(csv2))
    print()

    # Example 3: Table with pipe characters
    csv3 = """Feature,Description
Feature A,Uses | as separator
Feature B,Normal text"""

    print("Example 3: Table with Pipe Characters")
    print(csv_to_markdown(csv3))
    print()

    # Example 4: Table with NA values (common in CINS data)
    csv4 = """NA,NA,NA,NA
NA,Retail Tractor Monthly COA Grid,Commission %,State
NA,Upto 3 lac,0.26,GUJARAT
NA,>3 lacs,0.275,JHARKHAND"""

    print("Example 4: Table with NA Values")
    print(csv_to_markdown(csv4))
    print()

    # Example 5: Very wide table
    csv5 = """A,B,C,D,E,F,G,H,I,J,K,L,M,N,O,P,Q,R,S,T
1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20"""

    print("Example 5: Wide Table with Warning")
    print(format_wide_table(csv5, max_width=100))
