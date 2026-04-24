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
    from cins.processors.llm_services.factory import get_config_for_extraction

    schema = get_pass0a_email_cleaning_schema()
    prompt = get_pass0a_email_cleaning_prompt()

    if not schema:
        # Fallback: return content as-is if schema not available
        return raw_content, {"error": "Schema not available", "fallback": True}

    # Build config
    config = get_config_for_extraction()
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


def detect_csv_tables(csv_content: str, sheet_name: str, service) -> tuple[list, dict]:
    """
    Pass 0C: Detect multiple tables within a CSV sheet using row boundary detection.

    The LLM receives the CSV with line numbers prepended and returns only
    start_row/end_row boundaries per table. Python slices the original CSV
    lines using those boundaries.

    Args:
        csv_content: CSV content of the sheet as a string
        sheet_name: Name of the sheet (used as context in the prompt)
        service: LLM service instance

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
    from cins.processors.llm_services.factory import get_config_for_extraction

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

    config = get_config_for_extraction()
    config.response_json_schema = schema
    config.system_instruction = prompt

    result_json = service.generate_content(user_message, config=config)

    try:
        result_dict = json.loads(result_json)
        detection = CSVTableDetectionExtraction.model_validate(result_dict)

        tables_list = []
        for table in detection.tables:
            # Clamp row numbers to valid range
            start = max(1, min(table.start_row, total_lines)) - 1  # 0-indexed
            end = max(1, min(table.end_row, total_lines))  # exclusive end for slice

            # Slice original CSV lines (deterministic, no LLM hallucination)
            table_lines = original_lines[start:end]

            if table.start_col is not None or table.end_col is not None:
                import csv as _csv
                import io as _io

                start_idx = (max(1, table.start_col) - 1) if table.start_col else 0
                end_idx = table.end_col  # None means "to end"

                sliced_lines = []
                for line in table_lines:
                    try:
                        row = next(_csv.reader(_io.StringIO(line)))
                        sliced = row[start_idx:end_idx]
                        buf = _io.StringIO()
                        _csv.writer(buf).writerow(sliced)
                        sliced_lines.append(buf.getvalue().rstrip("\r\n"))
                    except Exception:
                        sliced_lines.append(line)
                table_csv_content = "\n".join(sliced_lines)
            else:
                table_csv_content = "\n".join(table_lines)

            tables_list.append(
                {
                    "table_index": table.table_index,
                    "table_name": table.table_name,
                    "table_type": table.table_type,
                    "table_csv_content": table_csv_content,
                    "description": table.description,
                    "confidence": table.confidence,
                    "detection_notes": table.detection_notes,
                    "start_row": table.start_row,
                    "end_row": table.end_row,
                    "start_col": table.start_col,
                    "end_col": table.end_col,
                }
            )

        return tables_list, {
            "total_tables_found": detection.total_tables_found,
            "sheet_has_multiple_tables": detection.sheet_has_multiple_tables,
            "detection_notes": detection.detection_notes,
        }
    except (json.JSONDecodeError, Exception) as e:
        return [], {"error": str(e), "fallback": True}


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
    from cins.processors.llm_services.factory import get_config_for_extraction

    schema = get_pass0b_master_data_cleaning_schema()
    prompt = get_pass0b_master_data_cleaning_prompt()

    if not schema:
        # Fallback: return content as-is if schema not available
        return raw_content, {"error": "Schema not available", "fallback": True}

    # Build config
    config = get_config_for_extraction()
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
