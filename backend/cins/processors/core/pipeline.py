"""
CINS Pipeline
=============
Top-level orchestrator for the commission extraction pipeline.

Usage (Python API):
    from cins.processors.core.pipeline import Pipeline
    result = Pipeline(llm_provider="gemini").process("file.xlsx")

Usage (CLI):
    python -m processors.core.pipeline file.xlsx --provider gemini --output-dir outputs/
"""

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from cins.processors.content.indexer import ContentIndexer, IndexedDocument
from cins.processors.content.prepare_prompt import prepare_extraction_request
from cins.processors.content.csv_to_markdown import csv_to_markdown
from cins.processors.core.llm_extract import extract
from cins.processors.extractors.extract_eml import extract_eml_content
from cins.processors.extractors.extract_pdf import extract_pdf_text

logger = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    """Result returned by Pipeline processing methods."""
    cics_doc: dict                    # parsed CICS JSON
    output_path: Path                 # where the file was written
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    validation_issues: list = field(default_factory=list)   # from SchemaValidator
    overlap_issues: list = field(default_factory=list)      # from RuleOverlapChecker
    duration: float = 0.0
    # per-sheet results when processing multiple sheets
    sheet_results: List[Dict] = field(default_factory=list)
    # per-sheet pass-level results for DB persistence
    # each entry: {sheet_name, attachment_name, pass_number, result, model, duration, status, error}
    sheet_pass_results: List[Dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Module-level helpers (pure functions, no Streamlit dependency)
# ---------------------------------------------------------------------------

def get_safe_sheet_name(sheet_name: str) -> str:
    """Sanitize sheet name for use in filenames."""
    safe_name = re.sub(r"[^\w\-_]", "_", str(sheet_name))
    return safe_name[:50]


def _extract_provider_from_path(file_path: str) -> str:
    """
    Extract provider name from file path.
    Assumes structure: .../inputs/finance/July/{PROVIDER}/...
    Falls back to parent directory name.
    """
    path_parts = Path(file_path).parts
    try:
        july_idx = path_parts.index("July")
        if july_idx + 1 < len(path_parts):
            return path_parts[july_idx + 1]
    except (ValueError, IndexError):
        pass
    return Path(file_path).parent.name


def _format_eml_content(email_data: Dict) -> str:
    """Format extracted email data as a prompt-ready string."""
    parts = [
        f"Subject: {email_data.get('subject', 'N/A')}",
        f"From: {email_data.get('from', 'N/A')}",
        f"Date: {email_data.get('date', 'N/A')}",
        "=" * 70,
        email_data.get("body", ""),
    ]

    attachments = email_data.get("attachments", [])
    if attachments:
        parts.append("\n" + "=" * 70)
        parts.append(f"ATTACHMENTS ({len(attachments)} found)")
        parts.append("=" * 70)

        for i, att in enumerate(attachments, 1):
            if "temp_path" in att:
                continue
            parts.append(f"\n--- Attachment {i}: {att.get('filename', 'Unknown')} ---")
            if "error" in att:
                parts.append(f"Error: {att['error']}")
            else:
                parts.append(f"Type: {att.get('content_type', 'Unknown')}")
                parts.append(f"Size: {att.get('size', 0)} bytes")
                if "extracted_content" in att:
                    parts.append("\nExtracted Content:")
                    parts.append("-" * 70)
                    parts.append(att["extracted_content"])

    return "\n".join(parts)


def _format_email_context(email_data: Dict) -> str:
    """Format email metadata and body for prepending to sheets."""
    parts = [
        "=" * 80,
        "EMAIL CONTEXT",
        "=" * 80,
        f"Subject: {email_data.get('subject', 'N/A')}",
        f"From: {email_data.get('from', 'N/A')}",
        f"Date: {email_data.get('date', 'N/A')}",
        "=" * 80,
        "",
        "EMAIL BODY:",
        email_data.get("body", ""),
        "",
    ]
    return "\n".join(parts)


def _merge_products(existing_products: list, new_products: list) -> list:
    """
    Merge products from multiple sheet extractions, combining rules for same product_code.

    Keeps first occurrence as base; merges commission_rules deduped by rule_id.
    """
    products_by_code = {}

    for product in existing_products:
        code = product.get("product_code")
        if code not in products_by_code:
            products_by_code[code] = product.copy()
            products_by_code[code]["commission_rules"] = list(
                product.get("commission_rules", [])
            )

    for product in new_products:
        code = product.get("product_code")
        if code in products_by_code:
            existing_rules = products_by_code[code]["commission_rules"]
            existing_rule_ids = {r.get("rule_id") for r in existing_rules}
            for rule in product.get("commission_rules", []):
                if rule.get("rule_id") not in existing_rule_ids:
                    existing_rules.append(rule)
                    existing_rule_ids.add(rule.get("rule_id"))
        else:
            products_by_code[code] = product.copy()
            products_by_code[code]["commission_rules"] = list(
                product.get("commission_rules", [])
            )

    return list(products_by_code.values())


def _build_validation_status(schema_result, overlap_result) -> str:
    """Derive a simple PASSED / WARNINGS / FAILED string from validator results."""
    has_critical = schema_result.has_critical or overlap_result.has_critical
    has_high = schema_result.has_high or overlap_result.has_high
    has_medium = len(schema_result.warnings) > 0
    if has_critical or has_high:
        return "FAILED"
    if has_medium:
        return "WARNINGS"
    return "PASSED"


def _merge_sheet_jsons(
    successful_results: List[Dict],
    base_name: str,
    output_dir: Path,
    source_type: str = "excel",
) -> Dict:
    """
    Merge per-sheet CICS JSONs into one combined CICS document.

    Returns the combined dict (not yet written to disk).
    """
    combined_data = {
        "schema_version": "1.0",
        "document_metadata": None,
        "provider": None,
        "commission_period": None,
        "products": [],
        "change_reference": None,
        "source_traces": [],
        "validation": None,
    }

    all_confidence_scores = []
    all_validation_notes = []
    requires_review_flags = []
    sheet_names = []
    first_doc_metadata = {}

    for idx, result in enumerate(successful_results):
        json_data = result.get("json_data") or {}
        sheet_name = result.get("sheet", f"Sheet_{idx}")
        sheet_names.append(sheet_name)

        if combined_data["provider"] is None:
            combined_data["provider"] = json_data.get("provider")
            combined_data["commission_period"] = json_data.get("commission_period")
            combined_data["change_reference"] = json_data.get("change_reference")
            first_doc_metadata = json_data.get("document_metadata", {})
        else:
            current_provider = json_data.get("provider", {})
            if current_provider and current_provider.get(
                "provider_id"
            ) != combined_data["provider"].get("provider_id"):
                logger.warning(
                    f"Sheet '{sheet_name}' has different provider: {current_provider.get('provider_id')}"
                )

        products = json_data.get("products", [])
        combined_data["products"] = _merge_products(combined_data["products"], products)

        for trace in json_data.get("source_traces", []):
            trace_copy = trace.copy()
            if trace_copy.get("extraction_notes"):
                trace_copy["extraction_notes"] = (
                    f"[{sheet_name}] {trace_copy['extraction_notes']}"
                )
            else:
                trace_copy["extraction_notes"] = f"[{sheet_name}]"
            combined_data["source_traces"].append(trace_copy)

        validation = json_data.get("validation", {})
        if validation:
            confidence = validation.get("overall_confidence")
            if confidence is not None:
                all_confidence_scores.append(confidence)
            requires_review_flags.append(validation.get("requires_manual_review", False))
            notes = validation.get("validation_notes", [])
            if notes:
                all_validation_notes.extend([f"[{sheet_name}] {n}" for n in notes])

    combined_data["document_metadata"] = {
        "document_id": base_name.replace(" ", "_"),
        "source_type": first_doc_metadata.get("source_type", source_type)
        if first_doc_metadata
        else source_type,
        "source_file": first_doc_metadata.get("source_file", f"unknown.{source_type}")
        if first_doc_metadata
        else f"unknown.{source_type}",
        "extracted_at": datetime.now().isoformat(),
        "extractor": first_doc_metadata.get("extractor", "gemini-2.0")
        if first_doc_metadata
        else "gemini-2.0",
        "notes": f"Combined output from {len(successful_results)} sheets: {', '.join(sheet_names)}",
    }

    if all_confidence_scores or all_validation_notes or requires_review_flags:
        combined_data["validation"] = {
            "overall_confidence": min(all_confidence_scores) if all_confidence_scores else None,
            "requires_manual_review": any(requires_review_flags) if requires_review_flags else False,
            "validation_notes": all_validation_notes if all_validation_notes else None,
        }

    # Validate against CICS Pydantic model
    try:
        from cins.schema.cics_model import CICSDocument
        validated = CICSDocument(**combined_data)
        combined_data = validated.model_dump(exclude_none=False)
        logger.info("Combined JSON validated against CICS schema")
    except Exception as e:
        logger.error(f"Combined JSON failed Pydantic validation: {e}")
        logger.warning("Saving combined JSON despite validation error")

    return combined_data


def _run_validators(output_path: Path):
    """Run SchemaValidator + RuleOverlapChecker. Returns (schema_result, overlap_result)."""
    try:
        from cins.validators.schema_validator import SchemaValidator
        from cins.validators.rule_overlap_checker import RuleOverlapChecker
        schema_result = SchemaValidator().validate(str(output_path))
        overlap_result = RuleOverlapChecker().check(str(output_path))
        return schema_result, overlap_result
    except ImportError:
        logger.warning("Validators not available; skipping validation")
        return None, None


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------

class Pipeline:
    """
    Top-level orchestrator for the CINS extraction pipeline.

    Separates indexing (Stage 1: parse + cache) from extraction (Stage 2: LLM).
    """

    def __init__(
        self,
        llm_provider: str = "gemini",
        model: Optional[str] = None,
        indexed_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        excel_mode: str = "sheet_by_sheet",
    ):
        self.llm_provider = llm_provider
        self.model = model
        self.excel_mode = excel_mode
        # Optional callback: called with a list of pass-result dicts after each sheet completes.
        # Set by the caller (e.g. Django task) to persist results incrementally.
        self._on_sheet_complete_callback = None

        # Resolve directories relative to project root if not given
        cins_root = Path(__file__).parent.parent.parent
        self.indexed_dir = Path(indexed_dir) if indexed_dir else cins_root / "input-indexed"
        self.output_dir = Path(output_dir) if output_dir else cins_root / "outputs"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(
        self,
        file_path,
        force_reindex: bool = False,
        output_path: Optional[Path] = None,
    ) -> PipelineResult:
        """
        Index (if needed) + extract. One-call end-to-end.

        Args:
            file_path: Path to input file (.eml, .pdf, .xlsx)
            force_reindex: Re-extract even if cached index exists
            output_path: Where to write the CICS JSON (auto-derived if None)

        Returns:
            PipelineResult
        """
        file_path = Path(file_path)
        provider = _extract_provider_from_path(str(file_path))

        indexer = ContentIndexer(self.indexed_dir)
        document_id = indexer._sanitize_filename(file_path.stem)

        indexed_doc = indexer.get_indexed_document(document_id, provider)

        if indexed_doc and not force_reindex and indexer.is_cache_valid(indexed_doc, file_path):
            logger.info(f"Cache hit: {document_id}")
        else:
            logger.info(f"Indexing: {document_id}")
            indexed_doc = indexer.index_document(
                str(file_path),
                provider,
                excel_mode=self.excel_mode,
                force_reindex=force_reindex,
            )

        resolved_output_path = output_path or self._default_output_path(file_path, provider)
        return self.process_from_indexed(indexed_doc, resolved_output_path)

    def index_only(
        self,
        file_path,
        force_reindex: bool = False,
    ) -> IndexedDocument:
        """
        Stage 1 only: parse and cache document content.

        Returns:
            IndexedDocument
        """
        file_path = Path(file_path)
        provider = _extract_provider_from_path(str(file_path))
        indexer = ContentIndexer(self.indexed_dir)
        return indexer.index_document(
            str(file_path),
            provider,
            excel_mode=self.excel_mode,
            force_reindex=force_reindex,
        )

    def process_from_indexed(
        self,
        indexed_doc: IndexedDocument,
        output_path: Path,
    ) -> PipelineResult:
        """
        Stage 2 only: LLM extraction from an already-indexed document.

        Routes to sheet-by-sheet or single-document processing based on content.
        """
        start_time = datetime.now()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if indexed_doc.has_sheets:
            # For email sources with a single Excel attachment, pass the attachment name
            # so ExtractionPass rows are keyed correctly.
            attachment_name = ""
            if indexed_doc.is_email:
                source_meta = indexed_doc.metadata.get("source_metadata", {})
                excel_attachments = source_meta.get("excel_attachments", [])
                if len(excel_attachments) == 1:
                    attachment_name = excel_attachments[0]
            return self._process_sheets(
                indexed_doc,
                output_path,
                attachment_name=attachment_name,
                on_sheet_complete=self._on_sheet_complete_callback,
            )
        else:
            return self._process_single(indexed_doc, output_path, start_time)

    def rerun_pass3(self, output_folder: Path) -> PipelineResult:
        """
        Re-run Pass 3 (products+rules) using existing Pass 1 & 2 output files.

        Expects these files in output_folder:
            pass3_user_prompt.txt
            pass1_metadata.json
            pass2_master_data.json

        Overwrites the existing CICS JSON in output_folder.

        Returns:
            PipelineResult
        """
        start_time = datetime.now()
        output_folder = Path(output_folder)

        from cins.schema.extraction_passes import MetadataExtraction, MasterDataExtraction
        from cins.schema.raw_extraction import RawExtraction
        from cins.processors.core.llm_extract import _extract_pass3_products_and_rules
        from cins.processors.llm_services.factory import get_llm_service
        from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer

        pass3_prompt_file = output_folder / "pass3_user_prompt.txt"
        pass1_metadata_file = output_folder / "pass1_metadata.json"
        pass2_master_data_file = output_folder / "pass2_master_data.json"

        for f in [pass3_prompt_file, pass1_metadata_file, pass2_master_data_file]:
            if not f.exists():
                raise FileNotFoundError(f"Required file not found: {f}")

        logger.info("Loading Pass 1 & Pass 2 outputs")
        metadata = MetadataExtraction.model_validate(
            json.loads(pass1_metadata_file.read_text(encoding="utf-8"))
        )
        master_data_extraction = MasterDataExtraction.model_validate(
            json.loads(pass2_master_data_file.read_text(encoding="utf-8"))
        )

        # Clear stale outputs
        existing_cics = list(output_folder.glob("*.json"))
        existing_cics = [
            f for f in existing_cics
            if not f.name.startswith("pass") and f.name != "merged_raw_extraction.json"
        ]
        for f in existing_cics:
            f.unlink()
            logger.info(f"Removed stale output: {f.name}")

        for old in output_folder.glob("pass3_*.json"):
            old.unlink()
        merged_raw = output_folder / "merged_raw_extraction.json"
        if merged_raw.exists():
            merged_raw.unlink()

        logger.info("Running Pass 3 (products & rules extraction)")
        commission_data_content = pass3_prompt_file.read_text(encoding="utf-8")
        service = get_llm_service()
        rules_extraction = _extract_pass3_products_and_rules(
            commission_data_content,
            service,
            metadata,
            master_data_extraction,
            output_dir=output_folder,
        )

        pass3_out = output_folder / "pass3_products_and_rules.json"
        pass3_out.write_text(
            json.dumps(rules_extraction.model_dump(), indent=2), encoding="utf-8"
        )

        logger.info("Merging passes into RawExtraction")
        raw_extraction = RawExtraction.from_passes(metadata, master_data_extraction, rules_extraction)
        merged_out = output_folder / "merged_raw_extraction.json"
        merged_out.write_text(
            json.dumps(raw_extraction.model_dump(exclude_none=True), indent=2), encoding="utf-8"
        )

        logger.info("Transforming to CICS")
        transformer = RawToCICSTransformer()
        cics_doc, transform_warnings, transform_errors = transformer.transform(raw_extraction)
        cics_json = cics_doc.model_dump(exclude_none=True)

        # Determine output file name from the folder (use folder name + .json)
        output_path = output_folder / f"{output_folder.name}.json"
        output_path.write_text(json.dumps(cics_json, indent=2), encoding="utf-8")
        logger.info(f"Saved CICS output: {output_path}")

        schema_result, overlap_result = _run_validators(output_path)

        duration = (datetime.now() - start_time).total_seconds()
        return PipelineResult(
            cics_doc=cics_json,
            output_path=output_path,
            warnings=transform_warnings or [],
            errors=transform_errors or [],
            validation_issues=schema_result.errors if schema_result else [],
            overlap_issues=overlap_result.overlaps if overlap_result else [],
            duration=duration,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _default_output_path(self, file_path: Path, provider: str) -> Path:
        provider_dir = self.output_dir / provider
        provider_dir.mkdir(parents=True, exist_ok=True)
        return provider_dir / f"{file_path.stem}.json"

    def _process_single(
        self,
        indexed_doc: IndexedDocument,
        output_path: Path,
        start_time: datetime,
    ) -> PipelineResult:
        """Process a single-unit document (PDF, email without Excel attachments)."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_dir = output_path.parent

        content_str = indexed_doc.get_primary_content()

        temp_content = output_dir / "extracted_content.txt"
        temp_content.write_text(content_str, encoding="utf-8")

        _, user_prompt = prepare_extraction_request(str(temp_content))

        source_type = indexed_doc.metadata.get("source_type")
        extract_response = extract(
            user_prompt,
            return_prompts=True,
            output_dir=str(output_dir),
            source_type=source_type,
        )

        json_result = extract_response["result"]
        actual_system_instruction = extract_response["system_instruction"]
        actual_user_prompt = extract_response["user_prompt"]

        prompt_path = output_path.parent / (output_path.stem + "_prompt.txt")
        prompt_path.write_text(
            f"=== SYSTEM INSTRUCTION ===\n{actual_system_instruction}\n\n=== USER PROMPT ===\n{actual_user_prompt}",
            encoding="utf-8",
        )

        try:
            parsed_result = json.loads(json_result)
            if parsed_result.get("status") == "failed":
                raise RuntimeError(parsed_result.get("error", "Unknown LLM error"))
        except json.JSONDecodeError:
            if "error" in json_result.lower():
                raise RuntimeError(f"LLM extraction failed: {json_result[:500]}")
            parsed_result = {}

        output_path.write_text(json_result, encoding="utf-8")

        schema_result, overlap_result = _run_validators(output_path)

        duration = (datetime.now() - start_time).total_seconds()
        return PipelineResult(
            cics_doc=parsed_result,
            output_path=output_path,
            validation_issues=schema_result.errors if schema_result else [],
            overlap_issues=overlap_result.overlaps if overlap_result else [],
            duration=duration,
        )

    def _process_sheets(
        self,
        indexed_doc: IndexedDocument,
        base_output_path: Path,
        attachment_name: str = "",
        on_sheet_complete=None,
    ) -> PipelineResult:
        """Process indexed sheets using cached content (multi-pass extraction)."""
        from cins.processors.core.llm_extract import is_multi_pass_enabled, extract_multi_pass

        start_time = datetime.now()
        output_dir = base_output_path.parent
        base_name = base_output_path.stem
        output_dir.mkdir(parents=True, exist_ok=True)

        sheet_contents = indexed_doc.get_all_sheet_contents()
        commission_sheets = indexed_doc.get_commission_sheets()
        master_sheets = indexed_doc.get_master_sheets()

        if commission_sheets:
            logger.info(f"{len(commission_sheets)} commission, {len(master_sheets)} master sheets")
        else:
            logger.warning("No commission sheets classified; processing all sheets")

        # Build master data context
        master_context = ""
        for sheet_name in master_sheets:
            content = sheet_contents.get(sheet_name, "")
            if content:
                master_context += f"\n\n## Master Data Reference: {sheet_name}\n\n{content}"

        sheets_to_process = commission_sheets if commission_sheets else list(sheet_contents.keys())
        total_sheets = len(sheets_to_process)

        results = []
        errors = []
        sheet_pass_results = []
        use_multi_pass = is_multi_pass_enabled()

        for sheet_idx, sheet_name in enumerate(sheets_to_process, 1):
            logger.info(f"Processing sheet {sheet_idx}/{total_sheets}: {sheet_name}")
            try:
                content = sheet_contents.get(sheet_name, "")
                if not content:
                    logger.warning(f"Skipping '{sheet_name}' — no content")
                    continue

                safe_name = get_safe_sheet_name(sheet_name)
                sheet_output_path = output_dir / f"{base_name}_{safe_name}.json"

                # Build per-pass content
                pass1_content = ""
                if indexed_doc.is_email:
                    email_content = indexed_doc.get_primary_content()
                    if email_content:
                        pass1_content = f"{email_content}\n\n"
                if master_context:
                    pass1_content += master_context

                pass2_content = master_context
                pass3_content = f"## Commission Data Sheet: {sheet_name}\n\n{content}"

                for pass_name, pass_content in [
                    (f"pass1_content_{safe_name}.txt", pass1_content),
                    (f"pass2_content_{safe_name}.txt", pass2_content),
                    (f"pass3_content_{safe_name}.txt", pass3_content),
                ]:
                    (output_dir / pass_name).write_text(pass_content, encoding="utf-8")

                _, pass1_prompt = prepare_extraction_request(str(output_dir / f"pass1_content_{safe_name}.txt"))
                _, pass2_prompt = prepare_extraction_request(str(output_dir / f"pass2_content_{safe_name}.txt"))
                _, pass3_prompt = prepare_extraction_request(str(output_dir / f"pass3_content_{safe_name}.txt"))

                if use_multi_pass:
                    # Call multi-pass directly with return_passes=True to capture per-pass outputs
                    sheet_start = datetime.now()
                    pass_result = extract_multi_pass(
                        pass1_prompt,
                        return_passes=True,
                        output_dir=str(output_dir),
                        filename_prefix=f"{safe_name}_",
                        source_type="excel",
                        email_and_master_data_content=pass1_prompt,
                        master_data_content=pass2_prompt,
                        commission_data_content=pass3_prompt,
                    )
                    sheet_duration = (datetime.now() - sheet_start).total_seconds()

                    # pass_result is a dict when return_passes=True
                    if isinstance(pass_result, dict) and "cics" in pass_result:
                        parsed_result = pass_result["cics"]
                        json_result = json.dumps(parsed_result, indent=2)

                        # Capture per-pass outputs for DB storage
                        from cins.processors.llm_services.factory import get_llm_service
                        try:
                            model_used = get_llm_service().model
                        except Exception:
                            model_used = ""

                        pass_prompts = {
                            1: pass1_prompt,
                            2: pass2_prompt,
                            3: pass3_prompt,
                        }
                        new_entries = []
                        for pass_num, pass_key in [
                            (1, "pass1_metadata"),
                            (2, "pass2_master_data"),
                            (3, "pass3_products_and_rules"),
                        ]:
                            entry = {
                                "sheet_name": sheet_name,
                                "attachment_name": attachment_name,
                                "pass_number": pass_num,
                                "result": pass_result.get(pass_key),
                                "prompt_text": pass_prompts[pass_num],
                                "model": model_used,
                                "duration": sheet_duration / 3,
                                "status": "completed",
                                "error": "",
                            }
                            new_entries.append(entry)
                            sheet_pass_results.append(entry)

                        if on_sheet_complete:
                            on_sheet_complete(new_entries)
                    else:
                        # Unexpected shape — treat as failed passes
                        json_result = json.dumps(pass_result) if not isinstance(pass_result, str) else pass_result
                        try:
                            parsed_result = json.loads(json_result)
                        except Exception:
                            parsed_result = {}
                        failed_entries = []
                        for pass_num in (1, 2, 3):
                            entry = {
                                "sheet_name": sheet_name,
                                "attachment_name": attachment_name,
                                "pass_number": pass_num,
                                "result": None,
                                "prompt_text": "",
                                "model": "",
                                "duration": 0.0,
                                "status": "failed",
                                "error": "Unexpected multi-pass result shape",
                            }
                            failed_entries.append(entry)
                            sheet_pass_results.append(entry)
                        if on_sheet_complete:
                            on_sheet_complete(failed_entries)
                else:
                    extract_response = extract(
                        pass1_prompt,
                        return_prompts=True,
                        output_dir=str(output_dir),
                        raw_filename=f"raw_extraction_{safe_name}.json",
                        filename_prefix=f"{safe_name}_",
                        source_type="excel",
                        email_and_master_data_content=pass1_prompt,
                        master_data_content=pass2_prompt,
                        commission_data_content=pass3_prompt,
                    )
                    json_result = extract_response["result"]

                    try:
                        parsed_result = json.loads(json_result)
                        if parsed_result.get("status") == "failed":
                            raise ValueError(parsed_result.get("error", "Unknown LLM error"))
                    except json.JSONDecodeError:
                        if "error" in json_result.lower():
                            raise ValueError(f"LLM extraction failed: {json_result[:500]}")
                        parsed_result = {}

                prompt_path = output_dir / f"{base_name}_{safe_name}_prompt.txt"
                prompt_path.write_text(
                    f"=== PASS PROMPTS ===\n=== Pass 1 ===\n{pass1_prompt}\n\n=== Pass 2 ===\n{pass2_prompt}\n\n=== Pass 3 ===\n{pass3_prompt}",
                    encoding="utf-8",
                )

                sheet_output_path.write_text(json.dumps(parsed_result, indent=2) if isinstance(parsed_result, dict) else json.dumps({}), encoding="utf-8")

                schema_result, overlap_result = _run_validators(sheet_output_path)
                validation_status = (
                    _build_validation_status(schema_result, overlap_result)
                    if schema_result
                    else "UNKNOWN"
                )

                results.append({
                    "sheet": sheet_name,
                    "status": "success",
                    "output_path": str(sheet_output_path),
                    "output_filename": sheet_output_path.name,
                    "validation_status": validation_status,
                    "json_data": parsed_result,
                })
                logger.info(f"  ✓ '{sheet_name}' processed")

            except Exception as e:
                error_msg = str(e)
                logger.error(f"  ✗ '{sheet_name}' failed: {error_msg}")
                errors.append({"sheet": sheet_name, "error": error_msg})
                results.append({"sheet": sheet_name, "status": "failed", "error": error_msg})
                if use_multi_pass:
                    exc_entries = []
                    for pass_num in (1, 2, 3):
                        entry = {
                            "sheet_name": sheet_name,
                            "attachment_name": attachment_name,
                            "pass_number": pass_num,
                            "result": None,
                            "prompt_text": "",
                            "model": "",
                            "duration": 0.0,
                            "status": "failed",
                            "error": error_msg,
                        }
                        exc_entries.append(entry)
                        sheet_pass_results.append(entry)
                    if on_sheet_complete:
                        on_sheet_complete(exc_entries)

        successful_results = [r for r in results if r.get("status") == "success"]
        combined_path = base_output_path.parent / f"{base_name}_combined.json"
        combined_schema_result = None
        combined_overlap_result = None

        if successful_results:
            combined_data = _merge_sheet_jsons(successful_results, base_name, output_dir)
            combined_path.write_text(json.dumps(combined_data, indent=2), encoding="utf-8")
            combined_schema_result, combined_overlap_result = _run_validators(combined_path)
            logger.info(f"Combined JSON saved: {combined_path.name}")

        duration = (datetime.now() - start_time).total_seconds()
        return PipelineResult(
            cics_doc=combined_data if successful_results else {},
            output_path=combined_path if successful_results else base_output_path,
            validation_issues=combined_schema_result.errors if combined_schema_result else [],
            overlap_issues=combined_overlap_result.overlaps if combined_overlap_result else [],
            duration=duration,
            sheet_results=results,
            errors=[e["error"] for e in errors],
            sheet_pass_results=sheet_pass_results,
        )

    def _process_direct(
        self,
        file_path: str,
        output_path: Path,
    ) -> PipelineResult:
        """
        Legacy direct processing without content indexing.

        Routes Excel to sheet-by-sheet; email/PDF through single-document flow.
        """
        start_time = datetime.now()
        file_path_obj = Path(file_path)
        file_ext = file_path_obj.suffix.lower()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_dir = output_path.parent

        logger.info(f"Direct processing: {file_path}")

        if file_ext in [".xlsx", ".xls", ".xlsm"]:
            if self.excel_mode == "sheet_by_sheet":
                return self._process_excel_sheets(str(file_path), output_path)
            else:
                from cins.processors.content.indexer import _convert_excel_to_csv
                csv_output_dir = output_dir / "csv_converted"
                csv_output_dir.mkdir(parents=True, exist_ok=True)
                csv_data = _convert_excel_to_csv(file_path, csv_output_dir)
                content_str = "\n\n".join(
                    v for v in csv_data["sheet_contents"].values()
                )

        elif file_ext == ".eml":
            email_data = extract_eml_content(file_path, output_dir=str(output_dir))
            excel_attachments = [
                att for att in email_data.get("attachments", []) if "temp_path" in att
            ]
            if excel_attachments:
                return self._process_email_with_attachments(email_data, output_path)
            content_str = _format_eml_content(email_data)

        elif file_ext == ".pdf":
            content_str = extract_pdf_text(file_path)

        else:
            raise ValueError(f"Unsupported file type: {file_ext}")

        temp_content = output_dir / "extracted_content.txt"
        temp_content.write_text(content_str, encoding="utf-8")
        _, user_prompt = prepare_extraction_request(str(temp_content))

        source_type_map = {".eml": "email", ".pdf": "pdf", ".xlsx": "excel", ".xls": "excel", ".xlsm": "excel"}
        source_type = source_type_map.get(file_ext)

        extract_response = extract(
            user_prompt,
            return_prompts=True,
            output_dir=str(output_dir),
            source_type=source_type,
        )

        json_result = extract_response["result"]
        prompt_path = output_path.parent / (output_path.stem + "_prompt.txt")
        prompt_path.write_text(
            f"=== SYSTEM INSTRUCTION ===\n{extract_response['system_instruction']}\n\n=== USER PROMPT ===\n{extract_response['user_prompt']}",
            encoding="utf-8",
        )

        try:
            parsed_result = json.loads(json_result)
            if parsed_result.get("status") == "failed":
                raise RuntimeError(parsed_result.get("error", "Unknown LLM error"))
        except json.JSONDecodeError:
            if "error" in json_result.lower():
                raise RuntimeError(f"LLM extraction failed: {json_result[:500]}")
            parsed_result = {}

        output_path.write_text(json_result, encoding="utf-8")
        schema_result, overlap_result = _run_validators(output_path)
        duration = (datetime.now() - start_time).total_seconds()

        return PipelineResult(
            cics_doc=parsed_result,
            output_path=output_path,
            validation_issues=schema_result.errors if schema_result else [],
            overlap_issues=overlap_result.overlaps if overlap_result else [],
            duration=duration,
        )

    def _process_excel_sheets(
        self,
        excel_path: str,
        base_output_path: Path,
        context_str: str = "",
        attachment_name: str = "",
    ) -> PipelineResult:
        """Process each Excel sheet separately by converting to CSV."""
        import openpyxl
        from cins.processors.content.indexer import _convert_excel_to_csv

        start_time = datetime.now()
        output_path_obj = Path(base_output_path)
        output_dir = output_path_obj.parent
        base_name = output_path_obj.stem
        output_dir.mkdir(parents=True, exist_ok=True)

        wb = openpyxl.load_workbook(excel_path, data_only=True)
        sheet_names = wb.sheetnames
        wb.close()

        csv_output_dir = output_dir / "csv_converted"
        csv_output_dir.mkdir(parents=True, exist_ok=True)

        csv_data = _convert_excel_to_csv(excel_path, csv_output_dir)
        sheet_contents = csv_data["sheet_contents"]
        master_sheets = csv_data["master_sheets"]
        commission_sheets = csv_data["commission_sheets"]

        master_context = ""
        if master_sheets:
            master_context = "=" * 80 + "\nMASTER DATA (GLOBAL RULES)\n" + "=" * 80 + "\n"
            for ms in master_sheets:
                master_context += ms["formatted_content"] + "\n\n"

        sheets_to_process = (
            [s["sheet_name"] for s in commission_sheets] if commission_sheets else sheet_names
        )

        results = []
        errors = []

        for sheet_idx, sheet_name in enumerate(sheets_to_process, 1):
            logger.info(f"Processing sheet {sheet_idx}/{len(sheets_to_process)}: {sheet_name}")
            try:
                safe_name = get_safe_sheet_name(sheet_name)
                sheet_output_path = output_dir / f"{base_name}_{safe_name}.json"

                content_str = sheet_contents.get(sheet_name, "")
                if not content_str:
                    raise ValueError(f"Sheet '{sheet_name}' not found in CSV results")

                if master_context:
                    content_str = f"{master_context}\n\n{content_str}"
                if context_str:
                    content_str = f"{context_str}\n\n{content_str}"

                temp_path = output_dir / f"extracted_content_{safe_name}.txt"
                temp_path.write_text(content_str, encoding="utf-8")
                _, user_prompt = prepare_extraction_request(str(temp_path))

                extract_response = extract(
                    user_prompt,
                    return_prompts=True,
                    output_dir=str(output_dir),
                    raw_filename=f"raw_extraction_{safe_name}.json",
                    filename_prefix=f"{safe_name}_",
                    source_type="excel",
                )

                json_result = extract_response["result"]
                prompt_path = output_dir / f"{base_name}_{safe_name}_prompt.txt"
                prompt_path.write_text(
                    f"=== SYSTEM INSTRUCTION ===\n{extract_response['system_instruction']}\n\n=== USER PROMPT ===\n{extract_response['user_prompt']}",
                    encoding="utf-8",
                )

                try:
                    parsed_result = json.loads(json_result)
                    if parsed_result.get("status") == "failed":
                        raise ValueError(parsed_result.get("error", "Unknown LLM error"))
                except json.JSONDecodeError:
                    if "error" in json_result.lower():
                        raise ValueError(f"LLM extraction failed: {json_result[:500]}")
                    parsed_result = {}

                sheet_output_path.write_text(json_result, encoding="utf-8")
                schema_result, overlap_result = _run_validators(sheet_output_path)
                validation_status = (
                    _build_validation_status(schema_result, overlap_result)
                    if schema_result else "UNKNOWN"
                )

                results.append({
                    "sheet": sheet_name,
                    "status": "success",
                    "output_path": str(sheet_output_path),
                    "output_filename": sheet_output_path.name,
                    "validation_status": validation_status,
                    "json_data": parsed_result,
                })
                logger.info(f"  ✓ '{sheet_name}' processed")

            except Exception as e:
                error_msg = str(e)
                logger.error(f"  ✗ '{sheet_name}' failed: {error_msg}")
                errors.append({"sheet": sheet_name, "error": error_msg})
                results.append({"sheet": sheet_name, "status": "failed", "error": error_msg})

        successful_results = [r for r in results if r.get("status") == "success"]
        combined_path = output_dir / f"{base_name}_combined.json"
        combined_schema_result = None
        combined_overlap_result = None
        combined_data = {}

        if successful_results:
            combined_data = _merge_sheet_jsons(successful_results, base_name, output_dir)
            combined_path.write_text(json.dumps(combined_data, indent=2), encoding="utf-8")
            combined_schema_result, combined_overlap_result = _run_validators(combined_path)

        duration = (datetime.now() - start_time).total_seconds()
        return PipelineResult(
            cics_doc=combined_data,
            output_path=combined_path if successful_results else base_output_path,
            validation_issues=combined_schema_result.errors if combined_schema_result else [],
            overlap_issues=combined_overlap_result.overlaps if combined_overlap_result else [],
            duration=duration,
            sheet_results=results,
            errors=[e["error"] for e in errors],
        )

    def _process_email_with_attachments(
        self,
        email_data: Dict,
        output_path: Path,
    ) -> PipelineResult:
        """Process email with Excel attachments — routes each attachment to sheet-by-sheet."""
        import os
        import shutil

        start_time = datetime.now()
        output_path_obj = Path(output_path)
        output_dir = output_path_obj.parent
        base_name = output_path_obj.stem
        output_dir.mkdir(parents=True, exist_ok=True)

        email_context = _format_email_context(email_data)
        excel_attachments = [
            att for att in email_data.get("attachments", []) if "temp_path" in att
        ]

        all_results = []
        all_errors = []
        all_sheet_pass_results = []

        for att_idx, attachment in enumerate(excel_attachments, 1):
            temp_path = attachment["temp_path"]
            att_filename = Path(attachment["filename"]).stem
            logger.info(f"Processing attachment {att_idx}/{len(excel_attachments)}: {attachment['filename']}")

            try:
                att_base_path = output_dir / f"{base_name}_{att_filename}.json"
                res = self._process_excel_sheets(
                    str(temp_path),
                    att_base_path,
                    context_str=email_context,
                    attachment_name=attachment["filename"],
                )
                for r in res.sheet_results:
                    r["attachment"] = attachment["filename"]
                    all_results.append(r)
                for e_msg in res.errors:
                    all_errors.append({"attachment": attachment["filename"], "error": e_msg})
                for spr in res.sheet_pass_results:
                    all_sheet_pass_results.append(spr)

            except Exception as e:
                all_errors.append({
                    "attachment": attachment["filename"],
                    "error": str(e),
                })

            finally:
                try:
                    if temp_path and Path(temp_path).exists():
                        safe_stem = Path(attachment["filename"]).stem.replace(" ", "_")
                        saved = output_dir / f"attachment_{safe_stem}{Path(attachment['filename']).suffix}"
                        shutil.copy2(temp_path, saved)
                        os.unlink(temp_path)
                except Exception as e:
                    logger.warning(f"Could not save/clean attachment: {e}")

        successful_results = [r for r in all_results if r.get("status") == "success"]
        combined_path = output_dir / f"{base_name}_combined.json"
        combined_schema_result = None
        combined_overlap_result = None
        combined_data = {}

        if successful_results:
            combined_data = _merge_sheet_jsons(successful_results, base_name, output_dir)
            combined_path.write_text(json.dumps(combined_data, indent=2), encoding="utf-8")
            combined_schema_result, combined_overlap_result = _run_validators(combined_path)

        duration = (datetime.now() - start_time).total_seconds()
        return PipelineResult(
            cics_doc=combined_data,
            output_path=combined_path if successful_results else output_path,
            validation_issues=combined_schema_result.errors if combined_schema_result else [],
            overlap_issues=combined_overlap_result.overlaps if combined_overlap_result else [],
            duration=duration,
            sheet_results=all_results,
            errors=[e.get("error", "") for e in all_errors],
            sheet_pass_results=all_sheet_pass_results,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    parser = argparse.ArgumentParser(
        description="CINS Pipeline — extract commission data from emails, PDFs, and Excel files"
    )
    parser.add_argument("file", help="Path to input file (.eml, .pdf, .xlsx)")
    parser.add_argument("--provider", default="gemini", choices=["gemini", "openai"],
                        help="LLM provider (default: gemini)")
    parser.add_argument("--model", default=None, help="Model name (auto-selected if omitted)")
    parser.add_argument("--output-dir", default="outputs", help="Output directory (default: outputs/)")
    parser.add_argument("--indexed-dir", default=None, help="Index cache directory")
    parser.add_argument("--force-reindex", action="store_true",
                        help="Re-extract even if index cache exists")
    parser.add_argument("--excel-mode", default="sheet_by_sheet",
                        choices=["sheet_by_sheet", "all_at_once"])
    args = parser.parse_args()

    pipeline = Pipeline(
        llm_provider=args.provider,
        model=args.model,
        indexed_dir=Path(args.indexed_dir) if args.indexed_dir else None,
        output_dir=Path(args.output_dir),
        excel_mode=args.excel_mode,
    )

    try:
        result = pipeline.process(args.file, force_reindex=args.force_reindex)
        print(f"Output: {result.output_path}")
        print(f"Duration: {result.duration:.2f}s")
        if result.warnings:
            print(f"Warnings: {len(result.warnings)}")
        if result.errors:
            print(f"Errors: {len(result.errors)}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Pipeline failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
