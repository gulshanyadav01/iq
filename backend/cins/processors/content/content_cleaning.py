#!/usr/bin/env python3
"""
Content Cleaning Module

Provides Pass 0A (email cleaning) and Pass 0B (master data cleaning) functionality
for use during content indexing stage.

These functions use LLM to clean and normalize content before it's cached for extraction.
"""

import json

from cins.schema.extraction_passes import (
    EmailCleaningExtraction,
    MasterDataCleaningExtraction,
    CSVTableDetectionExtraction,
)


def clean_email_content(raw_content: str, service) -> tuple[str, dict]:
    """
    Pass 0A: Clean email content by removing noise.

    Args:
        raw_content: Raw email content as string
        service: LLM service instance

    Returns:
        tuple: (cleaned_content, cleaning_metadata)
    """
    from cins.processors.core.llm_extract import (
        get_pass0a_email_cleaning_schema,
        get_pass0a_email_cleaning_prompt,
    )

    schema = get_pass0a_email_cleaning_schema()
    prompt = get_pass0a_email_cleaning_prompt()

    if not schema:
        # Fallback: return content as-is if schema not available
        return raw_content, {"error": "Schema not available", "fallback": True}

    # Build config from the running service so config and provider match.
    config = service.config_for_extraction()
    config.response_json_schema = schema
    config.system_instruction = prompt

    # Call LLM
    result_json = service.generate_content(raw_content, config=config)

    # Parse and validate
    try:
        result_dict = json.loads(result_json)
        email_cleaning = EmailCleaningExtraction.model_validate(result_dict)

        return email_cleaning.cleaned_content, {
            "removed_sections": email_cleaning.removed_sections,
            "tables_found": email_cleaning.tables_found,
            "attachments_mentioned": email_cleaning.attachments_mentioned,
            "cleaning_notes": email_cleaning.cleaning_notes,
        }
    except (json.JSONDecodeError, Exception) as e:
        # Fallback: return content as-is if parsing fails
        return raw_content, {"error": str(e), "fallback": True}


def detect_csv_tables(
    csv_content: str,
    sheet_name: str,
    service,
    header_row_count: int = 0,
) -> tuple[list, dict]:
    """
    Pass 0C: Detect multiple tables within a CSV sheet using row boundary detection.

    The LLM receives the CSV with line numbers prepended and returns only
    start_row/end_row boundaries per table. Python slices the original CSV
    lines using those boundaries.

    Args:
        csv_content: CSV content of the sheet as a string
        sheet_name: Name of the sheet (used as context in the prompt)
        service: LLM service instance
        header_row_count: Number of shared column-header rows at the top of
            the sheet (from `metadata.formatting.detected_header_rows`). Used
            by the de-overlap pass to reconstruct per-sub-table slices when
            the LLM emits cumulative end_row values under the Pass 0C
            "Shared Header Rule" (see `_dedupe_overlapping_table_ranges`).

    Returns:
        tuple: (tables_list, detection_metadata)
            tables_list: List of dicts with keys:
                - table_index, table_name, table_csv_content (sliced by Python),
                  description, confidence, detection_notes
            detection_metadata: Summary dict for metadata.json
    """
    from cins.processors.core.llm_extract import (
        get_pass0c_table_detection_schema,
        get_pass0c_table_detection_prompt,
    )

    schema = get_pass0c_table_detection_schema()
    prompt = get_pass0c_table_detection_prompt()

    if not schema:
        return [], {"error": "Schema not available", "fallback": True}

    # Split content into lines (keep original for slicing)
    original_lines = csv_content.splitlines()
    total_lines = len(original_lines)

    # Prepend line numbers for the LLM
    MAX_LINES_FOR_LLM = 500
    KEEP_HEAD = 200
    KEEP_TAIL = 50

    if total_lines <= MAX_LINES_FOR_LLM:
        numbered_lines = [f"{i + 1}: {line}" for i, line in enumerate(original_lines)]
        numbered_content = "\n".join(numbered_lines)
    else:
        head = [f"{i + 1}: {line}" for i, line in enumerate(original_lines[:KEEP_HEAD])]
        tail_start = total_lines - KEEP_TAIL
        tail = [
            f"{i + 1}: {line}"
            for i, line in enumerate(original_lines[tail_start:], start=tail_start)
        ]
        omitted = total_lines - KEEP_HEAD - KEEP_TAIL
        numbered_content = (
            "\n".join(head)
            + f"\n... [{omitted} rows omitted for brevity — middle rows follow same structure] ...\n"
            + "\n".join(tail)
        )

    user_message = f"Sheet name: {sheet_name}\n\n{numbered_content}"

    config = service.config_for_extraction()
    config.response_json_schema = schema
    config.system_instruction = prompt

    result_json = service.generate_content(user_message, config=config)

    try:
        result_dict = json.loads(result_json)
        detection = CSVTableDetectionExtraction.model_validate(result_dict)

        # Build boundary records first; reshape any cumulative/overlapping
        # ranges before slicing. See `_dedupe_overlapping_table_ranges`.
        boundary_records = []
        for table in detection.tables:
            start = max(1, min(table.start_row, total_lines))
            end = max(start, min(table.end_row, total_lines))
            boundary_records.append(
                {
                    "table_index": table.table_index,
                    "table_name": table.table_name,
                    "table_type": table.table_type,
                    "description": table.description,
                    "confidence": table.confidence,
                    "detection_notes": table.detection_notes,
                    "start_row": start,
                    "end_row": end,
                    "start_row_original": start,
                    "end_row_original": end,
                    "start_col": table.start_col,
                    "end_col": table.end_col,
                    # data_ranges: list of [start, end] 1-based inclusive pairs.
                    # Default: single contiguous slice == [start_row, end_row].
                    # Reshape pass may rewrite this to "header band + own data
                    # band" for cumulative siblings.
                    "data_ranges": [[start, end]],
                }
            )

        reshape_notes = _dedupe_overlapping_table_ranges(
            boundary_records, header_row_count=header_row_count
        )

        tables_list = []
        for record in boundary_records:
            slice_lines = []
            for r_start, r_end in record["data_ranges"]:
                slice_lines.extend(original_lines[r_start - 1 : r_end])

            start_col = record["start_col"]
            end_col = record["end_col"]
            if start_col is not None or end_col is not None:
                import csv as _csv
                import io as _io

                start_col_idx = (max(1, start_col) - 1) if start_col else 0
                end_col_idx = end_col  # None means "to end"

                sliced_lines = []
                for line in slice_lines:
                    try:
                        row = next(_csv.reader(_io.StringIO(line)))
                        sliced = row[start_col_idx:end_col_idx]
                        buf = _io.StringIO()
                        _csv.writer(buf).writerow(sliced)
                        sliced_lines.append(buf.getvalue().rstrip("\r\n"))
                    except Exception:
                        sliced_lines.append(line)
                table_csv_content = "\n".join(sliced_lines)
            else:
                table_csv_content = "\n".join(slice_lines)

            record["table_csv_content"] = table_csv_content
            tables_list.append(record)

        detection_notes = detection.detection_notes
        if reshape_notes:
            detection_notes = (
                f"{detection_notes} | {reshape_notes}" if detection_notes else reshape_notes
            )

        return tables_list, {
            "total_tables_found": detection.total_tables_found,
            "sheet_has_multiple_tables": detection.sheet_has_multiple_tables,
            "detection_notes": detection_notes,
        }
    except (json.JSONDecodeError, Exception) as e:
        return [], {"error": str(e), "fallback": True}


def _dedupe_overlapping_table_ranges(records: list, header_row_count: int = 0) -> str:
    """
    De-duplicate overlapping/cumulative table row ranges emitted by Pass 0C.

    Why this exists
    ---------------
    The Pass 0C prompt (`pass0c_table_detection_prompt.md`) contains a
    "Shared Header Rule" instructing the LLM to anchor every sub-table's
    `start_row` to the shared column-header rows so each slice is
    "self-contained". For a sheet with N stacked sub-tables that share one
    header block (e.g. one Two-Wheeler commission grid per state, all under
    one column-header row), the LLM tends to emit:

        Table 1: start=4,  end=9      (header + state A data)
        Table 2: start=4,  end=14     (header + state A + state B data)
        Table 3: start=4,  end=18     (header + state A + state B + state C)
        ...
        Table N: start=4,  end=LAST   (header + every prior state's data)

    The downstream codegen iterates every detected table and emits one
    rule per (data_row x column). Cumulative end_row → each state's rows
    are counted in every subsequent table → rule count explodes
    (arithmetic-progression sum). Observed: a single Two-Wheeler grid
    produced 24,006 rules instead of ~3,000 — root cause for the
    investigation that prompted this helper.

    What this function does
    -----------------------
    Walks the commission tables and reshapes their `data_ranges` so each
    sub-table covers ONLY its own data rows. Two cases:

    A) Cumulative siblings: tables share an identical `(start_row, start_col,
       end_col)` and have monotonically increasing `end_row`. For each table
       beyond the first, rewrite `data_ranges` to:

           [[start_row, start_row + header_row_count - 1],   # shared header
            [prev_table.end_row + 1, this_table.end_row]]    # own data only

       This preserves the column header on every sub-table's markdown so
       downstream codegen still sees a well-formed table, but drops the
       prior sub-tables' data rows.

    B) Plain overlap: tables share a column band, are sorted by `start_row`
       ascending, and a later sibling's `start_row` falls inside the prior
       sibling's `end_row`. Clip the prior sibling's `end_row` to
       `next.start_row - 1`. (This is the classic Option-B clip; cumulative
       siblings cannot be fixed this way because they share `start_row`.)

    Safety guards
    -------------
    1. Only `table_type == 'commission'` is reshaped. `master` and `notes`
       tables are left intact so lookup tables and trailing legal /
       disclaimer text are not truncated.
    2. Column-band aware: tables with non-overlapping `[start_col, end_col]`
       (i.e. side-by-side tables) do NOT affect each other.
    3. Sort by `start_row` then `end_row` ascending before reshaping so the
       order in the LLM response does not change the outcome.
    4. If `header_row_count <= 0`, case A is disabled (no safe way to
       reconstruct headers — fall back to clipping only).
    5. Records carry `start_row_original` / `end_row_original` so debugging
       can compare LLM-emitted vs reshaped ranges.

    Returns
    -------
    Human-readable note describing what was reshaped (or "" if nothing
    changed). Appended to `detection_notes` for traceability.
    """

    def cols_overlap(a, b) -> bool:
        a_start = a["start_col"] if a["start_col"] else 1
        a_end = a["end_col"] if a["end_col"] else 10**9
        b_start = b["start_col"] if b["start_col"] else 1
        b_end = b["end_col"] if b["end_col"] else 10**9
        return a_start <= b_end and b_start <= a_end

    def col_band_key(a) -> tuple:
        return (
            a["start_col"] if a["start_col"] else 0,
            a["end_col"] if a["end_col"] else -1,
        )

    commission_tables = [r for r in records if r["table_type"] == "commission"]
    if len(commission_tables) < 2:
        return ""

    notes: list[str] = []

    # Case A: cumulative siblings grouped by (start_row, col band).
    if header_row_count > 0:
        groups: dict = {}
        for rec in commission_tables:
            key = (rec["start_row"], col_band_key(rec))
            groups.setdefault(key, []).append(rec)
        for (start_row, _band), siblings in groups.items():
            if len(siblings) < 2:
                continue
            siblings.sort(key=lambda r: r["end_row"])
            # Any two commission tables sharing the same (start_row, col band)
            # are treated as cumulative siblings — that is the dominant Pass
            # 0C failure mode under the Shared Header Rule. We sort by
            # end_row ascending so the smallest range becomes the anchor;
            # each subsequent sibling keeps only the rows beyond its
            # predecessor.
            header_end = start_row + header_row_count - 1
            # Sanity: every sibling's end_row must be > header_end, else
            # we'd produce an empty data band.
            if any(r["end_row"] <= header_end for r in siblings):
                continue
            for i, sib in enumerate(siblings):
                if i == 0:
                    continue  # first sibling keeps its full slice
                data_start = siblings[i - 1]["end_row"] + 1
                data_end = sib["end_row"]
                if data_start > data_end:
                    continue
                sib["data_ranges"] = [
                    [start_row, header_end],
                    [data_start, data_end],
                ]
                # Logically, the table now starts at the header. Keep
                # `start_row` for display compatibility but record the
                # effective data band in end_row.
                sib["end_row"] = data_end
                notes.append(
                    f"#{sib['table_index']}(cumulative→data {data_start}-{data_end})"
                )

    # Case B: plain overlap clip — applied independently after Case A. Uses
    # current `end_row` (which Case A may have updated) so the two passes
    # compose cleanly.
    sorted_records = sorted(
        commission_tables, key=lambda r: (r["start_row"], r["end_row"], r["table_index"])
    )
    for i, current in enumerate(sorted_records):
        for nxt in sorted_records[i + 1 :]:
            if not cols_overlap(current, nxt):
                continue
            if nxt["start_row"] <= current["start_row"]:
                continue
            if nxt["start_row"] > current["end_row"]:
                break
            new_end = max(current["start_row"], nxt["start_row"] - 1)
            if new_end < current["end_row"]:
                notes.append(
                    f"#{current['table_index']}(clip {current['end_row']}→{new_end})"
                )
                current["end_row"] = new_end
                # If this record used the default contiguous range, update it.
                if len(current["data_ranges"]) == 1:
                    current["data_ranges"] = [[current["start_row"], new_end]]
            break

    if not notes:
        return ""
    return (
        "Pass 0C de-overlap reshaped commission ranges: " + ", ".join(notes)
    )


def clean_master_data_content(raw_content: str, service) -> tuple[str, dict]:
    """
    Pass 0B: Clean and normalize master data sections.

    Args:
        raw_content: Document content as string (may be cleaned by Pass 0A)
        service: LLM service instance

    Returns:
        tuple: (cleaned_content, cleaning_metadata)
    """
    from cins.processors.core.llm_extract import (
        get_pass0b_master_data_cleaning_schema,
        get_pass0b_master_data_cleaning_prompt,
    )

    schema = get_pass0b_master_data_cleaning_schema()
    prompt = get_pass0b_master_data_cleaning_prompt()

    if not schema:
        # Fallback: return content as-is if schema not available
        return raw_content, {"error": "Schema not available", "fallback": True}

    # Build config from the running service so config and provider match.
    config = service.config_for_extraction()
    config.response_json_schema = schema
    config.system_instruction = prompt

    # Call LLM
    result_json = service.generate_content(raw_content, config=config)

    # Parse and validate
    try:
        result_dict = json.loads(result_json)
        md_cleaning = MasterDataCleaningExtraction.model_validate(result_dict)

        return md_cleaning.cleaned_content, {
            "master_data_sections": md_cleaning.master_data_sections,
            "master_data_table_count": md_cleaning.master_data_table_count,
            "commission_data_sections": md_cleaning.commission_data_sections,
            "normalization_notes": md_cleaning.normalization_notes,
        }
    except (json.JSONDecodeError, Exception) as e:
        # Fallback: return content as-is if parsing fails
        return raw_content, {"error": str(e), "fallback": True}
