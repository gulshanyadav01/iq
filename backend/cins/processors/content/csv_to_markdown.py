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
import re
from typing import List, Tuple

# A header cell that is a bare number / percentage is never a *label* — it is a
# rate value from a data row that Pass 0C swallowed into the header band (see
# _build_compound_headers). Anchored and total so "150cc", "UPTO 3.5T" and
# "1_GCCV_3W" stay labels.
_NUMERIC_HEADER_CELL_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?\s*%?$")

# Minimum rate-shaped cells before a row inside the header band is judged a
# swallowed DATA row (see effective_header_row_count). Three is enough to be
# unambiguous on a real commission grid and far above the one or two stray
# numbers a genuine label row might carry.
_SWALLOWED_ROW_MIN_RATES = 3

# Fraction of a row's non-empty cells that must be rate-shaped for it to read as
# data rather than labels.
_SWALLOWED_ROW_RATE_SHARE = 0.5


def _is_rate_like(cell: str) -> bool:
    """True for a cell that is a commission RATE, not a numeric label.

    Deliberately tighter than the gate's ``_is_rate``: a rate is a fraction
    (``0.325``, ``0``) or explicitly %-suffixed (``30%``). Bare integers above 1
    are label-shaped — a ``150`` cc band, a ``2025`` year, a ``1500`` CC ceiling —
    and must never let a real header row be mistaken for data.
    """
    val = cell.strip()
    if not _NUMERIC_HEADER_CELL_RE.match(val):
        return False
    if val.endswith("%"):
        return True
    try:
        return abs(float(val)) <= 1
    except ValueError:
        return False


def _is_swallowed_data_row(row: List[str]) -> bool:
    """True when a row inside the header band is really the grid's first DATA row."""
    non_empty = [c.strip() for c in row if c.strip()]
    if not non_empty:
        return False
    rates = [c for c in non_empty if _is_rate_like(c)]
    return len(rates) >= _SWALLOWED_ROW_MIN_RATES and len(
        rates
    ) >= _SWALLOWED_ROW_RATE_SHARE * len(non_empty)


def effective_header_row_count(rows: List[List[str]], header_row_count: int) -> int:
    """Trim DATA rows that Pass 0C / Excel header detection swallowed into the
    header band, returning the header row count actually to use.

    Chola's GCCV sheet reports a 5-row header, but rows 4 and 5 of that band are
    the real `1_GCCV_3W` and `1_GCCV_3W[Electric]` commission rows. Collapsing
    them into the compound label buried 64 rate cells inside header strings like
    `AP/TS > ACT > 0.325 > 0.325`, reachable only if the generated Pass-3
    extractor happened to implement a bespoke recovery loop — which is a coin
    flip across codegen runs, and one run emitted zero goods-3-wheeler rules as a
    result. Handing codegen ordinary positional data rows removes the coin flip.

    The scan stops at the FIRST swallowed row: everything below it in the band is
    data too. At least one header row is always kept.
    """
    hrc = max(0, int(header_row_count or 0))
    if hrc <= 1:
        return hrc
    for idx in range(1, min(hrc, len(rows))):
        if _is_swallowed_data_row(rows[idx]):
            return idx
    return hrc


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

    Vertical-repeat suppression is skipped for **numeric** cells. Grids whose
    first data rows get merged into the header band (Chola's GCCV sheet swallows
    `1_GCCV_3W` and `1_GCCV_3W[Electric]` into the 4-row header) carry those
    rows' rates as trailing `>` segments. Two consecutive rows can legitimately
    publish the SAME rate — AP/TS ACT is 0.325 for both the base and the electric
    3-wheeler — and suppressing the repeat collapsed `AP/TS > ACT > 0.325 > 0.325`
    to `AP/TS > ACT > 0.325`. Downstream codegen aligns the i-th trailing value
    to the i-th sub-class segment, so the electric twin silently vanished for
    every state whose two rates happened to be equal (18 of 20 state groups on
    the July 2025 Chola grid). A numeric cell is a value, never a merged parent
    label, so keeping its repeat is safe for every sheet.

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
            val = row[col].strip() if col < len(row) else ""
            # Include this value if non-empty and either it differs from the
            # cell above, or it is a numeric rate (a repeated value, not a
            # repeated label).
            if val and (val != prev or _NUMERIC_HEADER_CELL_RE.match(val)):
                parts.append(val)
            prev = val if val else prev  # blank cell doesn't reset the group
        compound.append(" > ".join(parts))

    return compound


# A banner must head at least this many columns before it is treated as one. A
# segment spanning two or three columns is a real parent label (a make group,
# a coverage group), not a table-wide banner.
_BANNER_MIN_COLUMNS = 4

# ...and it must head at least this share of the table's HIERARCHICAL columns
# (see below). Not 100%: the side-by-side split's column bands do NOT line up
# with the banner bands. On the real Chola `PROP TW Apr-26` sheet, block 1 is 39
# columns of "TW Make Grid for the month of Apr 26" plus ONE that leaked in from
# the neighbouring "TW Electric" grid — an all-columns rule declines on that
# single dissenter and the whole fix does nothing.
_BANNER_DOMINANCE = 0.8

# A table needs at least this many `a > b > c` columns before it is judged to
# have a hierarchy a banner could be the root of. Below it, a shared leading
# segment IS the grouping dimension (a single-make grid's `Hero OEM > <band>`,
# a `GVW > <band>` ladder) and stripping it would be the very defect this fixes.
_MIN_HIERARCHICAL_COLUMNS = 2


def _strip_shared_header_banner(labels: List[str]) -> Tuple[List[str], List[str]]:
    """Drop the leading ">" segment shared by the bulk of a table's columns.

    `_build_compound_headers` suppresses a label repeated VERTICALLY (a merged
    parent above its children). It has no notion of one repeated HORIZONTALLY,
    across every column — which is exactly what a merged banner row spanning the
    whole table produces once `expand_merged_cells` copies it into each cell:

        TW Make Grid for the month of Apr 26 > Hero OEM > 150cc
        TW Make Grid for the month of Apr 26 > Hero OEM > SCOOTER

    A banner discriminates nothing (it heads the whole table by construction) and
    it shifts every real segment one position right. That broke the Two-Wheeler
    grid: the carrier overlay reads `part[0]` as the vehicle make and `part[1]` as
    the CC band, so with the banner in front NO column parsed as a make, codegen
    dropped the entire make grid, and the sheet shipped 272 make-less electric
    rules instead of ~1400 make x CC-band rules (doc 1457d5e8, `PROP TW Apr-26`).
    The same workbook's unbannered sheets were always correct, which is why this
    read as an intermittent LLM defect for so long.

    Three guards keep a genuinely meaningful shared parent from being eaten:

    - The table must have at least `_MIN_HIERARCHICAL_COLUMNS` columns of depth
      >= 3, and the banner must head `_BANNER_DOMINANCE` of THOSE. A banner is
      the root of a `banner > make > band` hierarchy; a table publishing one
      make (`Hero OEM > 150cc`, `Hero OEM > SCOOTER`, ...) or one dimension
      (`GVW > UPTO 3.5T`) is only two deep, so its grouping segment can never be
      mistaken for a banner however many columns it heads.
    - It must still span `_BANNER_MIN_COLUMNS` columns of the table overall.
    - The strip is by whole `>` segment, never a substring, so a column whose
      first segment merely STARTS with the banner text is untouched.

    Only the columns that actually carry the banner are stripped; a dissenting
    column keeps its label, where the segment does discriminate.

    **At most ONE segment is ever stripped.** No sheet in the corpus stacks two
    banner rows, and every extra pass is another chance to eat a real dimension
    on a grid shape nobody has looked at. A silently eaten make row misprices.

    Returns:
        (labels with the banner segment removed, [banner] or []).
        Columns that were ONLY the banner (the blank spacer columns between make
        groups) become "", this module's existing "no label" convention.
    """
    split = [label.split(" > ") if label else [] for label in labels]
    populated = [parts for parts in split if parts]
    if len(populated) < _BANNER_MIN_COLUMNS:
        return list(labels), []

    # Dominance is measured over the columns that HAVE a hierarchy, not over
    # every column. A banner is the root of one: `banner > make > band`. Key
    # columns (`banner > State`), spacer columns and any neighbouring grid the
    # side-by-side split lumped in are all shallower, and counting them buries
    # the signal — on the live electric block `TW Electric` heads 7 of 12
    # columns (58%, below the floor) but 4 of the 4 three-deep ones.
    deep = [parts for parts in populated if len(parts) >= 3]
    if len(deep) < _MIN_HIERARCHICAL_COLUMNS:
        return list(labels), []

    counts: dict = {}
    for parts in deep:
        counts[parts[0]] = counts.get(parts[0], 0) + 1
    banner, deep_heads = max(counts.items(), key=lambda kv: kv[1])

    if deep_heads < len(deep) * _BANNER_DOMINANCE:
        return list(labels), []
    # ...but it must still span a real slice of the table. Without this a
    # three-deep label on a couple of columns of a wide grid would qualify.
    if sum(1 for p in populated if p[0] == banner) < _BANNER_MIN_COLUMNS:
        return list(labels), []

    stripped = [
        " > ".join(parts[1:] if parts and parts[0] == banner else parts)
        for parts in split
    ]
    return stripped, [banner]


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
    padded = [row + [""] * (max_cols - len(row)) for row in rows]

    # Push any DATA rows that got swallowed into the header band back into the
    # data section, so codegen reads them positionally like every other row.
    header_row_count = effective_header_row_count(padded, header_row_count)

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
                        deduped.append("")
                    else:
                        deduped.append(val)
                        prev = val
                cells = deduped
            else:
                cells = [cell.strip() for cell in row]

            escaped = [c.replace("|", "\\|") for c in cells]
            markdown_lines.append("| " + " | ".join(escaped) + " |")
            if row_idx == 0:
                markdown_lines.append("| " + " | ".join(["---"] * max_cols) + " |")

        return "\n".join(markdown_lines)

    # ── Multi-level headers: collapse into compound labels ─────────────────
    header_rows = padded[:header_row_count]
    data_rows = padded[header_row_count:]

    compound_labels = _build_compound_headers(header_rows)
    compound_labels, banners = _strip_shared_header_banner(compound_labels)

    markdown_lines = []

    # The banner is not discarded — some are load-bearing (a grid whose columns
    # all sit under a meaningful "GVW" parent). It is re-emitted as a caption so
    # the text stays in the pass-3 content codegen reads, while the per-column
    # labels keep the shape the carrier overlays parse positionally.
    if banners:
        # Commas are neutralised because count_rate_bearing_rows_markdown splits
        # a pipe-less line on them, and a banner ending in a bare number would
        # otherwise read as a rate cell and inflate the coverage gate's expected
        # row count by one per table.
        caption = banners[0].replace("|", "\\|").replace(",", " -")
        markdown_lines.append(f"_Section: {caption}_")
        markdown_lines.append("")

    # Synthetic single header row
    escaped_header = [label.replace("|", "\\|") for label in compound_labels]
    markdown_lines.append("| " + " | ".join(escaped_header) + " |")
    markdown_lines.append("| " + " | ".join(["---"] * max_cols) + " |")

    # Data rows — no deduplication, values are preserved as-is
    for row in data_rows:
        cells = [cell.strip() for cell in row]
        escaped = [c.replace("|", "\\|") for c in cells]
        markdown_lines.append("| " + " | ".join(escaped) + " |")

    return "\n".join(markdown_lines)


def csv_file_to_markdown(csv_file_path: str) -> str:
    """Convert CSV file to markdown table format."""
    with open(csv_file_path, "r", encoding="utf-8") as f:
        csv_content = f.read()
    return csv_to_markdown(csv_content)


def format_wide_table(csv_content: str, max_width: int = 200) -> str:
    """Convert CSV to markdown with optional width warning for very wide tables."""
    markdown_table = csv_to_markdown(csv_content)
    lines = markdown_table.split("\n")
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
