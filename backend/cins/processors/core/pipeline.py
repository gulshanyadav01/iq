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
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from cins.processors.content.indexer import ContentIndexer, IndexedDocument
from cins.processors.content.prepare_prompt import prepare_extraction_request
from cins.processors.core.cpa_split import materialize_cpa_split

logger = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Result returned by Pipeline processing methods."""

    cics_doc: dict  # parsed CICS JSON
    output_path: Path  # where the file was written
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    validation_issues: list = field(default_factory=list)  # from SchemaValidator
    overlap_issues: list = field(default_factory=list)  # from RuleOverlapChecker
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


def derive_pipeline_routing(
    source_type: str,
    sheet_classifications: dict,
    sheet_names: list,
    excel_attachments: list,
) -> tuple:
    """
    Compute the (sheets_to_process, attachment_name) the pipeline will use.

    Single source of truth shared by `_process_sheets` and the pre-create loop
    in `commission_intake.tasks.run_extraction`. Keep these in lockstep so the
    pre-created PENDING ExtractionPass rows match the rows the pipeline writes.

    Args:
        source_type: indexed_doc.source_type ("email", "excel", "pdf", ...)
        sheet_classifications: {sheet_name: {"sheet_type": "commission_data"|...}}
        sheet_names: ordered list of all sheet names available
        excel_attachments: list of attachment filenames (email source only)

    Returns:
        (sheets_to_process, attachment_name)
    """
    commission_sheets = [
        name
        for name, info in (sheet_classifications or {}).items()
        if (info or {}).get("sheet_type") == "commission_data"
    ]
    sheets_to_process = commission_sheets if commission_sheets else list(sheet_names)

    # Mirrors process_from_indexed: use the attachment name only when there is
    # exactly one attachment for an email source — otherwise blank.
    attachment_name = ""
    if source_type == "email" and len(excel_attachments or []) == 1:
        attachment_name = excel_attachments[0]

    return sheets_to_process, attachment_name


_PIPE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def _strip_common_header_prefix(header_line: str) -> tuple[str, str]:
    """
    If every column header in a markdown pipe-row shares a common
    `"<title> - "` prefix (a pattern the email indexer produces when it
    flattens HTML tables that had a title row), strip the prefix off and
    return (title, cleaned_header_line).

    Returns ("", header_line) when no common prefix is detected.
    """
    cells = [c.strip() for c in header_line.strip().strip("|").split("|")]
    cells = [c for c in cells if c]
    if len(cells) < 2:
        return "", header_line
    sep = " - "
    prefixes = []
    for c in cells:
        idx = c.find(sep)
        if idx <= 0:
            return "", header_line
        prefixes.append(c[:idx])
    title = prefixes[0]
    if not all(p == title for p in prefixes):
        return "", header_line
    cleaned = (
        "| " + " | ".join(c[len(title) + len(sep) :].strip() for c in cells) + " |"
    )
    return title, cleaned


def _promote_table_headings(body: str) -> str:
    """
    Promote plain-text section titles to typed `## ` headings when they
    immediately precede a markdown pipe table, so emails arrive at Pass 3
    with the same `[type: commission]` shape Excel sheets do.

    Two patterns are handled:

    1. Bare title line directly above the table:

           GCV – STP Grid for Flatbed Trailers
           | RTO State | RTO Division | Payout |
           | --- | --- | --- |
           | TN | Chennai | 30% |

       → `## GCV – STP Grid for Flatbed Trailers [type: commission]`

    2. Title baked into every column header (the email indexer does this
       when an HTML table had a title row above the column row):

           | GCV Grid - RTO State | GCV Grid - Payout |
           | --- | --- |
           | TN01 | 30% |

       → title `GCV Grid` lifted into a `[type: commission]` heading and
       the column headers cleaned to `RTO State | Payout`.

    Without this the codegen helper `parse_md_tables` swallows the table
    into the leading-context block (`name=None`), and the LLM cannot rely
    on the prompt's `[type: commission]` contract to find it.
    """
    lines = body.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Pattern 1: bare title line above a pipe-table row.
        looks_like_title = (
            stripped
            and len(stripped) <= 120
            and "|" not in stripped
            and not stripped.startswith(("#", "-", "*", ">", "`"))
        )
        if looks_like_title:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _PIPE_ROW_RE.match(lines[j]):
                out.append(f"## {stripped} [type: commission]")
                i += 1
                continue

        # Pattern 2: pipe-table row whose every column shares a "<title> - " prefix.
        if _PIPE_ROW_RE.match(line):
            already_under_heading = any(ln.strip().startswith("## ") for ln in out)
            if not already_under_heading:
                title, cleaned = _strip_common_header_prefix(line)
                if title:
                    out.append(f"## {title} [type: commission]")
                    out.append(cleaned)
                    i += 1
                    continue

        out.append(line)
        i += 1
    return "\n".join(out)


def build_pass_contents(
    sheet_name: str,
    sheet_content: str,
    master_context: str,
    email_body: str,
    commission_sheets: list,
) -> tuple:
    """
    Build the per-pass content strings for a single sheet extraction.

    Single source of truth shared by both the full pipeline (_process_sheets)
    and the per-pass rerun task (rerun_single_pass). Any routing change
    (new source types, new header formats, etc.) only needs to happen here.

    Returns:
        (pass1_content, pass2_content, pass3_content, email_is_primary_commission)
    """
    email_is_primary_commission = bool(email_body) and not commission_sheets

    pass1_content = (email_body + "\n\n") if email_body else ""
    pass1_content += master_context

    pass2_content = master_context

    if email_is_primary_commission:
        pass3_content = (
            f"## Email Commission Data\n\n{_promote_table_headings(email_body)}"
        )
    else:
        pass3_content = f"## Commission Data Sheet: {sheet_name}\n\n{sheet_content}"

    return pass1_content, pass2_content, pass3_content, email_is_primary_commission


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
            requires_review_flags.append(
                validation.get("requires_manual_review", False)
            )
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
            "overall_confidence": min(all_confidence_scores)
            if all_confidence_scores
            else None,
            "requires_manual_review": any(requires_review_flags)
            if requires_review_flags
            else False,
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


def _run_validators(output_path: Path, indexed_doc=None):
    """Run SchemaValidator + RuleOverlapChecker (+ RowCoverageValidator when the
    source grid is available). Returns (schema_result, overlap_result).

    When ``indexed_doc`` is supplied, the row-coverage validator asserts every
    non-empty grid data row traces to >=1 emitted rule (the inverse of
    SchemaValidator's forward trace check). Its issues are merged into
    ``schema_result`` so existing consumers surface them unchanged.
    """
    try:
        from cins.validators.schema_validator import SchemaValidator
        from cins.validators.rule_overlap_checker import RuleOverlapChecker

        schema_result = SchemaValidator().validate(str(output_path))
        overlap_result = RuleOverlapChecker().check(str(output_path))
    except ImportError:
        logger.warning("Validators not available; skipping validation")
        return None, None
    if indexed_doc is not None and schema_result is not None:
        try:
            from cins.validators.row_coverage_validator import RowCoverageValidator

            sheet_contents = indexed_doc.get_all_sheet_contents()
            rc_result = RowCoverageValidator().validate(
                str(output_path), sheet_contents
            )
            schema_result.errors.extend(rc_result.errors)
            schema_result.warnings.extend(rc_result.warnings)
            schema_result.info.extend(rc_result.info)
        except Exception as e:  # never let coverage checking break the pipeline
            logger.warning(f"Row-coverage validator failed: {e}")
        try:
            from cins.validators.coverage_integrity_validator import (
                CoverageIntegrityValidator,
            )

            ci_result = CoverageIntegrityValidator().validate(
                str(output_path), indexed_doc.get_all_sheet_contents()
            )
            schema_result.errors.extend(ci_result.errors)
            schema_result.warnings.extend(ci_result.warnings)
            schema_result.info.extend(ci_result.info)
        except Exception as e:  # never let coverage checking break the pipeline
            logger.warning(f"Coverage-integrity validator failed: {e}")
    return schema_result, overlap_result


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
        self.indexed_dir = (
            Path(indexed_dir) if indexed_dir else cins_root / "input-indexed"
        )
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

        indexer = ContentIndexer(self.indexed_dir, llm_provider=self.llm_provider)
        document_id = indexer._sanitize_filename(file_path.stem)

        indexed_doc = indexer.get_indexed_document(document_id, provider)

        if (
            indexed_doc
            and not force_reindex
            and indexer.is_cache_valid(indexed_doc, file_path)
        ):
            logger.info(f"Cache hit: {document_id}")
        else:
            logger.info(f"Indexing: {document_id}")
            indexed_doc = indexer.index_document(
                str(file_path),
                provider,
                excel_mode=self.excel_mode,
                force_reindex=force_reindex,
            )

        resolved_output_path = output_path or self._default_output_path(
            file_path, provider
        )
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
        indexer = ContentIndexer(self.indexed_dir, llm_provider=self.llm_provider)
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
            source_meta = indexed_doc.metadata.get("source_metadata", {})
            _, attachment_name = derive_pipeline_routing(
                source_type=indexed_doc.source_type,
                sheet_classifications=indexed_doc.get_sheet_classifications(),
                sheet_names=list(indexed_doc.get_all_sheet_contents().keys()),
                excel_attachments=source_meta.get("excel_attachments", []),
            )
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

        from cins.schema.extraction_passes import (
            MetadataExtraction,
            MasterDataExtraction,
        )
        from cins.schema.raw_extraction import RawExtraction
        from cins.processors.core.llm_extract import _extract_pass3_via_codegen
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
            f
            for f in existing_cics
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
        service = get_llm_service(provider=self.llm_provider)
        rules_extraction, _, _ = _extract_pass3_via_codegen(
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
        raw_extraction = RawExtraction.from_passes(
            metadata, master_data_extraction, rules_extraction
        )
        merged_out = output_folder / "merged_raw_extraction.json"
        merged_out.write_text(
            json.dumps(raw_extraction.model_dump(exclude_none=True), indent=2),
            encoding="utf-8",
        )

        logger.info("Transforming to CICS")
        transformer = RawToCICSTransformer()
        cics_doc, transform_warnings, transform_errors = transformer.transform(
            raw_extraction
        )
        cics_json = cics_doc.model_dump(exclude_none=False)
        # Materialize CPA-deduction rules into With/Without-CPA variants so the
        # canonical document, the validators, and every consumer see the split.
        cics_json = materialize_cpa_split(cics_json)

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
        """Process a single-unit document (email without Excel attachments)."""
        from cins.processors.core.llm_extract import (
            extract_multi_pass,
            get_pass1_metadata_prompt,
            get_pass2_master_data_codegen_prompt,
            get_pass3_rules_codegen_prompt,
        )
        from cins.processors.llm_services.factory import get_llm_service

        output_dir = output_path.parent
        sheet_name = ""  # no sheet for plain emails
        sheet_pass_results = []
        on_sheet_complete = self._on_sheet_complete_callback

        try:
            model_used = get_llm_service(provider=self.llm_provider).model
        except Exception:
            model_used = ""

        content_str = indexed_doc.get_primary_content()
        temp_content = output_dir / "extracted_content.txt"
        temp_content.write_text(content_str, encoding="utf-8")

        _, user_prompt = prepare_extraction_request(str(temp_content))

        system_instructions = {
            1: get_pass1_metadata_prompt(),
            2: get_pass2_master_data_codegen_prompt(),
            3: get_pass3_rules_codegen_prompt(),
        }

        def _on_pass_done(pass_num, result_dict, duration, extra=None):
            _extra = extra or {}
            entry = {
                "sheet_name": sheet_name,
                "attachment_name": "",
                "pass_number": pass_num,
                "result": result_dict,
                "prompt_text": _extra.get("user_prompt") or user_prompt,
                "system_instruction": system_instructions.get(pass_num, ""),
                "model": model_used,
                "duration": duration,
                "status": "completed",
                "error": "",
                "extraction_method": _extra.get("extraction_method", "llm"),
                "codegen_code": _extra.get("codegen_code", ""),
            }
            sheet_pass_results.append(entry)
            if on_sheet_complete:
                on_sheet_complete([entry])

        def _on_pass_start(pass_num):
            from cins.processors.llm_services.langfuse_client import (
                set_trace_context,
                get_trace_context,
                make_pass_trace_id,
            )

            ctx = get_trace_context()
            if ctx["session_id"]:
                set_trace_context(
                    session_id=ctx["session_id"],
                    trace_id=make_pass_trace_id(ctx["session_id"], pass_num),
                    trace_name=f"pass-{pass_num}",
                    metadata={"document_id": ctx["session_id"], "pass": pass_num},
                )

        source_type = indexed_doc.metadata.get("source_type")
        pass_result = extract_multi_pass(
            user_prompt,
            return_passes=True,
            output_dir=str(output_dir),
            source_type=source_type,
            on_pass_complete=_on_pass_done,
            on_pass_start=_on_pass_start,
            provider=self.llm_provider,
        )

        if isinstance(pass_result, dict) and "cics" in pass_result:
            parsed_result = pass_result["cics"]
            json_result = json.dumps(parsed_result, indent=2)
        else:
            json_result = (
                json.dumps(pass_result)
                if not isinstance(pass_result, str)
                else pass_result
            )
            try:
                parsed_result = json.loads(json_result)
            except Exception:
                parsed_result = {}

        parsed_result = materialize_cpa_split(parsed_result)
        json_result = json.dumps(parsed_result, indent=2)
        output_path.write_text(json_result, encoding="utf-8")

        schema_result, overlap_result = _run_validators(output_path, indexed_doc)

        duration = (datetime.now() - start_time).total_seconds()
        return PipelineResult(
            cics_doc=parsed_result,
            output_path=output_path,
            validation_issues=schema_result.errors if schema_result else [],
            overlap_issues=overlap_result.overlaps if overlap_result else [],
            duration=duration,
            sheet_pass_results=sheet_pass_results,
        )

    def _process_sheets(
        self,
        indexed_doc: IndexedDocument,
        base_output_path: Path,
        attachment_name: str = "",
        on_sheet_complete=None,
    ) -> PipelineResult:
        """Process indexed sheets using multi-pass extraction."""
        from cins.processors.core.llm_extract import (
            extract_multi_pass,
            get_pass1_metadata_prompt,
            get_pass2_master_data_codegen_prompt,
            get_pass3_rules_codegen_prompt,
        )

        system_instructions = {
            1: get_pass1_metadata_prompt(),
            2: get_pass2_master_data_codegen_prompt(),
            3: get_pass3_rules_codegen_prompt(),
        }

        start_time = datetime.now()
        output_dir = base_output_path.parent
        base_name = base_output_path.stem
        output_dir.mkdir(parents=True, exist_ok=True)

        sheet_contents = indexed_doc.get_all_sheet_contents()
        commission_sheets = indexed_doc.get_commission_sheets()
        master_sheets = indexed_doc.get_master_sheets()

        if commission_sheets:
            logger.info(
                f"{len(commission_sheets)} commission, {len(master_sheets)} master sheets"
            )
        else:
            logger.warning("No commission sheets classified; processing all sheets")

        # Build master data context
        master_context = ""
        for sheet_name in master_sheets:
            content = sheet_contents.get(sheet_name, "")
            if content:
                master_context += (
                    f"\n\n## Master Data Reference: {sheet_name}\n\n{content}"
                )

        source_meta = indexed_doc.metadata.get("source_metadata", {})
        sheets_to_process, _ = derive_pipeline_routing(
            source_type=indexed_doc.source_type,
            sheet_classifications=indexed_doc.get_sheet_classifications(),
            sheet_names=list(sheet_contents.keys()),
            excel_attachments=source_meta.get("excel_attachments", []),
        )
        total_sheets = len(sheets_to_process)

        email_body_for_pass3 = ""
        if indexed_doc.is_email:
            _email_body = indexed_doc.get_primary_content()
            if _email_body:
                email_body_for_pass3 = _email_body

        results = []
        errors = []
        sheet_pass_results = []

        # All shared mutable state is guarded by this lock. Per-sheet workers
        # only need it when appending to `sheet_pass_results`, `results`, or
        # `errors`, and when invoking the `on_sheet_complete` callback (the
        # callback writes to Django ORM and we want serialised writes per row).
        results_lock = threading.Lock()

        from cins.processors.llm_services.factory import get_llm_service

        try:
            model_used = get_llm_service(provider=self.llm_provider).model
        except Exception:
            model_used = ""

        # Capture parent thread's langfuse trace context once so worker threads
        # can re-establish it (set_trace_context uses contextvars which don't
        # propagate across thread boundaries).
        from cins.processors.llm_services.langfuse_client import (
            get_trace_context,
            set_trace_context,
            clear_trace_context,
            make_pass_trace_id,
        )

        parent_trace_ctx = get_trace_context()

        def _process_one_sheet(sheet_idx: int, sheet_name: str):
            """Run all 3 passes for a single sheet. Safe to call concurrently."""
            # Re-bind the parent trace context onto this worker thread.
            if parent_trace_ctx.get("session_id"):
                set_trace_context(**parent_trace_ctx)
            try:
                logger.info(
                    f"Processing sheet {sheet_idx}/{total_sheets}: {sheet_name}"
                )
                content = sheet_contents.get(sheet_name, "")
                if not content:
                    logger.warning(f"Skipping '{sheet_name}' — no content")
                    return

                safe_name = get_safe_sheet_name(sheet_name)
                sheet_output_path = output_dir / f"{base_name}_{safe_name}.json"

                # Build per-pass content via shared helper (also used by rerun_single_pass)
                (
                    pass1_content,
                    pass2_content,
                    pass3_content,
                    email_is_primary_commission,
                ) = build_pass_contents(
                    sheet_name=sheet_name,
                    sheet_content=content,
                    master_context=master_context,
                    email_body=email_body_for_pass3,
                    commission_sheets=commission_sheets,
                )
                if email_is_primary_commission:
                    logger.info(
                        f"  '{sheet_name}': email body used as Pass 3 commission content"
                    )

                for pass_name, pass_content in [
                    (f"pass1_content_{safe_name}.txt", pass1_content),
                    (f"pass2_content_{safe_name}.txt", pass2_content),
                    (f"pass3_content_{safe_name}.txt", pass3_content),
                ]:
                    (output_dir / pass_name).write_text(pass_content, encoding="utf-8")

                _, pass1_prompt = prepare_extraction_request(
                    str(output_dir / f"pass1_content_{safe_name}.txt")
                )
                _, pass2_prompt = prepare_extraction_request(
                    str(output_dir / f"pass2_content_{safe_name}.txt")
                )
                _, pass3_prompt = prepare_extraction_request(
                    str(output_dir / f"pass3_content_{safe_name}.txt")
                )

                pass_prompts = {
                    1: pass1_prompt,
                    2: pass2_prompt,
                    3: pass3_prompt,
                }

                def _on_pass_done(pass_num, result_dict, duration, extra=None):
                    _extra = extra or {}
                    entry = {
                        "sheet_name": sheet_name,
                        "attachment_name": attachment_name,
                        "pass_number": pass_num,
                        "result": result_dict,
                        "prompt_text": _extra.get("user_prompt")
                        or pass_prompts.get(pass_num, ""),
                        "system_instruction": system_instructions.get(pass_num, ""),
                        "model": model_used,
                        "duration": duration,
                        "status": "completed",
                        "error": _extra.get("skip_reason", "")
                        if _extra.get("skipped")
                        else "",
                        "extraction_method": _extra.get("extraction_method", "llm"),
                        "codegen_code": _extra.get("codegen_code", ""),
                    }
                    with results_lock:
                        sheet_pass_results.append(entry)
                        if on_sheet_complete:
                            on_sheet_complete([entry])

                def _on_pass_start(pass_num):
                    ctx = get_trace_context()
                    if ctx["session_id"]:
                        set_trace_context(
                            session_id=ctx["session_id"],
                            trace_id=make_pass_trace_id(
                                ctx["session_id"], pass_num, sheet_name, attachment_name
                            ),
                            trace_name=f"pass-{pass_num}-{safe_name}",
                            metadata={
                                "document_id": ctx["session_id"],
                                "pass": pass_num,
                                "sheet": sheet_name,
                                "attachment": attachment_name,
                            },
                        )

                try:
                    pass_result = extract_multi_pass(
                        pass1_prompt,
                        return_passes=True,
                        output_dir=str(output_dir),
                        filename_prefix=f"{safe_name}_",
                        source_type="excel",
                        email_and_master_data_content=pass1_prompt,
                        master_data_content=pass2_prompt,
                        commission_data_content=pass3_prompt,
                        # When email body IS the commission content it's already embedded in
                        # pass3_prompt; skip the separate email_body_content prepend to avoid
                        # mixing it with master sheet data.
                        email_body_content=None
                        if email_is_primary_commission
                        else (email_body_for_pass3 or None),
                        on_pass_complete=_on_pass_done,
                        on_pass_start=_on_pass_start,
                        provider=self.llm_provider,
                    )
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"  ✗ '{sheet_name}' failed: {error_msg}")
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
                    with results_lock:
                        errors.append({"sheet": sheet_name, "error": error_msg})
                        results.append(
                            {
                                "sheet": sheet_name,
                                "status": "failed",
                                "error": error_msg,
                            }
                        )
                        sheet_pass_results.extend(exc_entries)
                        if on_sheet_complete:
                            on_sheet_complete(exc_entries)
                    return

                # pass_result is a dict when return_passes=True
                if isinstance(pass_result, dict) and "cics" in pass_result:
                    parsed_result = pass_result["cics"]
                else:
                    # Unexpected shape — mark any passes not yet persisted as failed.
                    # `extract_multi_pass` swallows upstream exceptions (e.g.
                    # GeminiAPIError 429) and returns a JSON string shaped like
                    # {"error": "Multi-pass extraction failed: ...", "status": "failed"}.
                    # Surface that string so the DB row points at the real cause
                    # (rate limit, quota, connectivity) instead of this generic label.
                    json_result = (
                        json.dumps(pass_result)
                        if not isinstance(pass_result, str)
                        else pass_result
                    )
                    try:
                        parsed_result = json.loads(json_result)
                    except Exception:
                        parsed_result = {}
                    upstream_error = ""
                    if isinstance(parsed_result, dict):
                        err = parsed_result.get("error")
                        if isinstance(err, str) and err.strip():
                            upstream_error = err.strip()
                        else:
                            errs = parsed_result.get("errors")
                            if isinstance(errs, list) and errs:
                                upstream_error = "; ".join(str(x) for x in errs if x)
                            elif isinstance(errs, str) and errs.strip():
                                upstream_error = errs.strip()
                    error_msg = upstream_error or "Unexpected multi-pass result shape"
                    with results_lock:
                        persisted_pass_nums = {
                            e["pass_number"]
                            for e in sheet_pass_results
                            if e.get("sheet_name") == sheet_name
                        }
                        failed_entries = []
                        for pass_num in (1, 2, 3):
                            if pass_num not in persisted_pass_nums:
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
                                failed_entries.append(entry)
                                sheet_pass_results.append(entry)
                        if failed_entries and on_sheet_complete:
                            on_sheet_complete(failed_entries)

                prompt_path = output_dir / f"{base_name}_{safe_name}_prompt.txt"
                prompt_path.write_text(
                    f"=== PASS PROMPTS ===\n=== Pass 1 ===\n{pass1_prompt}\n\n=== Pass 2 ===\n{pass2_prompt}\n\n=== Pass 3 ===\n{pass3_prompt}",
                    encoding="utf-8",
                )

                sheet_output_path.write_text(
                    json.dumps(parsed_result, indent=2)
                    if isinstance(parsed_result, dict)
                    else json.dumps({}),
                    encoding="utf-8",
                )

                schema_result, overlap_result = _run_validators(
                    sheet_output_path, indexed_doc
                )
                validation_status = (
                    _build_validation_status(schema_result, overlap_result)
                    if schema_result
                    else "UNKNOWN"
                )

                with results_lock:
                    results.append(
                        {
                            "sheet": sheet_name,
                            "status": "success",
                            "output_path": str(sheet_output_path),
                            "output_filename": sheet_output_path.name,
                            "validation_status": validation_status,
                            "json_data": parsed_result,
                        }
                    )
                logger.info(f"  ✓ '{sheet_name}' processed")
            finally:
                # Don't leak per-sheet trace context back into the parent thread.
                clear_trace_context()

        # Tunable concurrency: default 4, override via CINS_SHEET_CONCURRENCY.
        # Each worker is a single LLM-bound HTTP path, so threads are fine
        # (no GIL contention on JSON or HTTP I/O).
        try:
            max_workers = int(os.environ.get("CINS_SHEET_CONCURRENCY", "4"))
        except ValueError:
            max_workers = 4
        max_workers = max(1, min(max_workers, len(sheets_to_process) or 1))

        if max_workers == 1 or len(sheets_to_process) <= 1:
            for sheet_idx, sheet_name in enumerate(sheets_to_process, 1):
                _process_one_sheet(sheet_idx, sheet_name)
        else:
            logger.info(
                f"Processing {len(sheets_to_process)} sheets with {max_workers} threads"
            )
            with ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="cins-sheet"
            ) as executor:
                futures = [
                    executor.submit(_process_one_sheet, idx, name)
                    for idx, name in enumerate(sheets_to_process, 1)
                ]
                for fut in as_completed(futures):
                    # Surface any unexpected exception from the worker — the
                    # function already does internal try/except, but a bug in
                    # that logic shouldn't silently swallow.
                    fut.result()

        successful_results = [r for r in results if r.get("status") == "success"]
        combined_path = base_output_path.parent / f"{base_name}_combined.json"
        combined_schema_result = None
        combined_overlap_result = None

        if successful_results:
            combined_data = _merge_sheet_jsons(
                successful_results, base_name, output_dir
            )
            combined_data = materialize_cpa_split(combined_data)
            combined_path.write_text(
                json.dumps(combined_data, indent=2), encoding="utf-8"
            )
            combined_schema_result, combined_overlap_result = _run_validators(
                combined_path, indexed_doc
            )
            logger.info(f"Combined JSON saved: {combined_path.name}")

        duration = (datetime.now() - start_time).total_seconds()
        return PipelineResult(
            cics_doc=combined_data if successful_results else {},
            output_path=combined_path if successful_results else base_output_path,
            validation_issues=combined_schema_result.errors
            if combined_schema_result
            else [],
            overlap_issues=combined_overlap_result.overlaps
            if combined_overlap_result
            else [],
            duration=duration,
            sheet_results=results,
            errors=[e["error"] for e in errors],
            sheet_pass_results=sheet_pass_results,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    import argparse

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )

    parser = argparse.ArgumentParser(
        description="CINS Pipeline — extract commission data from emails, PDFs, and Excel files"
    )
    parser.add_argument("file", help="Path to input file (.eml, .pdf, .xlsx)")
    parser.add_argument(
        "--provider",
        default="gemini",
        choices=["gemini", "openai"],
        help="LLM provider (default: gemini)",
    )
    parser.add_argument(
        "--model", default=None, help="Model name (auto-selected if omitted)"
    )
    parser.add_argument(
        "--output-dir", default="outputs", help="Output directory (default: outputs/)"
    )
    parser.add_argument("--indexed-dir", default=None, help="Index cache directory")
    parser.add_argument(
        "--force-reindex",
        action="store_true",
        help="Re-extract even if index cache exists",
    )
    parser.add_argument(
        "--excel-mode",
        default="sheet_by_sheet",
        choices=["sheet_by_sheet", "all_at_once"],
    )
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
