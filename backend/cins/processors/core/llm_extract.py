#!/usr/bin/env python3
"""Commission Data Extractor — multi-pass LLM extraction pipeline for insurance commission documents."""

import functools
import json
import sys
import time
from pathlib import Path

from cins.processors.llm_services.factory import (
    get_llm_service,
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


def _render_template(text: str) -> str:
    """
    Substitute shared prompt fragments (`{codegen_sandbox_rules}`,
    `{product_taxonomy}`) into a prompt body. Plain str.replace is used
    deliberately — many prompt bodies contain `{` in code samples and
    str.format would mangle them.
    """
    from cins.prompts import _shared  # local import to avoid Django boot order

    return text.replace(
        "{codegen_sandbox_rules}", _shared.CODEGEN_SANDBOX_RULES
    ).replace("{product_taxonomy}", _shared.PRODUCT_TAXONOMY)


@functools.lru_cache(maxsize=None)
def _load_carrier_overlay_file(code, pass_id):
    """Read a carrier overlay file by canonical code. Cached on (code, pass_id)
    so name variants that normalize to the same carrier share one cached read."""
    path = (
        Path(__file__).parent.parent.parent
        / "prompts"
        / "carrier_overlays"
        / f"{code}_{pass_id}.md"
    )
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except Exception as e:
        print(
            f"Warning: Could not load carrier overlay {path.name}: {e}", file=sys.stderr
        )
        return ""


def get_carrier_overlay(provider_name, pass_id="pass3"):
    """Load the provider-specific prompt overlay for a pass, or '' if none.

    Carrier-specific extraction rules live in per-provider files
    (`prompts/carrier_overlays/<code>_<pass_id>.md`) instead of bloating the
    shared base prompt — only the matching provider's overlay is injected at
    runtime (see `_inject_carrier_overlay`), so the prompt the LLM sees stays
    small and on-topic for the document at hand. Returns '' for unknown
    providers or when no overlay file exists.
    """
    from cins.schema.carriers import normalize_carrier  # local: Django boot order

    code = normalize_carrier(provider_name or "")
    if not code:
        return ""
    return _load_carrier_overlay_file(code.lower(), pass_id)


def _inject_carrier_overlay(prompt: str, provider_name, pass_id="pass3") -> str:
    """Substitute the `{carrier_overlay}` placeholder in a base prompt with the
    resolved provider's overlay (or '' when there is none)."""
    return prompt.replace(
        "{carrier_overlay}", get_carrier_overlay(provider_name, pass_id)
    )


@functools.lru_cache(maxsize=None)
def get_pass0a_email_cleaning_schema():
    """Get the EmailCleaningExtraction JSON schema from Pydantic model."""
    return EmailCleaningExtraction.model_json_schema()


@functools.lru_cache(maxsize=None)
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


@functools.lru_cache(maxsize=None)
def get_pass0b_master_data_cleaning_schema():
    """Get the MasterDataCleaningExtraction JSON schema from Pydantic model."""
    return MasterDataCleaningExtraction.model_json_schema()


@functools.lru_cache(maxsize=None)
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


@functools.lru_cache(maxsize=None)
def get_pass0c_table_detection_schema():
    """Get the CSVTableDetectionExtraction JSON schema from Pydantic model."""
    return CSVTableDetectionExtraction.model_json_schema()


@functools.lru_cache(maxsize=None)
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


@functools.lru_cache(maxsize=None)
def get_pass1_metadata_schema():
    """Get the MetadataExtraction JSON schema from Pydantic model."""
    return MetadataExtraction.model_json_schema()


@functools.lru_cache(maxsize=None)
def get_pass2_master_data_schema():
    """Get the MasterDataExtraction JSON schema from Pydantic model."""
    return MasterDataExtraction.model_json_schema()


@functools.lru_cache(maxsize=None)
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


@functools.lru_cache(maxsize=None)
def get_pass2_master_data_codegen_prompt():
    """Get the Pass 2 code-generation prompt for master data."""
    try:
        prompt_path = (
            Path(__file__).parent.parent.parent
            / "prompts"
            / "pass2_master_data_codegen_prompt.md"
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            return _render_template(f.read())
    except Exception as e:
        print(f"Warning: Could not load Pass 2 codegen prompt: {e}", file=sys.stderr)
        return (
            "Generate Python code that sets result = [...] with one dict per master data entry. "
            "Each dict must have keys: entry_type, applies_to, key, values, notes, source_sheet, "
            "is_global_rule, priority."
        )


@functools.lru_cache(maxsize=None)
def get_pass3_rules_codegen_prompt():
    """Get the Pass 3 code-generation prompt."""
    try:
        prompt_path = (
            Path(__file__).parent.parent.parent
            / "prompts"
            / "pass3_rules_codegen_prompt.md"
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            return _render_template(f.read())
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

    # Build config — use the running service's own config so provider and
    # config types match (mixing providers here causes attribute errors).
    config = service.config_for_extraction()
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

    config = service.config_for_code_generation()
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
            return (
                MasterDataExtraction(
                    master_data=valid_entries,
                    document_structure_notes="; ".join(error_notes)
                    if error_notes
                    else "",
                ),
                generated_code,
                user_prompt,
            )

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
    return (
        MasterDataExtraction(
            master_data=[],
            document_structure_notes=f"Code-gen failed after {MAX_CODEGEN_ATTEMPTS} attempts: {last_error}",
        ),
        last_generated_code,
        user_prompt,
    )


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
    table_shape=None,
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
    from cins.processors.core.codegen_coverage import (
        count_rate_bearing_rows_markdown,
        declares_only_non_rate_tables,
        expected_rate_cells,
        table_coverage_report,
    )

    MAX_CODEGEN_ATTEMPTS = 3

    print("=" * 70, file=sys.stderr)
    print(
        "# PASS 3 (CODE-GEN): Extracting Products and Rules via Python code",
        file=sys.stderr,
    )
    print("=" * 70, file=sys.stderr)

    prompt = get_pass3_rules_codegen_prompt()
    # Inject only this document's provider overlay (carrier-specific rules) so
    # the shared base prompt stays small and provider-agnostic.
    prompt = _inject_carrier_overlay(prompt, metadata.provider_name, "pass3")

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

    config = service.config_for_code_generation()
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
    # Set when the deterministic completeness gate still fails on the final attempt:
    # we keep the partial output but flag the pass loudly instead of shipping a
    # silent-green empty (the client-visible May1/June whole-segment drop).
    gate_requires_review = False
    # Best (largest) valid set seen across attempts. The gate can force extra
    # retries on a shortfall; if a later attempt then HARD-fails (empty result /
    # all-invalid), we must fall back to this instead of shipping zero — the gate
    # exists to prevent a drop, it must never manufacture one.
    best_entries = []
    best_code = ""

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
            return (
                RulesExtraction(
                    products=[],
                    overall_ambiguities=[
                        f"Code-gen LLM call failed after {attempt} attempts: {e}"
                    ],
                    overall_requires_review=True,
                ),
                code,
                user_prompt,
            )

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
            return (
                RulesExtraction(
                    products=[],
                    overall_ambiguities=[
                        f"Code-gen AST validation failed after {attempt} attempts: {e}"
                    ],
                    overall_requires_review=True,
                ),
                code,
                user_prompt,
            )

        # Execute — pass the full prompt as commission_markdown so code can read it
        try:
            raw_dicts = execute_entry_code(code, commission_markdown=user_prompt)
        except CodeGenExecutionError as e:
            print(f"#   ⚠ Code-gen execution failed: {e}", file=sys.stderr)
            error_context = _build_error_context(e, code, attempt)
            if attempt < MAX_CODEGEN_ATTEMPTS:
                continue
            return (
                RulesExtraction(
                    products=[],
                    overall_ambiguities=[
                        f"Code-gen execution failed after {attempt} attempts: {e}"
                    ],
                    overall_requires_review=True,
                ),
                code,
                user_prompt,
            )

        print(f"#   → Code-gen produced {len(raw_dicts)} raw dicts", file=sys.stderr)

        # Empty result. Whether that is a failure depends entirely on whether the
        # sheet HAS anything to extract. A notes/conditions sheet (payout terms
        # prose, zero rate cells) correctly yields nothing, and punishing it costs
        # three LLM calls and then sets requires_review — which makes
        # cics_cache.should_cache refuse to pin the document FOREVER, so every
        # later access re-runs full codegen with no backoff. Chola's May'26
        # "conditions" sheet sat in exactly that state.
        #
        # The discriminator is the same host-side count the completeness gate uses,
        # so the two can never disagree about what "has rate data" means.
        #
        # Count against commission_data_content, NOT user_prompt. user_prompt is
        # the content plus a context header carrying Pass-1 global settings and the
        # Pass-2 master-data dump, and those are prose the counter can misread: a
        # deductions line like "- Deductions: 1.5%, 2%" splits on the comma into a
        # cell holding a bare "2%", which scores as a rate-bearing row. Counting the
        # header would make every notes sheet in such a document look rate-bearing
        # and silently revert this branch to the 3-retry + requires_review path —
        # the exact permanent-cache-block symptom it exists to remove.
        #
        # Suppressing a failure signal needs POSITIVE evidence that there was
        # nothing to extract. Three independent witnesses must all agree, because
        # each one alone has a blind spot that would let a real dropped grid
        # through as "clean" — and a clean empty gets PINNED TO CACHE, which is
        # strictly worse than the retry-and-flag behaviour this replaced.
        #
        # `declares_only_non_rate_tables` — structural, and the load-bearing one.
        # Every table in the content is tagged notes/master. Cannot be fooled by
        # cell formatting.
        #
        # `count_rate_bearing_rows_markdown` — heuristic over cell text. Catches
        # untagged rate rows the markers miss, but under-counts grids whose cells
        # read "22.5 %" or "Upto 25%", so it can never be the only witness.
        #
        # `expected_rate_cells(table_shape) == 0` — the index-time CSV count, an
        # independent view that survives a mangled markdown conversion. Returns 0
        # for an absent/legacy shape too, so it adds evidence but never proves
        # emptiness on its own.
        #
        # `not best_entries` — a gate-forced retry can land here after an earlier
        # attempt really did extract rows; returning empty would discard them, the
        # gate manufacturing the very drop it exists to prevent.
        if (
            not raw_dicts
            and not best_entries
            and declares_only_non_rate_tables(commission_data_content)
            and expected_rate_cells(table_shape) == 0
            and count_rate_bearing_rows_markdown(commission_data_content) == 0
        ):
            msg = (
                "no rate-bearing rows in this sheet's content — an empty "
                "extraction is correct, not a drop"
            )
            print(f"#   → {msg}", file=sys.stderr)
            return (
                RulesExtraction(
                    products=[],
                    overall_ambiguities=[f"Code-gen produced no rules: {msg}"],
                    overall_requires_review=False,
                ),
                code,
                user_prompt,
            )

        # Empty result on a sheet that DOES carry rate rows — a genuine drop. Retry.
        if not raw_dicts:
            msg = "result list was empty — no rules were generated"
            print(f"#   ⚠ {msg}", file=sys.stderr)
            error_context = _build_error_context(msg, code, attempt)
            if attempt < MAX_CODEGEN_ATTEMPTS:
                continue
            if best_entries:
                # A prior attempt produced real rows (gate forced this retry) —
                # keep them, flagged, rather than shipping an empty result.
                valid_entries, code = best_entries, best_code
                gate_requires_review = True
                break
            return (
                RulesExtraction(
                    products=[],
                    overall_ambiguities=[
                        f"Code-gen produced no rules after {attempt} attempts"
                    ],
                    overall_requires_review=True,
                ),
                code,
                user_prompt,
            )

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

        # Remember the largest valid set across attempts, so a gate-forced retry
        # that later hard-fails can fall back to it instead of shipping zero.
        if len(valid_entries) > len(best_entries):
            best_entries = valid_entries
            best_code = code

        # All entries failed validation — retry
        if not valid_entries and error_notes:
            msg = "\n".join(error_notes[:3])
            print("#   ⚠ All entries failed Pydantic validation", file=sys.stderr)
            error_context = _build_error_context(msg, code, attempt)
            if attempt < MAX_CODEGEN_ATTEMPTS:
                continue
            if best_entries:
                # Fall back to a prior attempt's real rows rather than shipping
                # empty (the gate must not manufacture the drop it guards against).
                valid_entries, code = best_entries, best_code
                gate_requires_review = True
                break
            return (
                RulesExtraction(
                    products=[],
                    overall_ambiguities=error_notes,
                    overall_requires_review=True,
                ),
                code,
                user_prompt,
            )

        # ── Completeness gate ────────────────────────────────────────────────
        # Reconcile emitted rules against the host-counted rate-bearing rows per
        # commission table. A dropped table / segment fails the attempt so the
        # retry channel re-prompts with a deterministic, table-specific gap message.
        # Fails open when no shape was persisted (legacy docs).
        coverage = table_coverage_report(
            table_shape, valid_entries, markdown=commission_data_content
        )
        print(
            f"#   → coverage: expected~{coverage.expected} emitted={coverage.emitted} "
            f"passed={coverage.passed} ({coverage.reason})",
            file=sys.stderr,
        )
        for note in coverage.advisory:
            print(f"#     [coverage] {note}", file=sys.stderr)
        if coverage.failed:
            print(
                f"#   ⚠ Completeness gate failed ({coverage.reason}): "
                f"expected ~{coverage.expected} rate rows, emitted {coverage.emitted} rules",
                file=sys.stderr,
            )
            if attempt < MAX_CODEGEN_ATTEMPTS:
                error_context = _build_error_context(
                    coverage.gap_message, code, attempt
                )
                continue
            # Exhausted retries but still incomplete — keep what we have and flag
            # loudly rather than accept a partial-drop silently.
            print(
                "#   ⚠ Completeness gate still failing after all attempts — "
                "flagging pass requires_review",
                file=sys.stderr,
            )
            gate_requires_review = True

        # Partial or full success — use what we have
        print(f"#   ✓ Code-gen succeeded on attempt {attempt}", file=sys.stderr)
        break

    # A gate-forced retry can end on a thinner attempt than an earlier one; ship
    # the fullest valid set we saw so a later degraded attempt never loses rows.
    if len(best_entries) > len(valid_entries):
        valid_entries, code = best_entries, best_code
        gate_requires_review = True

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
                qualifying_clauses=getattr(entry, "qualifying_clauses", []) or [],
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

    ambiguities = list(error_notes) if error_notes else []
    if gate_requires_review:
        ambiguities.append(
            "Completeness gate: emitted rules fall short of the source grid's "
            "rate-bearing rows — a table/segment may have been dropped. Manual review."
        )

    return (
        RulesExtraction(
            products=products,
            overall_ambiguities=ambiguities,
            overall_requires_review=bool(error_notes) or gate_requires_review,
        ),
        code,
        user_prompt,
    )


def _commission_grid_present(content) -> bool:
    """True when the Pass 3 content actually carries a commission rate grid.

    Guards the inherits_from_previous early-exit: a forwarded "we continue last
    month's grid" cover email may still ship the full month's sheets, and those
    must be extracted rather than skipped. The ``[type: commission]`` tag is the
    indexer's own commission-table classification marker, so this fires only on
    genuine grid content and never on a bare no-change email (which carries no
    commission table).
    """
    return bool(content) and "[type: commission]" in content


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
    table_shape=None,
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
    print("# Approach: Multi-pass (3 passes)", file=sys.stderr)
    print("# Note: Each pass receives only relevant content sections", file=sys.stderr)

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

    # Deterministic period fallback: the document's own date (email `Date:`
    # header), parsed from the same content Pass 1 reads. Used only if the
    # extraction yields no period — beats stamping today's processing date.
    from cins.processors.core.raw_to_cics_transformer import parse_document_date

    document_date = parse_document_date(pass1_content) or parse_document_date(
        email_body_content or ""
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

        # Early exit: if document states rates are unchanged, skip Pass 2 and
        # Pass 3 — BUT only when there is no commission grid actually attached
        # to extract. A forwarded "we continue last month's grid" cover email
        # commonly still ships the full month's sheets (Chola June'26: the email
        # said "same grid as May 2026" yet the workbook carried complete CV /
        # Private Car / TW grids). Skipping those on the inheritance flag alone
        # produced an empty document with no previous_document_id to copy from.
        # When a real grid is present, honour the data over the cover note and
        # extract normally. The "[type: commission]" tag is the indexer's own
        # commission-table classification marker, so this fires only on genuine
        # grid content, never on a bare no-change email.
        _grid_attached = _commission_grid_present(pass3_content)
        if metadata.inherits_from_previous and _grid_attached:
            print(
                "# INHERITANCE OVERRIDDEN: Pass 1 reported inherits_from_previous"
                "=True, but a commission grid is attached — extracting it instead"
                " of skipping.",
                file=sys.stderr,
            )
        if metadata.inherits_from_previous and not _grid_attached:
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
                raw_extraction, document_date=document_date
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
                    "transform_notices": list(transformer.notices),
                    "transform_errors": transform_errors,
                    "no_change": True,
                }

            return pretty_cics

        # Pass 2: Master Data — always via code-gen. The JSON path was the
        # main perf bottleneck (e.g. 747s emitting 349 entries one-by-one);
        # codegen produces the same output as a Python list literal in
        # seconds. See plan: lets-plan-to-enhance-sunny-hamming.md (A1).
        if on_pass_start:
            on_pass_start(2)
        _pass2_start = time.monotonic()
        _pass2_extraction_method = "codegen"
        master_data_extraction, _pass2_codegen_code, _pass2_user_prompt = (
            _extract_pass2_via_codegen(
                pass2_content,
                service,
                metadata,
                output_dir=out_path,
                filename_prefix=filename_prefix,
            )
        )
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
            on_pass_complete(
                2,
                master_data_extraction.model_dump(),
                _pass2_duration,
                {
                    "extraction_method": _pass2_extraction_method,
                    "codegen_code": _pass2_codegen_code,
                    "user_prompt": _pass2_user_prompt,
                },
            )

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
        rules_extraction, _pass3_codegen_code, _pass3_user_prompt = (
            _extract_pass3_via_codegen(
                pass3_content,
                service,
                metadata,
                master_data_extraction,
                output_dir=out_path,
                filename_prefix=filename_prefix,
                table_shape=table_shape,
            )
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
            on_pass_complete(
                3,
                rules_extraction.model_dump(),
                _pass3_duration,
                {
                    "extraction_method": _pass3_extraction_method,
                    "codegen_code": _pass3_codegen_code,
                    "user_prompt": _pass3_user_prompt,
                },
            )

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
            raw_extraction, document_date=document_date
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
                "transform_notices": list(transformer.notices),
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
                "#   → This indicates malformed JSON output from the LLM",
                file=sys.stderr,
            )
            print(
                "#   → Common causes: repetitive text, incomplete output, token limit exceeded",
                file=sys.stderr,
            )
            print(
                "#   → Check the raw output files in the output directory",
                file=sys.stderr,
            )
        elif "API" in error_type or "quota" in error_msg.lower():
            print(
                "#   → This is an API-level error (rate limit, quota, connectivity)",
                file=sys.stderr,
            )
        elif "validation" in error_msg.lower() or "pydantic" in error_type.lower():
            print("#   → This indicates schema validation failure", file=sys.stderr)
            print(
                "#   → The LLM output doesn't match the expected Pydantic model",
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
