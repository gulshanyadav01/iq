#!/usr/bin/env python3
"""Commission Data Extractor — multi-pass LLM extraction pipeline for insurance commission documents."""

import json
import sys
import time
from pathlib import Path

from cins.processors.llm_services.factory import (
    get_llm_service,
    get_config_for_extraction,
    get_config_for_code_generation,
)
from cins.schema.raw_extraction import RawExtraction
from cins.schema.extraction_passes import (
    EmailCleaningExtraction,
    MasterDataCleaningExtraction,
    CSVTableDetectionExtraction,
    MetadataExtraction,
    MasterDataExtraction,
    MasterDataEntry,
    RulesExtraction,
    ProductWithRules,
    RuleEntry,
)
from cins.validators.raw_extraction_validator import RawExtractionValidator

# Threshold for proactive code-gen mode selection in Pass 2 (master data).
CODEGEN_INPUT_SIZE_THRESHOLD_BYTES_PASS2 = 30_000  # master_data_content > 30 KB


def get_pass0a_email_cleaning_schema():
    """Get the EmailCleaningExtraction JSON schema from Pydantic model."""
    return EmailCleaningExtraction.model_json_schema()


def get_pass0a_email_cleaning_prompt():
    """Get the Pass 0A email cleaning prompt."""
    try:
        prompt_path = (
            Path(__file__).parent.parent.parent
            / "prompts"
            / "pass0a_email_cleaning_prompt.md"
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Warning: Could not load Pass 0A prompt: {e}", file=sys.stderr)
        return "Clean email content by removing headers, signatures, disclaimers, and thread history. Preserve commission data and tables."


def get_pass0b_master_data_cleaning_schema():
    """Get the MasterDataCleaningExtraction JSON schema from Pydantic model."""
    return MasterDataCleaningExtraction.model_json_schema()


def get_pass0b_master_data_cleaning_prompt():
    """Get the Pass 0B master data cleaning prompt."""
    try:
        prompt_path = (
            Path(__file__).parent.parent.parent
            / "prompts"
            / "pass0b_master_data_cleaning_prompt.md"
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Warning: Could not load Pass 0B prompt: {e}", file=sys.stderr)
        return "Clean and normalize master data sections. Separate master data from commission rules."


def get_pass0c_table_detection_schema():
    """Get the CSVTableDetectionExtraction JSON schema from Pydantic model."""
    return CSVTableDetectionExtraction.model_json_schema()


def get_pass0c_table_detection_prompt():
    """Get the Pass 0C CSV table detection prompt."""
    try:
        prompt_path = (
            Path(__file__).parent.parent.parent
            / "prompts"
            / "pass0c_table_detection_prompt.md"
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Warning: Could not load Pass 0C prompt: {e}", file=sys.stderr)
        return "Detect distinct commission table boundaries within the CSV sheet. Return start_row and end_row for each table."


def get_pass1_metadata_schema():
    """Get the MetadataExtraction JSON schema from Pydantic model."""
    return MetadataExtraction.model_json_schema()


def get_pass2_master_data_schema():
    """Get the MasterDataExtraction JSON schema from Pydantic model."""
    return MasterDataExtraction.model_json_schema()


def get_pass1_metadata_prompt():
    """Get the Pass 1 metadata extraction prompt."""
    try:
        prompt_path = (
            Path(__file__).parent.parent.parent / "prompts" / "pass1_metadata_prompt.md"
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Warning: Could not load Pass 1 prompt: {e}", file=sys.stderr)
        return "Extract document metadata, provider info, period, and global settings only."


def get_pass2_master_data_prompt():
    """Get the Pass 2 master data extraction prompt."""
    try:
        prompt_path = (
            Path(__file__).parent.parent.parent / "prompts" / "pass2_products_prompt.md"
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Warning: Could not load Pass 2 prompt: {e}", file=sys.stderr)
        return "Extract master data only. Do not extract products or commission rules."


def get_pass2_master_data_codegen_prompt():
    """Get the Pass 2 code-generation fallback prompt for master data."""
    try:
        prompt_path = (
            Path(__file__).parent.parent.parent
            / "prompts"
            / "pass2_master_data_codegen_prompt.md"
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Warning: Could not load Pass 2 codegen prompt: {e}", file=sys.stderr)
        return (
            "Generate Python code that sets result = [...] with one dict per master data entry. "
            "Each dict must have keys: entry_type, applies_to, key, values, notes, source_sheet, "
            "is_global_rule, priority."
        )


def get_pass3_rules_codegen_prompt():
    """Get the Pass 3 code-generation fallback prompt."""
    try:
        prompt_path = (
            Path(__file__).parent.parent.parent
            / "prompts"
            / "pass3_rules_codegen_prompt.md"
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Warning: Could not load Pass 3 codegen prompt: {e}", file=sys.stderr)
        return (
            "Generate Python code that sets result = [...] with one dict per commission rule. "
            "Each dict must have keys: rule_text, commission_value, product_name, product_type, "
            "customer_type_text, channel_text, coverage_type_text, state_text, conditions, "
            "other_conditions, applied_on_text, gst_applicable, source_section, quoted_text, confidence_note."
        )


def _is_truncated_json(raw_json: str, provider: str) -> bool:
    """
    Detect whether an LLM JSON output was truncated at the token limit.

    Returns True if:
    - Output is >= 90% of the estimated max character capacity for the provider, OR
    - The last 100 characters do not end with a closing brace (incomplete JSON)

    Args:
        raw_json: Raw string returned by the LLM
        provider: LLM provider name ("gemini" or "openai")
    """
    # Estimated max chars: tokens * ~3.5 chars/token
    # Gemini: 65535 tokens → ~229K chars; OpenAI: 16000 tokens → ~56K chars
    max_chars = {"gemini": 229_000, "openai": 56_000}.get(provider.lower(), 229_000)
    tail = raw_json[-100:].rstrip() if len(raw_json) >= 100 else raw_json.rstrip()
    ends_clean = tail.endswith("}")
    return len(raw_json) >= max_chars * 0.90 or not ends_clean



def _should_use_codegen_for_pass2(master_data_content: str) -> bool:
    """
    Proactive heuristic: decide whether Pass 2 should use code generation instead
    of JSON extraction before making any LLM call.

    Signal checked: input size > CODEGEN_INPUT_SIZE_THRESHOLD_BYTES_PASS2

    Args:
        master_data_content: The master data text that will be sent to Pass 2

    Returns:
        True if code-gen mode should be used proactively
    """
    content_bytes = len(master_data_content.encode("utf-8"))
    if content_bytes > CODEGEN_INPUT_SIZE_THRESHOLD_BYTES_PASS2:
        print(
            f"#   → Proactive code-gen: pass2 content size {content_bytes:,} bytes "
            f"> threshold {CODEGEN_INPUT_SIZE_THRESHOLD_BYTES_PASS2:,} bytes",
            file=sys.stderr,
        )
        return True
    return False


def _extract_pass1_metadata(
    email_and_master_data_content, service, output_dir=None, filename_prefix=""
):
    """
    Pass 1: Extract metadata, provider, period, and global settings.

    Args:
        email_and_master_data_content: Email content + master data/metadata sections only (no commission data)
        service: LLM service instance
        output_dir: Directory to save user prompt (optional)
        filename_prefix: Prefix for output filenames (e.g., sheet name)

    Returns:
        MetadataExtraction: Validated metadata extraction
    """
    print("=" * 70, file=sys.stderr)
    print("# PASS 1: Extracting Metadata", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    schema = get_pass1_metadata_schema()
    prompt = get_pass1_metadata_prompt()

    if not schema:
        raise Exception("Could not load Pass 1 schema")

    # Build config
    config = get_config_for_extraction()
    config.response_json_schema = schema
    config.system_instruction = prompt

    # Save user prompt before calling LLM
    if output_dir:
        prompt_filename = (
            f"{filename_prefix}pass1_user_prompt.txt"
            if filename_prefix
            else "pass1_user_prompt.txt"
        )
        prompt_file = output_dir / prompt_filename
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(email_and_master_data_content)
        print(f"#   → Saved user prompt to {prompt_file}", file=sys.stderr)

    # Call LLM
    result_json = service.generate_content(email_and_master_data_content, config=config)

    # Save raw LLM response before parsing
    if output_dir:
        raw_filename = (
            f"{filename_prefix}pass1_raw_response.json"
            if filename_prefix
            else "pass1_raw_response.json"
        )
        raw_file = output_dir / raw_filename
        with open(raw_file, "w", encoding="utf-8") as f:
            f.write(result_json)
        print(f"#   → Saved raw LLM response to {raw_file}", file=sys.stderr)

    # Parse and validate
    try:
        result_dict = json.loads(result_json)
        metadata = MetadataExtraction.model_validate(result_dict)
    except json.JSONDecodeError as e:
        print(f"#   ⚠ Pass 1 JSON parse failed: {e}", file=sys.stderr)

        # Show preview of problematic output
        if result_json:
            print(
                f"#   → Output preview (first 500 chars): {result_json[:500]}",
                file=sys.stderr,
            )
            print(f"#   → Output size: {len(result_json):,} bytes", file=sys.stderr)

            # Save the failed JSON output for debugging (additional copy with error marker)
            if output_dir:
                error_filename = (
                    f"{filename_prefix}pass1_PARSE_ERROR.json"
                    if filename_prefix
                    else "pass1_PARSE_ERROR.json"
                )
                error_file = output_dir / error_filename
                with open(error_file, "w") as f:
                    f.write(result_json)
                print(
                    f"#   → Saved failed JSON output to {error_file}", file=sys.stderr
                )

        print("#   Check saved output file for details.", file=sys.stderr)
        raise  # Re-raise to propagate error

    print(
        f"# Pass 1 complete: Provider={metadata.provider_name}, Period={metadata.period_label}",
        file=sys.stderr,
    )
    return metadata


def _extract_pass2_via_codegen(
    master_data_content, service, metadata, output_dir=None, filename_prefix=""
):
    """
    Code-generation fallback for Pass 2.

    Instead of asking the LLM to output structured JSON (which can hit output token limits
    for large master data sections), this asks the LLM to generate a Python *parser program*
    that reads the master data content from a pre-injected variable (`master_data_markdown`)
    and extracts entries at runtime.

    The generated code is:
    1. Validated with AST analysis (blocks imports, exec, dunder access, etc.)
    2. Executed in a restricted namespace with `master_data_markdown` pre-injected
    3. Validated entry-by-entry against the MasterDataEntry Pydantic model
    4. Assembled into a MasterDataExtraction (same return type as the JSON path)

    Retries up to MAX_CODEGEN_ATTEMPTS times on failure, appending a bug trace to the
    user prompt each time so the LLM can self-correct.

    Args:
        master_data_content: Master data text for Pass 2
        service: LLM service instance
        metadata: MetadataExtraction from Pass 1
        output_dir: Directory to save intermediate files (optional)
        filename_prefix: Prefix for output filenames

    Returns:
        MasterDataExtraction: Master data entries (may be empty if all attempts fail)
    """
    from cins.processors.core.code_gen_executor import (
        CodeGenExecutionError,
        CodeGenValidationError,
        execute_entry_code,
        validate_code_ast,
    )

    MAX_CODEGEN_ATTEMPTS = 3

    print("=" * 70, file=sys.stderr)
    print(
        "# PASS 2 (CODE-GEN): Extracting Master Data via Python code", file=sys.stderr
    )
    print("=" * 70, file=sys.stderr)

    prompt = get_pass2_master_data_codegen_prompt()

    # Build context header
    context_parts = [
        f"**Provider**: {metadata.provider_name}",
        f"**Period**: {metadata.period_label} ({metadata.start_date} to {metadata.end_date or 'ongoing'})",
    ]
    global_settings = []
    if metadata.global_vehicle_type_text:
        global_settings.append(f"- Vehicle Type: {metadata.global_vehicle_type_text}")
    if metadata.global_state_text:
        global_settings.append(f"- States: {metadata.global_state_text}")
    if metadata.global_applied_on:
        global_settings.append(f"- Applied On: {metadata.global_applied_on}")
    if global_settings:
        context_parts.append("**Global Settings**:")
        context_parts.extend(global_settings)

    context_parts.append("")
    context_parts.append("---")
    context_parts.append("Extract master data from this document:")
    context_parts.append("")

    context_header = "\n".join(context_parts)
    # Full content the LLM sees AND what master_data_markdown will be at runtime
    user_prompt = context_header + master_data_content

    # Save base prompt (without any retry error context)
    if output_dir:
        prompt_file = output_dir / (
            f"{filename_prefix}pass2_codegen_user_prompt.txt"
            if filename_prefix
            else "pass2_codegen_user_prompt.txt"
        )
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(user_prompt)
        print(f"#   → Saved code-gen user prompt to {prompt_file}", file=sys.stderr)

    config = get_config_for_code_generation()
    config.system_instruction = prompt

    def _strip_fences(raw: str) -> str:
        s = raw.strip()
        if s.startswith("```"):
            lines = s.splitlines()
            # Remove first and last fence lines
            start = 1
            end = len(lines)
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip().startswith("```"):
                    end = i
                    break
            return "\n".join(lines[start:end])
        return s

    last_error = None
    last_generated_code = ""
    current_prompt = user_prompt

    for attempt in range(1, MAX_CODEGEN_ATTEMPTS + 1):
        print(
            f"# Pass 2 code-gen attempt {attempt}/{MAX_CODEGEN_ATTEMPTS}",
            file=sys.stderr,
        )

        try:
            raw_code = service.generate_content(current_prompt, config=config)
        except Exception as e:
            print(f"#   ⚠ LLM call failed: {e}", file=sys.stderr)
            last_error = str(e)
            current_prompt = (
                user_prompt
                + f"\n\n# Previous attempt error:\n# {last_error}\n# Please fix and regenerate."
            )
            continue

        generated_code = _strip_fences(raw_code)
        last_generated_code = generated_code

        # Save generated code
        if output_dir:
            code_filename = (
                f"{filename_prefix}pass2_codegen_attempt{attempt}.py"
                if filename_prefix
                else f"pass2_codegen_attempt{attempt}.py"
            )
            code_file = output_dir / code_filename
            with open(code_file, "w", encoding="utf-8") as f:
                f.write(generated_code)
            print(f"#   → Saved generated code to {code_file}", file=sys.stderr)

        # AST validation
        try:
            validate_code_ast(generated_code)
        except CodeGenValidationError as e:
            print(f"#   ⚠ AST validation failed: {e}", file=sys.stderr)
            last_error = str(e)
            current_prompt = (
                user_prompt
                + f"\n\n# Previous attempt AST validation error:\n# {last_error}\n# Please fix and regenerate."
            )
            continue

        # Execute generated code with commission_markdown injected (executor always uses this name)
        try:
            raw_dicts = execute_entry_code(
                generated_code, commission_markdown=user_prompt
            )
        except CodeGenExecutionError as e:
            print(f"#   ⚠ Code execution failed: {e}", file=sys.stderr)
            last_error = str(e)
            current_prompt = (
                user_prompt
                + f"\n\n# Previous attempt execution error:\n# {last_error}\n# Please fix and regenerate."
            )
            continue
        if not isinstance(raw_dicts, list):
            last_error = f"result is {type(raw_dicts).__name__}, expected list"
            print(f"#   ⚠ {last_error}", file=sys.stderr)
            current_prompt = (
                user_prompt
                + f"\n\n# Previous attempt error:\n# {last_error}\n# Please fix and regenerate."
            )
            continue

        # Validate each dict against MasterDataEntry
        valid_entries = []
        error_notes = []
        for i, d in enumerate(raw_dicts):
            if not isinstance(d, dict):
                error_notes.append(
                    f"Entry {i}: expected dict, got {type(d).__name__} — skipped"
                )
                continue
            try:
                entry = MasterDataEntry.model_validate(d)
                valid_entries.append(entry)
            except Exception as e:
                key = d.get("key", "unknown")
                error_notes.append(f"Entry {i} (key={key!r}): {e}")

        print(
            f"#   → {len(valid_entries)} valid entries, {len(error_notes)} validation errors",
            file=sys.stderr,
        )

        if valid_entries or not raw_dicts:
            # Success (or legitimately empty document)
            if output_dir:
                result_filename = (
                    f"{filename_prefix}pass2_codegen_result.json"
                    if filename_prefix
                    else "pass2_codegen_result.json"
                )
                result_file = output_dir / result_filename
                with open(result_file, "w", encoding="utf-8") as f:
                    json.dump([e.model_dump() for e in valid_entries], f, indent=2)
                print(f"#   → Saved codegen result to {result_file}", file=sys.stderr)

            print(
                f"# Pass 2 code-gen complete: {len(valid_entries)} master data entries",
                file=sys.stderr,
            )
            return MasterDataExtraction(
                master_data=valid_entries,
                document_structure_notes="; ".join(error_notes) if error_notes else "",
            ), generated_code, user_prompt

        # All entries failed validation — retry
        last_error = (
            f"All {len(raw_dicts)} entries failed Pydantic validation: "
            + "; ".join(error_notes[:3])
        )
        current_prompt = (
            user_prompt
            + f"\n\n# Previous attempt validation errors:\n# {last_error}\n# Please fix and regenerate."
        )

    # All attempts exhausted
    print(
        f"# Pass 2 code-gen: all {MAX_CODEGEN_ATTEMPTS} attempts failed, returning empty extraction",
        file=sys.stderr,
    )
    return MasterDataExtraction(
        master_data=[],
        document_structure_notes=f"Code-gen failed after {MAX_CODEGEN_ATTEMPTS} attempts: {last_error}",
    ), last_generated_code, user_prompt


def _extract_pass2_master_data(
    master_data_content, service, metadata, output_dir=None, filename_prefix=""
):
    """
    Pass 2: Extract master data only.

    Args:
        master_data_content: Master data/metadata sections only (from Pass 1 or Pass 0B)
        service: LLM service instance
        metadata: MetadataExtraction from Pass 1
        output_dir: Directory to save user prompt (optional)
        filename_prefix: Prefix for output filenames (e.g., sheet name)

    Returns:
        MasterDataExtraction: Validated master data extraction
    """
    print("=" * 70, file=sys.stderr)
    print("# PASS 2: Extracting Master Data", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    schema = get_pass2_master_data_schema()
    prompt = get_pass2_master_data_prompt()

    if not schema:
        raise Exception("Could not load Pass 2 schema")

    # Add metadata context to user prompt - only include available fields
    context_parts = [
        f"**Provider**: {metadata.provider_name}",
        f"**Period**: {metadata.period_label} ({metadata.start_date} to {metadata.end_date or 'ongoing'})",
    ]

    # Only include global settings if any are present
    global_settings = []
    if metadata.global_vehicle_type_text:
        global_settings.append(f"- Vehicle Type: {metadata.global_vehicle_type_text}")
    if metadata.global_state_text:
        global_settings.append(f"- States: {metadata.global_state_text}")
    if metadata.global_applied_on:
        global_settings.append(f"- Applied On: {metadata.global_applied_on}")

    if global_settings:
        context_parts.append("**Global Settings**:")
        context_parts.extend(global_settings)

    context_parts.append("")  # Empty line
    context_parts.append("---")
    context_parts.append("")  # Empty line after separator

    context_header = "\n".join(context_parts)

    user_prompt = context_header + master_data_content

    # Build config
    config = get_config_for_extraction()
    config.response_json_schema = schema
    config.system_instruction = prompt

    # Save user prompt before calling LLM
    if output_dir:
        prompt_filename = (
            f"{filename_prefix}pass2_user_prompt.txt"
            if filename_prefix
            else "pass2_user_prompt.txt"
        )
        prompt_file = output_dir / prompt_filename
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(user_prompt)
        print(f"#   → Saved user prompt to {prompt_file}", file=sys.stderr)

    # Call LLM
    result_json = service.generate_content(user_prompt, config=config)

    # Save raw LLM response before parsing
    if output_dir:
        raw_filename = (
            f"{filename_prefix}pass2_raw_response.json"
            if filename_prefix
            else "pass2_raw_response.json"
        )
        raw_file = output_dir / raw_filename
        with open(raw_file, "w", encoding="utf-8") as f:
            f.write(result_json)
        print(f"#   → Saved raw LLM response to {raw_file}", file=sys.stderr)

    # Parse and validate
    try:
        result_dict = json.loads(result_json)
        master_data_extraction = MasterDataExtraction.model_validate(result_dict)
    except json.JSONDecodeError as e:
        print(f"#   ⚠ Pass 2 JSON parse failed: {e}", file=sys.stderr)

        # Show preview of problematic output
        if result_json:
            print(
                f"#   → Output preview (first 500 chars): {result_json[:500]}",
                file=sys.stderr,
            )
            print(f"#   → Output size: {len(result_json):,} bytes", file=sys.stderr)

            # Save the failed JSON output for debugging (additional copy with error marker)
            if output_dir:
                error_filename = (
                    f"{filename_prefix}pass2_PARSE_ERROR.json"
                    if filename_prefix
                    else "pass2_PARSE_ERROR.json"
                )
                error_file = output_dir / error_filename
                with open(error_file, "w") as f:
                    f.write(result_json)
                print(
                    f"#   → Saved failed JSON output to {error_file}", file=sys.stderr
                )

        print("#   Check saved output file for details.", file=sys.stderr)

        # Reactive fallback: if output looks truncated, try code-gen before giving up
        if result_json and _is_truncated_json(
            result_json, getattr(service, "provider", "gemini")
        ):
            print(
                "#   → Output appears truncated; falling back to code-gen",
                file=sys.stderr,
            )
            extraction, codegen_code, codegen_prompt = _extract_pass2_via_codegen(
                master_data_content,
                service,
                metadata,
                output_dir=output_dir,
                filename_prefix=filename_prefix,
            )
            return extraction, codegen_code, codegen_prompt

        raise  # Re-raise to propagate error

    print(
        f"# Pass 2 complete: {len(master_data_extraction.master_data)} master data entries",
        file=sys.stderr,
    )
    return master_data_extraction, "", user_prompt


def _format_master_data_as_text(master_data_extraction):
    """
    Convert Pass 2 master data JSON to concise text for Pass 3 context.

    Takes the MasterDataExtraction object and formats it as readable text
    that can be included in the LLM context for Pass 3 product/rule extraction.

    Returns:
        str: Formatted master data as text (~500-1500 chars), or empty string if no data

    Example output:
        **Document Structure**: Commission rules are organized into separate grids per product type.

        **Master Data**:

        Declined/Excluded:
          • PCV (3W/Other): restricted (0% Commission)
          • TW bikes (Comp+SATP): restricted (0% Commission)

        RTO Mappings:
          • UP-16, UP-14 → NCR_UP
          • HR-72, HR-55, HR-51, HR-38, HR-26 → NCR_HR
    """
    # Import here to avoid circular dependency
    from cins.schema.extraction_passes import MasterDataExtraction

    if (
        not master_data_extraction.master_data
        and not master_data_extraction.document_structure_notes
    ):
        return ""  # No master data to format

    lines = []

    # Document structure notes first
    if master_data_extraction.document_structure_notes:
        lines.append(
            f"**Document Structure**: {master_data_extraction.document_structure_notes}"
        )
        lines.append("")

    # Group master data by entry_type
    grouped = {}
    for entry in master_data_extraction.master_data:
        entry_type = entry.entry_type
        if entry_type not in grouped:
            grouped[entry_type] = []
        grouped[entry_type].append(entry)

    if grouped:
        lines.append("**Master Data**:")
        lines.append("")

        # Define formatting for each type
        type_labels = {
            "rto_mapping": "RTO Mappings",
            "declined": "Declined/Excluded",
            "vehicle_classification": "Vehicle Classifications",
            "state_mapping": "State Mappings",
            "general_term": "General Terms",
        }

        for entry_type, entries in grouped.items():
            label = type_labels.get(entry_type, entry_type.replace("_", " ").title())
            lines.append(f"{label}:")

            # Format based on type
            if entry_type == "rto_mapping":
                # Group RTOs by same value for brevity
                value_groups = {}
                for e in entries:
                    val = e.values
                    if val not in value_groups:
                        value_groups[val] = []
                    value_groups[val].append(e.key)

                for val, keys in value_groups.items():
                    lines.append(f"  • {', '.join(keys)} → {val}")

            else:
                # Standard formatting for other types
                for e in entries:
                    if e.notes:
                        lines.append(f"  • {e.key}: {e.values} ({e.notes})")
                    else:
                        lines.append(f"  • {e.key}: {e.values}")

            lines.append("")  # Blank line between types

    return "\n".join(lines)


def _extract_pass3_via_codegen(
    commission_data_content,
    service,
    metadata,
    master_data_extraction,
    output_dir=None,
    filename_prefix="",
):
    """
    Code-generation fallback for Pass 3.

    Instead of asking the LLM to output structured JSON (which can hit output token limits
    for large documents), this asks the LLM to generate a Python *parser program* that reads
    the commission content from a pre-injected variable (`commission_markdown`) and extracts
    rules at runtime. This avoids hardcoding data and is far more token-efficient.

    The generated code is:
    1. Validated with AST analysis (blocks imports, exec, dunder access, etc.)
    2. Executed in a restricted namespace with `commission_markdown` pre-injected
    3. Validated entry-by-entry against the RawCommissionEntry Pydantic model
    4. Assembled into a RulesExtraction (same return type as the JSON path)

    Retries up to MAX_CODEGEN_ATTEMPTS times on failure, appending a bug trace to the
    user prompt each time so the LLM can self-correct.

    Args:
        commission_data_content: Commission data text for Pass 3
        service: LLM service instance
        metadata: MetadataExtraction from Pass 1
        master_data_extraction: MasterDataExtraction from Pass 2
        output_dir: Directory to save intermediate files (optional)
        filename_prefix: Prefix for output filenames

    Returns:
        RulesExtraction: Products with their rules (may be empty if all attempts fail)
    """
    import re as _re

    from cins.processors.core.code_gen_executor import (
        CodeGenExecutionError,
        CodeGenValidationError,
        execute_entry_code,
        validate_code_ast,
        validate_entries,
    )

    MAX_CODEGEN_ATTEMPTS = 3

    print("=" * 70, file=sys.stderr)
    print(
        "# PASS 3 (CODE-GEN): Extracting Products and Rules via Python code",
        file=sys.stderr,
    )
    print("=" * 70, file=sys.stderr)

    prompt = get_pass3_rules_codegen_prompt()

    # Build context header (same structure as the JSON pass)
    context_parts = [
        f"**Provider**: {metadata.provider_name}",
        f"**Period**: {metadata.period_label}",
    ]
    global_settings = []
    if metadata.global_gst_percentage:
        global_settings.append(f"- GST: {metadata.global_gst_percentage}")
    if metadata.global_applied_on:
        global_settings.append(f"- Applied On: {metadata.global_applied_on}")
    if metadata.global_deductions:
        global_settings.append(f"- Deductions: {metadata.global_deductions}")
    if metadata.global_vehicle_type_text:
        global_settings.append(f"- Vehicle Type: {metadata.global_vehicle_type_text}")
    if metadata.global_state_text:
        global_settings.append(f"- States: {metadata.global_state_text}")
    if global_settings:
        context_parts.append("**Global Settings**:")
        context_parts.extend(global_settings)

    master_data_text = _format_master_data_as_text(master_data_extraction)
    if master_data_text:
        context_parts.append("")
        context_parts.append(master_data_text)

    context_parts.append("")
    context_parts.append("---")
    context_parts.append("Extract commission data from this document:")
    context_parts.append("")

    context_header = "\n".join(context_parts)
    # This is the full content the LLM sees AND what commission_markdown will be at runtime
    user_prompt = context_header + commission_data_content

    # Save base prompt (without any retry error context)
    if output_dir:
        prompt_file = output_dir / (
            f"{filename_prefix}pass3_codegen_user_prompt.txt"
            if filename_prefix
            else "pass3_codegen_user_prompt.txt"
        )
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(user_prompt)
        print(f"#   → Saved code-gen user prompt to {prompt_file}", file=sys.stderr)

    config = get_config_for_code_generation()
    config.system_instruction = prompt

    def _strip_fences(raw: str) -> str:
        """Remove markdown code fences if the LLM included them."""
        s = raw.strip()
        if s.startswith("```"):
            lines = s.splitlines()
            inner = lines[1:] if lines[0].startswith("```") else lines
            if inner and inner[-1].strip() == "```":
                inner = inner[:-1]
            return "\n".join(inner)
        return s

    def _build_error_context(error, code: str, attempt: int) -> str:
        """Build an error message with a code snippet for the retry prompt."""
        code_lines = code.splitlines()
        snippet = ""
        m = _re.search(r"line (\d+)", str(error))
        if m:
            ln = int(m.group(1)) - 1
            start = max(0, ln - 2)
            end = min(len(code_lines), ln + 3)
            snippet = "\n".join(
                f"  {i + 1}: {line}"
                for i, line in enumerate(code_lines[start:end], start=start)
            )
        ctx = f"[Attempt {attempt} failed]\nError: {error}"
        if snippet:
            ctx += f"\n\nRelevant code:\n{snippet}"
        ctx += "\n\nFix the above error and regenerate the complete corrected code."
        return ctx

    # ── Retry loop ──────────────────────────────────────────────────────────
    error_context = ""
    valid_entries = []
    error_notes = []
    code = ""

    for attempt in range(1, MAX_CODEGEN_ATTEMPTS + 1):
        print(
            f"#   → Code-gen attempt {attempt}/{MAX_CODEGEN_ATTEMPTS}", file=sys.stderr
        )

        prompt_for_attempt = user_prompt
        if error_context:
            prompt_for_attempt = user_prompt + "\n\n" + error_context

        # LLM call
        try:
            raw_code = service.generate_content(prompt_for_attempt, config=config)
        except Exception as e:
            print(f"#   ⚠ Code-gen LLM call failed: {e}", file=sys.stderr)
            error_context = _build_error_context(f"LLM API error: {e}", code, attempt)
            if attempt < MAX_CODEGEN_ATTEMPTS:
                continue
            return RulesExtraction(
                products=[],
                overall_ambiguities=[
                    f"Code-gen LLM call failed after {attempt} attempts: {e}"
                ],
                overall_requires_review=True,
            ), code, user_prompt

        code = _strip_fences(raw_code)
        print(f"#   → Generated code: {len(code):,} chars", file=sys.stderr)

        # Save generated code (overwrite per attempt for latest version)
        if output_dir:
            suffix = f"_attempt{attempt}" if attempt > 1 else ""
            code_file = output_dir / (
                f"{filename_prefix}pass3_codegen_raw{suffix}.py"
                if filename_prefix
                else f"pass3_codegen_raw{suffix}.py"
            )
            with open(code_file, "w", encoding="utf-8") as f:
                f.write(code)
            print(f"#   → Saved generated code to {code_file}", file=sys.stderr)

        # AST validation
        try:
            validate_code_ast(code)
        except (CodeGenValidationError, CodeGenExecutionError) as e:
            print(f"#   ⚠ AST validation failed: {e}", file=sys.stderr)
            error_context = _build_error_context(e, code, attempt)
            if attempt < MAX_CODEGEN_ATTEMPTS:
                continue
            return RulesExtraction(
                products=[],
                overall_ambiguities=[
                    f"Code-gen AST validation failed after {attempt} attempts: {e}"
                ],
                overall_requires_review=True,
            ), code, user_prompt

        # Execute — pass the full prompt as commission_markdown so code can read it
        try:
            raw_dicts = execute_entry_code(code, commission_markdown=user_prompt)
        except CodeGenExecutionError as e:
            print(f"#   ⚠ Code-gen execution failed: {e}", file=sys.stderr)
            error_context = _build_error_context(e, code, attempt)
            if attempt < MAX_CODEGEN_ATTEMPTS:
                continue
            return RulesExtraction(
                products=[],
                overall_ambiguities=[
                    f"Code-gen execution failed after {attempt} attempts: {e}"
                ],
                overall_requires_review=True,
            ), code, user_prompt

        print(f"#   → Code-gen produced {len(raw_dicts)} raw dicts", file=sys.stderr)

        # Empty result — retry
        if not raw_dicts:
            msg = "result list was empty — no rules were generated"
            print(f"#   ⚠ {msg}", file=sys.stderr)
            error_context = _build_error_context(msg, code, attempt)
            if attempt < MAX_CODEGEN_ATTEMPTS:
                continue
            return RulesExtraction(
                products=[],
                overall_ambiguities=[
                    f"Code-gen produced no rules after {attempt} attempts"
                ],
                overall_requires_review=True,
            ), code, user_prompt

        # Pydantic validation
        valid_entries, error_notes = validate_entries(raw_dicts)
        print(
            f"#   → {len(valid_entries)} valid entries, {len(error_notes)} validation errors",
            file=sys.stderr,
        )

        if error_notes:
            for note in error_notes[:5]:
                print(f"#     {note}", file=sys.stderr)
            if len(error_notes) > 5:
                print(f"#     ... and {len(error_notes) - 5} more", file=sys.stderr)

        # All entries failed validation — retry
        if not valid_entries and error_notes:
            msg = "\n".join(error_notes[:3])
            print("#   ⚠ All entries failed Pydantic validation", file=sys.stderr)
            error_context = _build_error_context(msg, code, attempt)
            if attempt < MAX_CODEGEN_ATTEMPTS:
                continue
            return RulesExtraction(
                products=[],
                overall_ambiguities=error_notes,
                overall_requires_review=True,
            ), code, user_prompt

        # Partial or full success — use what we have
        print(f"#   ✓ Code-gen succeeded on attempt {attempt}", file=sys.stderr)
        break

    # ── Assemble RulesExtraction from valid entries ──────────────────────────
    products_map = {}
    for entry in valid_entries:
        key = entry.product_name or "__unknown__"
        if key not in products_map:
            products_map[key] = {
                "product_name": entry.product_name,
                "product_type": entry.product_type,
                "rules": [],
            }
        products_map[key]["rules"].append(
            RuleEntry(
                rule_text=entry.rule_text,
                commission_value=entry.commission_value,
                customer_type_text=entry.customer_type_text,
                channel_text=entry.channel_text,
                coverage_type_text=entry.coverage_type_text,
                state_text=entry.state_text,
                conditions=entry.conditions,
                other_conditions=entry.other_conditions,
                applied_on_text=entry.applied_on_text,
                gst_applicable=entry.gst_applicable,
                source_section=entry.source_section,
                quoted_text=entry.quoted_text,
                confidence_note=entry.confidence_note,
            )
        )

    products = [
        ProductWithRules(
            product_name=p["product_name"],
            product_type=p["product_type"],
            rules=p["rules"],
        )
        for p in products_map.values()
    ]

    total_rules = sum(len(p.rules) for p in products)
    print(
        f"# Pass 3 (code-gen) complete: {len(products)} products, {total_rules} total rules",
        file=sys.stderr,
    )
    for p in products:
        print(f"#   → {p.product_name}: {len(p.rules)} rules", file=sys.stderr)

    return RulesExtraction(
        products=products,
        overall_ambiguities=error_notes if error_notes else [],
        overall_requires_review=bool(error_notes),
    ), code, user_prompt



def extract_multi_pass(
    prompt_text,
    api_key=None,
    model=None,
    return_passes=False,
    output_dir=None,
    filename_prefix="",
    source_type=None,
    email_and_master_data_content=None,
    master_data_content=None,
    commission_data_content=None,
    email_body_content=None,
    on_pass_complete=None,
    on_pass_start=None,
    provider=None,
    **kwargs,
):
    """
    Multi-pass extraction: Metadata → Master Data → Products+Rules → Merge → CICS

    This approach splits extraction into focused passes with smaller schemas,
    reducing LLM cognitive load and improving extraction accuracy.

    NOTE: Content cleaning (Pass 0A/0B) is done during the indexing stage.
    Each pass receives only the relevant content sections:
    - Pass 1: Email content + master data/metadata sections
    - Pass 2: Master data/metadata sections only
    - Pass 3: Commission data sections only (optionally prepended with email
      body if Pass 1 detects commission data in email via has_commission_data)

    Passes:
    - Pass 1: Extract metadata, provider, period, global settings
    - Pass 2: Extract master data (RTO mappings, declined lists, etc.)
    - Pass 3: Extract all products and their commission rules
    - Merge: Combine passes into RawExtraction
    - Transform: Convert to CICS schema

    Args:
        prompt_text: Full document content (fallback if specific sections not provided)
        api_key: API key (optional, uses env var)
        model: Model to use (optional, uses configured default)
        return_passes: If True, return pass outputs along with final result
        output_dir: Directory to save intermediate pass outputs (optional)
        filename_prefix: Prefix for output filenames (e.g., sheet name for multi-sheet Excel)
        source_type: Source type ('email', 'pdf', 'excel') - kept for backward compatibility
        email_and_master_data_content: Email + master data sections for Pass 1 (if None, uses prompt_text)
        master_data_content: Master data sections only for Pass 2 (if None, uses prompt_text)
        commission_data_content: Commission data sections only for Pass 3 (if None, uses prompt_text)
        email_body_content: Cleaned email body content (without master data). If provided and
            Pass 1 detects has_commission_data=True, this is prepended to Pass 3 content.
        on_pass_complete: Optional callable(pass_number, result_dict, duration_seconds) called
            immediately after each pass completes, before the next pass starts.
        **kwargs: Additional arguments

    Returns:
        str: Extracted JSON string (CICS format)
        or dict with pass outputs if return_passes=True
    """
    print("=" * 70, file=sys.stderr)
    print("# MULTI-PASS EXTRACTION", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    # Initialize LLM service
    service = get_llm_service(provider=provider)
    if api_key:
        service._api_key = api_key
    if model:
        service._model = model

    print(f"# Model: {service.model}", file=sys.stderr)
    print(f"# Approach: Multi-pass (3 passes)", file=sys.stderr)
    print(f"# Note: Each pass receives only relevant content sections", file=sys.stderr)

    # Setup output directory
    out_path = None
    if output_dir:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

    # Determine content for each pass (use specific sections if provided, else fallback to prompt_text)
    pass1_content = (
        email_and_master_data_content
        if email_and_master_data_content is not None
        else prompt_text
    )
    pass2_content = (
        master_data_content if master_data_content is not None else prompt_text
    )
    pass3_content = (
        commission_data_content if commission_data_content is not None else prompt_text
    )

    print(
        f"# Pass 1 content: {len(pass1_content)} chars (email + master data)",
        file=sys.stderr,
    )
    print(
        f"# Pass 2 content: {len(pass2_content)} chars (master data only)",
        file=sys.stderr,
    )
    print(
        f"# Pass 3 content: {len(pass3_content)} chars (commission data only)",
        file=sys.stderr,
    )

    try:
        # Pass 1: Metadata (email content + master data/metadata sections)
        if on_pass_start:
            on_pass_start(1)
        _pass1_start = time.monotonic()
        metadata = _extract_pass1_metadata(
            pass1_content, service, output_dir=out_path, filename_prefix=filename_prefix
        )
        _pass1_duration = time.monotonic() - _pass1_start

        if out_path:
            metadata_filename = (
                f"{filename_prefix}pass1_metadata.json"
                if filename_prefix
                else "pass1_metadata.json"
            )
            with open(out_path / metadata_filename, "w") as f:
                json.dump(metadata.model_dump(), f, indent=2)

        if on_pass_complete:
            on_pass_complete(1, metadata.model_dump(), _pass1_duration, {})

        # If Pass 1 detected commission data in email body, prepend it to Pass 3 content
        if email_body_content and metadata.has_commission_data:
            print(
                "# Pass 1 detected commission data in email — prepending email body to Pass 3",
                file=sys.stderr,
            )
            pass3_content = (
                f"## Email Commission Data\n\n{email_body_content}"
                f"\n\n---\n\n{pass3_content}"
            )

        # Early exit: if document states rates are unchanged, skip Pass 2 and Pass 3
        if metadata.inherits_from_previous:
            print("=" * 70, file=sys.stderr)
            print(
                "# NO CHANGE DETECTED: Skipping Pass 2 and Pass 3 (rates unchanged)",
                file=sys.stderr,
            )
            print(f"# Inheritance note: {metadata.inheritance_note}", file=sys.stderr)
            print("=" * 70, file=sys.stderr)

            empty_master_data = MasterDataExtraction(master_data=[])
            empty_rules = RulesExtraction(products=[])

            # Notify the orchestrator that pass 2 and pass 3 were intentionally
            # skipped. Without this, any pre-created PENDING ExtractionPass rows
            # for p2/p3 stay PENDING forever and the UI shows them as in-progress.
            _skipped_note = (
                f"Skipped: Pass 1 reported inherits_from_previous=True. "
                f"{metadata.inheritance_note or ''}".strip()
            )
            if on_pass_complete:
                on_pass_complete(
                    2,
                    empty_master_data.model_dump(),
                    0.0,
                    {"skipped": True, "skip_reason": _skipped_note},
                )
                on_pass_complete(
                    3,
                    empty_rules.model_dump(),
                    0.0,
                    {"skipped": True, "skip_reason": _skipped_note},
                )

            raw_extraction = RawExtraction.from_passes(
                metadata, empty_master_data, empty_rules
            )

            if out_path:
                merged_filename = (
                    f"{filename_prefix}merged_raw_extraction.json"
                    if filename_prefix
                    else "merged_raw_extraction.json"
                )
                with open(out_path / merged_filename, "w") as f:
                    json.dump(raw_extraction.model_dump(exclude_none=True), f, indent=2)

            from cins.processors.core.raw_to_cics_transformer import (
                RawToCICSTransformer,
            )

            transformer = RawToCICSTransformer()
            cics_doc, transform_warnings, transform_errors = transformer.transform(
                raw_extraction
            )
            cics_json = cics_doc.model_dump(exclude_none=False)
            pretty_cics = json.dumps(cics_json, indent=2)

            print("# No-change extraction complete.", file=sys.stderr)

            if return_passes:
                return {
                    "cics": cics_json,
                    "pass1_metadata": metadata.model_dump(),
                    "pass2_master_data": empty_master_data.model_dump(),
                    "pass3_products_and_rules": empty_rules.model_dump(),
                    "raw_extraction": raw_extraction.model_dump(exclude_none=True),
                    "transform_warnings": transform_warnings,
                    "transform_errors": transform_errors,
                    "no_change": True,
                }

            return pretty_cics

        # Pass 2: Master Data (master data/metadata sections only)
        # Proactive check: use code-gen mode if content is too large for JSON output
        if on_pass_start:
            on_pass_start(2)
        _pass2_start = time.monotonic()
        _pass2_codegen_code = ""
        _pass2_extraction_method = "llm"
        _pass2_user_prompt = pass2_content
        if _should_use_codegen_for_pass2(pass2_content):
            print(
                "# Pass 2: Using code-gen mode (proactive threshold triggered)",
                file=sys.stderr,
            )
            master_data_extraction, _pass2_codegen_code, _pass2_user_prompt = _extract_pass2_via_codegen(
                pass2_content,
                service,
                metadata,
                output_dir=out_path,
                filename_prefix=filename_prefix,
            )
            _pass2_extraction_method = "codegen"
        else:
            master_data_extraction, _pass2_codegen_code, _pass2_user_prompt = _extract_pass2_master_data(
                pass2_content,
                service,
                metadata,
                output_dir=out_path,
                filename_prefix=filename_prefix,
            )
            if _pass2_codegen_code:
                _pass2_extraction_method = "codegen"
        _pass2_duration = time.monotonic() - _pass2_start

        if out_path:
            master_data_filename = (
                f"{filename_prefix}pass2_master_data.json"
                if filename_prefix
                else "pass2_master_data.json"
            )
            with open(out_path / master_data_filename, "w") as f:
                json.dump(master_data_extraction.model_dump(), f, indent=2)

        if on_pass_complete:
            on_pass_complete(2, master_data_extraction.model_dump(), _pass2_duration, {
                "extraction_method": _pass2_extraction_method,
                "codegen_code": _pass2_codegen_code,
                "user_prompt": _pass2_user_prompt,
            })

        # Pass 3: Products and Rules (commission data sections only)
        # Always use code-gen mode for Pass 3
        if on_pass_start:
            on_pass_start(3)
        _pass3_start = time.monotonic()
        _pass3_codegen_code = ""
        _pass3_extraction_method = "codegen"
        _pass3_user_prompt = pass3_content
        print(
            "# Pass 3: Using code-gen mode (default)",
            file=sys.stderr,
        )
        rules_extraction, _pass3_codegen_code, _pass3_user_prompt = _extract_pass3_via_codegen(
            pass3_content,
            service,
            metadata,
            master_data_extraction,
            output_dir=out_path,
            filename_prefix=filename_prefix,
        )
        _pass3_duration = time.monotonic() - _pass3_start

        if out_path:
            rules_filename = (
                f"{filename_prefix}pass3_products_and_rules.json"
                if filename_prefix
                else "pass3_products_and_rules.json"
            )
            with open(out_path / rules_filename, "w") as f:
                json.dump(rules_extraction.model_dump(), f, indent=2)

        if on_pass_complete:
            on_pass_complete(3, rules_extraction.model_dump(), _pass3_duration, {
                "extraction_method": _pass3_extraction_method,
                "codegen_code": _pass3_codegen_code,
                "user_prompt": _pass3_user_prompt,
            })

        # Merge passes into RawExtraction
        print("=" * 70, file=sys.stderr)
        print("# MERGING PASSES", file=sys.stderr)
        print("=" * 70, file=sys.stderr)

        raw_extraction = RawExtraction.from_passes(
            metadata, master_data_extraction, rules_extraction
        )

        print(
            f"# Merged: {len(raw_extraction.commission_entries)} total rules",
            file=sys.stderr,
        )

        if out_path:
            merged_filename = (
                f"{filename_prefix}merged_raw_extraction.json"
                if filename_prefix
                else "merged_raw_extraction.json"
            )
            with open(out_path / merged_filename, "w") as f:
                json.dump(raw_extraction.model_dump(exclude_none=True), f, indent=2)

        # Transform to CICS (existing Stage 2)
        print("=" * 70, file=sys.stderr)
        print("# TRANSFORMING TO CICS", file=sys.stderr)
        print("=" * 70, file=sys.stderr)

        from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer

        transformer = RawToCICSTransformer()
        cics_doc, transform_warnings, transform_errors = transformer.transform(
            raw_extraction
        )

        if transform_warnings:
            print(f"# Transform warnings: {len(transform_warnings)}", file=sys.stderr)
        if transform_errors:
            print(f"# Transform errors: {len(transform_errors)}", file=sys.stderr)

        cics_json = cics_doc.model_dump(exclude_none=False)
        pretty_cics = json.dumps(cics_json, indent=2)

        print("=" * 70, file=sys.stderr)
        print("# MULTI-PASS EXTRACTION COMPLETE", file=sys.stderr)
        print("=" * 70, file=sys.stderr)

        if return_passes:
            return {
                "cics": cics_json,
                "pass1_metadata": metadata.model_dump(),
                "pass2_master_data": master_data_extraction.model_dump(),
                "pass3_products_and_rules": rules_extraction.model_dump(),
                "raw_extraction": raw_extraction.model_dump(exclude_none=True),
                "transform_warnings": transform_warnings,
                "transform_errors": transform_errors,
            }

        return pretty_cics

    except Exception as e:
        import traceback

        error_msg = str(e)
        error_type = type(e).__name__

        print(
            f"# Multi-pass extraction error ({error_type}): {error_msg}",
            file=sys.stderr,
        )

        # Provide helpful context for common errors
        if isinstance(e, json.JSONDecodeError):
            print(
                f"#   → This indicates malformed JSON output from the LLM",
                file=sys.stderr,
            )
            print(
                f"#   → Common causes: repetitive text, incomplete output, token limit exceeded",
                file=sys.stderr,
            )
            print(
                f"#   → Check the raw output files in the output directory",
                file=sys.stderr,
            )
        elif "API" in error_type or "quota" in error_msg.lower():
            print(
                f"#   → This is an API-level error (rate limit, quota, connectivity)",
                file=sys.stderr,
            )
        elif "validation" in error_msg.lower() or "pydantic" in error_type.lower():
            print(f"#   → This indicates schema validation failure", file=sys.stderr)
            print(
                f"#   → The LLM output doesn't match the expected Pydantic model",
                file=sys.stderr,
            )

        print(traceback.format_exc(), file=sys.stderr)

        return json.dumps(
            {
                "error": f"Multi-pass extraction failed: {error_type}: {error_msg}",
                "error_type": error_type,
                "status": "failed",
            },
            indent=2,
        )


def extract(
    prompt_text,
    api_key=None,
    model=None,
    return_prompts=False,
    output_dir=None,
    filename_prefix="",
    source_type=None,
    email_and_master_data_content=None,
    master_data_content=None,
    commission_data_content=None,
    **kwargs,
):
    """
    Extract commission data via multi-pass pipeline (main entry point).

    Args:
        prompt_text: Full document content (fallback if specific sections not provided)
        api_key: API key (optional, uses GEMINI_API_KEY or OPENAI_API_KEY env var)
        model: Model to use (optional, uses configured default)
        return_prompts: If True, return dict with 'result', 'system_instruction', 'user_prompt'
        output_dir: Directory to save intermediate outputs (optional)
        filename_prefix: Prefix for output filenames (e.g., sheet name for multi-sheet Excel)
        source_type: Source type ('email', 'pdf', 'excel')
        email_and_master_data_content: Email + master data sections for Pass 1
        master_data_content: Master data sections only for Pass 2
        commission_data_content: Commission data sections only for Pass 3

    Returns:
        str or dict: Extracted JSON string (CICS format), or dict if return_prompts=True
    """
    result = extract_multi_pass(
        prompt_text,
        api_key=api_key,
        model=model,
        output_dir=output_dir,
        filename_prefix=filename_prefix,
        source_type=source_type,
        email_and_master_data_content=email_and_master_data_content,
        master_data_content=master_data_content,
        commission_data_content=commission_data_content,
        **kwargs,
    )

    if return_prompts:
        return {
            "result": result,
            "system_instruction": "Multi-pass extraction (see pass-specific prompts)",
            "user_prompt": prompt_text,
        }
    return result


def main():
    """CLI entry point"""
    if len(sys.argv) < 2:
        print(
            "Usage: python llm_extract.py <prompt_file> [output_file] [model] [--output-dir DIR]"
        )
        print()
        print("  prompt_file: Path to file containing extraction prompt (required)")
        print("  output_file: Path to save extracted JSON (optional)")
        print("  model: Model to use (optional, uses configured default)")
        print("  --output-dir DIR: Directory to save intermediate outputs")
        print()
        print("Environment variables:")
        print("  GEMINI_API_KEY: Google API key for Gemini")
        print("  OPENAI_API_KEY: OpenAI API key")
        print()
        print("Examples:")
        print("  python llm_extract.py prompt.txt output.json")
        print("  python llm_extract.py prompt.txt output.json --output-dir ./outputs")
        sys.exit(1)

    # Parse arguments
    args = sys.argv[1:]

    # Parse --output-dir flag
    output_dir = None
    if "--output-dir" in args:
        idx = args.index("--output-dir")
        if idx + 1 < len(args):
            output_dir = args[idx + 1]
            args.pop(idx)  # Remove --output-dir
            args.pop(idx)  # Remove the directory path
        else:
            print("Error: --output-dir requires a directory path", file=sys.stderr)
            sys.exit(1)

    prompt_file = args[0]
    output_file = args[1] if len(args) > 1 and not args[1].startswith("--") else None
    model = None

    # Find model argument (not starting with --)
    for arg in args[1:]:
        if not arg.startswith("--") and arg != output_file:
            model = arg
            break

    # Read prompt
    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            prompt = f.read()
    except FileNotFoundError:
        print(f"Error: Prompt file not found: {prompt_file}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading prompt file: {e}", file=sys.stderr)
        sys.exit(1)

    extract_result = extract(prompt, model=model, output_dir=output_dir)
    # CLI never uses return_prompts, so result is always a string
    result = str(extract_result) if extract_result else ""

    # Save or print
    if output_file and result:
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(result)
            print("# Extraction complete!", file=sys.stderr)
            print(f"# Saved to: {output_file}", file=sys.stderr)
        except Exception as e:
            print(f"Error writing output file: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(result)


if __name__ == "__main__":
    main()
