"""
Content Indexer Module
Stage 1: Extract and cache document content for reuse across multiple LLM extraction runs
"""

import os
import json
import hashlib
import logging
from pathlib import Path
from typing import Dict, Optional, Iterator, Tuple, List, Any
from dataclasses import dataclass, field
from datetime import datetime

from cins.processors.extractors.extract_eml import extract_eml_content
from cins.processors.extractors.extract_pdf import extract_pdf_text
from cins.processors.extractors.excel_to_csv import ExcelToCSVConverter
from cins.processors.extractors.convert_xlsb import (
    convert_xlsb_to_xlsx,
    XlsbConversionError,
)
from cins.processors.content.csv_to_markdown import csv_to_markdown
from cins.processors.content.content_cleaning import (
    clean_email_content,
    clean_master_data_content,
    detect_csv_tables,
)

# Setup logging
logger = logging.getLogger("content_indexer")

# Current indexer version for cache invalidation
INDEXER_VERSION = "1.3"


def _retag_single_commission_table(content_str: str, sheet_name: str) -> str:
    """Rewrite a single-table commission sheet's leading `## Sheet: ...` header
    to the typed `## Table 1: ... [type: commission]` form that the multi-table
    branch produces.

    Pass 3 codegen filters tables on `ttype == "commission"`. Without this
    retag, single-table commission sheets are tagged "unknown" by
    `parse_md_tables` and the generated extractor skips every row.
    """
    lines = content_str.splitlines()
    typed_header = f"## Table 1: {sheet_name} [type: commission]"
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            if "[type:" in stripped:
                return content_str
            lines[i] = typed_header
            return "\n".join(lines)
        return typed_header + "\n\n" + content_str
    return typed_header + "\n"


@dataclass
class IndexedDocument:
    """
    Represents a fully indexed document with cached content.

    Provides lazy-loading access to extracted content without re-extracting from source.
    """

    document_id: str
    provider: str
    source_file: Path
    source_type: str  # 'email', 'pdf', 'excel'
    indexed_dir: Path
    metadata: dict

    # Lazy-loaded content cache
    _primary_content: Optional[str] = field(default=None, repr=False)
    _sheet_contents: Optional[Dict[str, str]] = field(default=None, repr=False)
    _email_context: Optional[str] = field(default=None, repr=False)

    @property
    def is_excel(self) -> bool:
        """Check if this is an Excel document"""
        return self.source_type == "excel"

    @property
    def is_email(self) -> bool:
        """Check if this is an email document"""
        return self.source_type == "email"

    @property
    def has_sheets(self) -> bool:
        """Check if this document has multiple sheets"""
        return "sheets" in self.metadata.get("content_files", {})

    def get_primary_content(self) -> str:
        """Load primary content (cached after first load)"""
        if self._primary_content is None:
            primary_file = self.indexed_dir / self.metadata["content_files"]["primary"]
            self._primary_content = primary_file.read_text(encoding="utf-8")
        return self._primary_content

    def _sanitize_for_lookup(self, name: str) -> str:
        """Convert original name to sanitized form for table_splits lookup."""
        import re

        safe_name = re.sub(r"[^\w\-_]", "_", str(name))
        return safe_name[:100]

    def get_table_splits(self) -> Dict[str, Any]:
        """Get table split information from metadata. Returns {} for old documents."""
        return self.metadata.get("table_splits", {})

    def get_sheet_content(self, sheet_name: str) -> str:
        """Load specific sheet content.

        For sheets split into multiple tables (split_applied: True), returns the
        single .md file containing all tables concatenated with '## Table N: name'
        headers so the downstream extraction LLM receives all tables in one prompt.
        For unsplit sheets, returns the single CSV file.
        """
        table_splits = self.get_table_splits()
        safe_name = self._sanitize_for_lookup(sheet_name)

        split_info = table_splits.get(safe_name, {})
        if split_info.get("split_applied"):
            md_file = self.indexed_dir / "sheets" / f"{safe_name}.md"
            if not md_file.exists():
                raise FileNotFoundError(
                    f"Split sheet content not found: {safe_name}.md"
                )
            return md_file.read_text(encoding="utf-8")

        # Unsplit sheet — new behavior
        sheet_file = self.indexed_dir / "sheets" / f"{safe_name}.md"
        if not sheet_file.exists():
            # Fallback for older indexed documents
            sheet_file = self.indexed_dir / "sheets" / f"{safe_name}.csv"
            if not sheet_file.exists():
                raise FileNotFoundError(f"Sheet content not found: {sheet_name}")
        return sheet_file.read_text(encoding="utf-8")

    def get_all_sheet_contents(self) -> Dict[str, str]:
        """Load all sheet contents (cached after first load)

        Returns:
            Dict mapping original sheet names -> content.
            For sheets split into multiple tables, the content is the concatenation
            of all table files (with ## Table N headers) so downstream extraction
            receives all tables in one prompt per sheet.
        """
        if self._sheet_contents is None:
            self._sheet_contents = {}
            original_names = self.metadata.get("source_metadata", {}).get(
                "sheet_names", []
            )

            if original_names:
                # Use original names — get_sheet_content handles split lookup internally
                for original in original_names:
                    try:
                        self._sheet_contents[original] = self.get_sheet_content(
                            original
                        )
                    except FileNotFoundError:
                        pass  # Sheet may have failed indexing; skip silently
            else:
                # Fallback for old documents or no source_metadata: use sanitized names
                sanitized_names = self.metadata["content_files"].get("sheets", [])
                for sanitized in sanitized_names:
                    try:
                        self._sheet_contents[sanitized] = self.get_sheet_content(
                            sanitized
                        )
                    except FileNotFoundError:
                        pass
        return self._sheet_contents

    def get_email_context(self) -> Optional[str]:
        """Load email context for prepending (Email only)"""
        if not self.is_email:
            return None
        if self._email_context is None:
            context_file = self.indexed_dir / "email_context.md"
            if context_file.exists():
                self._email_context = context_file.read_text(encoding="utf-8")
        return self._email_context

    def iter_processable_units(self) -> Iterator[Tuple[str, str]]:
        """
        Iterate over all processable units (sheets or primary content).

        Yields:
            (unit_name, content) tuples

        For Excel: yields (sheet_name, sheet_content) per sheet
        For others: yields (document_id, primary_content)
        """
        if self.has_sheets:
            # Process sheet by sheet (content already contains only md_content)
            for sheet_name, content in self.get_all_sheet_contents().items():
                yield (sheet_name, content)
        else:
            # Process as single unit
            yield (self.document_id, self.get_primary_content())

    def get_sheet_classifications(self) -> Dict[str, Dict[str, Any]]:
        """Get sheet classifications from metadata.

        Returns:
            Dict mapping sheet_name -> {sheet_type, confidence}
            Returns empty dict if no classifications available.
        """
        return self.metadata.get("sheet_classifications", {})

    def get_commission_sheets(self) -> List[str]:
        """Get list of sheet names classified as commission_data.

        Returns:
            List of sheet names (empty if no classifications or no commission sheets)
        """
        classifications = self.get_sheet_classifications()
        return [
            sheet_name
            for sheet_name, info in classifications.items()
            if info.get("sheet_type") == "commission_data"
        ]

    def get_master_sheets(self) -> List[str]:
        """Get list of sheet names classified as master_data.

        Returns:
            List of sheet names (empty if no classifications or no master sheets)
        """
        classifications = self.get_sheet_classifications()
        return [
            sheet_name
            for sheet_name, info in classifications.items()
            if info.get("sheet_type") == "master_data"
        ]


def _convert_excel_to_csv(excel_path: str, output_dir: Path, sheet_name: Optional[str] = None) -> Dict:
    """
    Convert Excel file (or specific sheet) to CSV using ExcelToCSVConverter.

    Returns dict with: sheets, master_sheets, commission_sheets, sheet_contents (backward compat).
    """
    converter = ExcelToCSVConverter(output_dir=output_dir, fill_empty='', enable_classification=True)

    sheet_contents = {}
    all_sheet_results = []
    master_sheets = []
    commission_sheets = []

    if sheet_name:
        result = converter.process_sheet(excel_path, sheet_name, output_dir)
        csv_path = Path(result['csv_path'])
        csv_content = csv_path.read_text(encoding='utf-8')
        metadata_path = Path(result['metadata_path'])
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        formatted_content = _format_csv_with_metadata(csv_content, metadata)
        sheet_contents[sheet_name] = formatted_content
        classification = result.get('classification', {})
        result['formatted_content'] = formatted_content
        all_sheet_results.append(result)
        sheet_type = classification.get('sheet_type', 'master_data')
        if sheet_type == 'commission_data':
            commission_sheets.append(result)
        else:
            master_sheets.append(result)
    else:
        results = converter.process_workbook(excel_path, output_dir)
        for sheet_result in results['sheets']:
            sn = sheet_result['sheet_name']
            csv_path = Path(sheet_result['csv_path'])
            csv_content = csv_path.read_text(encoding='utf-8')
            metadata_path = Path(sheet_result['metadata_path'])
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            formatted_content = _format_csv_with_metadata(csv_content, metadata)
            sheet_contents[sn] = formatted_content
            classification = sheet_result.get('classification', {})
            sheet_result['formatted_content'] = formatted_content
            all_sheet_results.append(sheet_result)
            sheet_type = classification.get('sheet_type', 'master_data')
            if sheet_type == 'commission_data':
                commission_sheets.append(sheet_result)
            else:
                master_sheets.append(sheet_result)

    return {
        "sheets": all_sheet_results,
        "master_sheets": master_sheets,
        "commission_sheets": commission_sheets,
        "sheet_contents": sheet_contents,
    }


def _format_csv_with_metadata(csv_content: str, metadata: Dict) -> str:
    """Format CSV content with metadata context for LLM consumption."""
    header_row_count = len(metadata.get('formatting', {}).get('detected_header_rows', []))
    markdown_table = csv_to_markdown(csv_content, header_row_count=header_row_count)
    sheet_name = metadata.get('sheet_name', 'Unknown')
    dimensions = metadata.get('dimensions', {})
    rows = dimensions.get('rows', 0)
    cols = dimensions.get('cols', 0)
    formatted_content = f"## Sheet: {sheet_name}\n"
    formatted_content += f"**Dimensions**: {rows} rows × {cols} columns\n\n"
    formatted_content += markdown_table
    return formatted_content


class ContentIndexer:
    """
    Stage 1: Extract and cache document content for reuse.

    Extracts content from emails, PDFs, and Excel files once, then stores
    in input-indexed/ for fast access during multiple LLM extraction runs.
    """

    def __init__(self, indexed_dir: Path, enable_cleaning: bool = True, llm_provider: str = None):
        """
        Initialize content indexer.

        Args:
            indexed_dir: Root directory for indexed content (e.g., input-indexed/)
            enable_cleaning: Enable Pass 0A/0B cleaning during indexing (default: True)
            llm_provider: LLM provider override ("gemini" or "openai"). Falls back to LLM_PROVIDER env var.
        """
        self.indexed_dir = Path(indexed_dir)
        self.indexed_dir.mkdir(parents=True, exist_ok=True)
        self.enable_cleaning = enable_cleaning
        self.llm_provider = llm_provider
        self._llm_service = None  # Lazy-loaded when cleaning is needed
        logger.info(
            f"ContentIndexer initialized with directory: {self.indexed_dir}, cleaning={enable_cleaning}"
        )

    def _get_llm_service(self):
        """Get or create LLM service instance for cleaning."""
        if self._llm_service is None:
            try:
                from cins.processors.llm_services.factory import get_llm_service

                self._llm_service = get_llm_service(provider=self.llm_provider)
                logger.info("LLM service initialized for content cleaning")
            except Exception as e:
                logger.warning(f"Could not initialize LLM service for cleaning: {e}")
                self._llm_service = None
        return self._llm_service

    def index_document(
        self,
        file_path: str,
        provider: str,
        excel_mode: str = "sheet_by_sheet",
        force_reindex: bool = False,
        progress_callback=None,
    ) -> IndexedDocument:
        """
        Index a document and save prepared content.

        Args:
            file_path: Path to source file
            provider: Provider name (e.g., 'Bajaj')
            excel_mode: Excel processing mode ('sheet_by_sheet' or 'all_at_once')
            force_reindex: Force re-extraction even if cache exists
            progress_callback: Optional callable(step, detail) invoked at each stage

        Returns:
            IndexedDocument with metadata and content paths
        """
        def _progress(step: str, detail: str = ""):
            if progress_callback:
                try:
                    progress_callback(step, detail)
                except Exception:
                    pass

        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            raise FileNotFoundError(f"Source file not found: {file_path}")

        # Generate document ID from filename
        document_id = self._sanitize_filename(file_path_obj.stem)

        # Check if already indexed
        if not force_reindex:
            existing_doc = self.get_indexed_document(document_id, provider)
            if existing_doc and self.is_cache_valid(existing_doc, file_path_obj):
                logger.info(f"Using cached index for: {document_id}")
                return existing_doc

        logger.info(f"Indexing document: {document_id} (provider: {provider})")

        # Create output directory
        output_dir = self.indexed_dir / provider / document_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # Detect file type
        file_ext = file_path_obj.suffix.lower()

        # Track indexing status
        indexing_status = "success"
        indexing_error = None

        try:
            # Index based on file type
            if file_ext == ".eml":
                metadata = self._index_email(file_path, output_dir, _progress)
            elif file_ext == ".pdf":
                metadata = self._index_pdf(file_path, output_dir, _progress)
            elif file_ext in [".xlsx", ".xls", ".xlsm"]:
                metadata = self._index_excel(file_path, output_dir, excel_mode, progress=_progress)
            elif file_ext == ".xlsb":
                # openpyxl can't read .xlsb; convert via LibreOffice first.
                converted_path = None
                try:
                    converted_path = convert_xlsb_to_xlsx(file_path)
                    metadata = self._index_excel(
                        converted_path, output_dir, excel_mode, progress=_progress,
                    )
                    metadata.setdefault("source_metadata", {})["converted_from"] = "xlsb"
                    metadata["source_metadata"]["original_filename"] = file_path_obj.name
                except XlsbConversionError as e:
                    raise ValueError(f"Failed to convert .xlsb file: {e}") from e
                finally:
                    if converted_path:
                        try:
                            os.unlink(converted_path)
                        except Exception:
                            pass
            else:
                raise ValueError(f"Unsupported file type: {file_ext}")

            # Check if there were partial failures (e.g., some sheets failed)
            if "indexing_errors" in metadata and len(metadata["indexing_errors"]) > 0:
                # For emails with Excel attachments, check if ANY content was extracted
                if metadata["source_type"] == "email":
                    if metadata.get("content_files", {}).get("sheets"):
                        indexing_status = "partial"
                    else:
                        if metadata.get("source_metadata", {}).get(
                            "excel_attachment_error"
                        ):
                            indexing_status = "failed"
                        else:
                            indexing_status = "partial"
                else:
                    indexing_status = "partial"

        except Exception as e:
            indexing_status = "failed"
            indexing_error = str(e)
            logger.error(f"Failed to index {document_id}: {indexing_error}")

            metadata = {
                "source_type": "unknown",
                "content_files": {},
                "source_metadata": {},
                "indexing_error": indexing_error,
            }

        # Add common metadata
        metadata.update(
            {
                "document_id": document_id,
                "source_file": str(file_path_obj.resolve()),
                "provider": provider,
                "indexed_at": datetime.now().isoformat(),
                "indexer_version": INDEXER_VERSION,
                "indexing_status": indexing_status,
                "hash": self._calculate_content_hash(file_path_obj),
            }
        )

        # Save metadata (even if indexing failed, so we can track the failure)
        self._save_metadata(metadata, output_dir)

        if indexing_status == "success":
            logger.info(f"Successfully indexed: {document_id}")
        elif indexing_status == "partial":
            logger.warning(f"Partially indexed: {document_id} (some sheets failed)")
        else:
            logger.error(f"Failed to index: {document_id}")

        return IndexedDocument(
            document_id=document_id,
            provider=provider,
            source_file=file_path_obj,
            source_type=metadata["source_type"],
            indexed_dir=output_dir,
            metadata=metadata,
        )

    def get_indexed_document(
        self, document_id: str, provider: str
    ) -> Optional[IndexedDocument]:
        """
        Load existing indexed document.

        Args:
            document_id: Document identifier
            provider: Provider name

        Returns:
            IndexedDocument if found, None otherwise
        """
        metadata_path = self.indexed_dir / provider / document_id / "metadata.json"

        if not metadata_path.exists():
            return None

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

            # Backward compatibility: add indexing_status if missing
            if "indexing_status" not in metadata:
                if "indexing_error" in metadata:
                    metadata["indexing_status"] = "failed"
                elif (
                    "indexing_errors" in metadata
                    and len(metadata.get("indexing_errors", [])) > 0
                ):
                    metadata["indexing_status"] = "partial"
                else:
                    metadata["indexing_status"] = "success"

            return IndexedDocument(
                document_id=document_id,
                provider=provider,
                source_file=Path(metadata["source_file"]),
                source_type=metadata["source_type"],
                indexed_dir=metadata_path.parent,
                metadata=metadata,
            )
        except Exception as e:
            logger.error(f"Error loading indexed document {document_id}: {e}")
            return None

    def is_indexed(self, file_path: str, provider: str) -> bool:
        """
        Check if document is already indexed.

        Args:
            file_path: Path to source file
            provider: Provider name

        Returns:
            True if indexed, False otherwise
        """
        document_id = self._sanitize_filename(Path(file_path).stem)
        return (self.indexed_dir / provider / document_id / "metadata.json").exists()

    def is_cache_valid(self, indexed_doc: IndexedDocument, source_file: Path) -> bool:
        """
        Check if cached content is still valid.

        Args:
            indexed_doc: Indexed document to validate
            source_file: Path to source file

        Returns:
            True if cache is valid, False if re-indexing needed
        """
        if not source_file.exists():
            logger.warning(f"Source file missing: {source_file}")
            return False

        current_hash = self._calculate_content_hash(source_file)
        if current_hash != indexed_doc.metadata.get("hash"):
            logger.info(f"Source file modified: {source_file.name}")
            return False

        if indexed_doc.metadata.get("indexer_version") != INDEXER_VERSION:
            logger.info("Indexer version changed, re-indexing needed")
            return False

        return True

    def _index_email(self, file_path: str, output_dir: Path, progress=None) -> dict:
        """
        Index email file (.eml).

        Extracts email metadata, body, and attachments.
        For Excel attachments, routes to _index_excel() with email context.
        """
        def _p(step, detail=""):
            if progress:
                progress(step, detail)

        logger.info(f"Indexing email: {file_path}")

        _p("extracting_content", "Parsing email")
        email_data = extract_eml_content(file_path, output_dir=str(output_dir))

        body = self._clean_email_body(email_data.get("body", ""))

        header_lines = []
        if email_data.get("subject"):
            header_lines.append(f"Subject: {email_data['subject']}")
        if email_data.get("from"):
            header_lines.append(f"From: {email_data['from']}")
        if email_data.get("to"):
            header_lines.append(f"To: {email_data['to']}")
        if email_data.get("cc"):
            header_lines.append(f"Cc: {email_data['cc']}")
        if email_data.get("date"):
            header_lines.append(f"Date: {email_data['date']}")

        cleaning_metadata = {}
        if self.enable_cleaning:
            service = self._get_llm_service()
            if service:
                logger.info("Applying Pass 0A (Email Cleaning)...")
                _p("cleaning", "Pass 0A: cleaning email body")
                try:
                    cleaned_body, email_cleaning_meta = clean_email_content(
                        body, service
                    )
                    if header_lines:
                        body = "\n".join(header_lines) + "\n\n" + cleaned_body
                    else:
                        body = cleaned_body
                    cleaning_metadata["pass0a_email_cleaning"] = email_cleaning_meta
                    logger.info(
                        f"Pass 0A complete: {len(email_cleaning_meta.get('removed_sections', []))} sections removed"
                    )
                except Exception as e:
                    logger.warning(f"Pass 0A failed, using uncleaned content: {e}")
                    cleaning_metadata["pass0a_email_cleaning"] = {
                        "error": str(e),
                        "fallback": True,
                    }
                    if header_lines:
                        body = "\n".join(header_lines) + "\n\n" + body
        else:
            if header_lines:
                body = "\n".join(header_lines) + "\n\n" + body

        content_file = output_dir / "content.md"
        content_file.write_text(body, encoding="utf-8")

        email_context = self._format_email_context(email_data)
        context_file = output_dir / "email_context.md"
        context_file.write_text(email_context, encoding="utf-8")

        excel_attachments = [
            att for att in email_data.get("attachments", []) if "temp_path" in att
        ]

        metadata = {
            "source_type": "email",
            "content_files": {
                "primary": "content.md",
                "email_context": "email_context.md",
            },
            "source_metadata": {
                "subject": email_data.get("subject", ""),
                "from": email_data.get("from", ""),
                "date": email_data.get("date", ""),
                "attachment_count": len(email_data.get("attachments", [])),
            },
        }

        if cleaning_metadata:
            metadata["cleaning_metadata"] = cleaning_metadata

        if excel_attachments:
            logger.info(f"Found {len(excel_attachments)} Excel attachment(s)")

            import shutil

            all_sheet_files = []
            all_sheet_names = []
            all_sheet_classifications = {}
            all_failed_sheets = []
            all_indexing_errors = []
            excel_attachment_names = []

            use_prefix = len(excel_attachments) > 1

            attachments_dir = output_dir / "attachments"
            attachments_dir.mkdir(exist_ok=True)

            for att_idx, att in enumerate(excel_attachments, 1):
                temp_path = att["temp_path"]
                att_filename = att["filename"]
                _p(
                    "processing_attachment",
                    f"Processing attachment {att_idx}/{len(excel_attachments)}: {att_filename}",
                )

                prefix = (
                    self._sanitize_filename(Path(att_filename).stem) + "__"
                    if use_prefix
                    else ""
                )

                try:
                    excel_metadata = self._index_excel(
                        temp_path, output_dir, mode="sheet_by_sheet",
                        sheet_name_prefix=prefix, progress=progress,
                    )

                    all_sheet_files.extend(
                        excel_metadata["content_files"].get("sheets", [])
                    )
                    all_sheet_names.extend(
                        excel_metadata["source_metadata"].get("sheet_names", [])
                    )
                    all_failed_sheets.extend(
                        excel_metadata["source_metadata"].get("failed_sheets", [])
                    )
                    if "sheet_classifications" in excel_metadata:
                        all_sheet_classifications.update(
                            excel_metadata["sheet_classifications"]
                        )
                    if "indexing_errors" in excel_metadata:
                        all_indexing_errors.extend(excel_metadata["indexing_errors"])

                    excel_attachment_names.append(att_filename)

                    saved_att_path = attachments_dir / att_filename
                    shutil.copy2(temp_path, saved_att_path)
                    logger.info(f"Saved Excel attachment to: {saved_att_path}")

                except Exception as e:
                    error_msg = str(e)
                    logger.error(
                        f"Failed to index Excel attachment '{att_filename}': {error_msg}"
                    )
                    all_indexing_errors.append(
                        {"attachment": att_filename, "error": error_msg}
                    )

                finally:
                    try:
                        os.unlink(temp_path)
                    except Exception as e:
                        logger.warning(f"Could not delete temp file: {e}")

            if all_sheet_files:
                metadata["content_files"]["sheets"] = all_sheet_files
                metadata["source_metadata"]["excel_attachments"] = excel_attachment_names
                metadata["source_metadata"]["excel_attachment"] = excel_attachment_names[0] if excel_attachment_names else None
                metadata["source_metadata"]["sheet_names"] = all_sheet_names
                metadata["source_metadata"]["successful_sheets"] = len(all_sheet_files)
                metadata["source_metadata"]["failed_sheets"] = all_failed_sheets

                if all_sheet_classifications:
                    metadata["sheet_classifications"] = all_sheet_classifications

            if all_indexing_errors:
                metadata["indexing_errors"] = all_indexing_errors

        # --- Inline-image commission grids ---------------------------------
        # Some senders attach the actual commission table as a screenshot
        # (PNG/JPG inside the email body). Convert qualifying images to
        # markdown via vision-LLM so they flow through the per-sheet pass
        # loop the same way Excel sheets do.
        self._extract_image_table_sheets(
            email_data=email_data,
            cleaned_body=body,
            output_dir=output_dir,
            metadata=metadata,
            progress=_p,
        )

        return metadata

    def _extract_image_table_sheets(
        self,
        email_data: Dict,
        cleaned_body: str,
        output_dir: Path,
        metadata: Dict,
        progress,
    ) -> None:
        """
        For each image attachment that (a) is referenced from the latest
        message body via a `cid:` token and (b) passes the table-likelihood
        size heuristic, run vision-LLM to extract a markdown table and inject
        it as a pseudo-sheet into the indexer's metadata.

        Mutates `metadata` in place. Side-effect free for non-image emails.
        """
        from cins.processors.extractors.extract_eml import extract_cid_refs_from_body
        from cins.processors.extractors.image_to_markdown import extract_table_markdown
        try:
            from cins.processors.extractors.sheet_classifier import SheetClassifier
        except ImportError:
            SheetClassifier = None  # type: ignore

        image_attachments = [
            att for att in email_data.get("attachments", [])
            if att.get("image_path")
        ]
        if not image_attachments:
            return

        # Step 1: cid whitelist from the *latest* message body. The cleaned
        # text body already had its quoted/forwarded trail stripped (and our
        # `[cid:...]` sweep removes stale tokens), so we collect cids from the
        # raw HTML parts instead — but trim each HTML to its pre-quoted-block
        # prefix first, otherwise the whitelist would include images that only
        # appear in older messages further down the thread.
        cid_whitelist = set()
        for raw_html_part in email_data.get("raw_html_parts", []) or []:
            latest_html = self._strip_quoted_trail_html(raw_html_part)
            cid_whitelist |= extract_cid_refs_from_body(latest_html)

        # Step 2: filter to images that (a) live in the latest message and
        # (b) look like grids. Older quoted images and tiny logos are dropped
        # before any LLM call.
        surviving = [
            att for att in image_attachments
            if att.get("is_likely_table")
            and att.get("content_id")
            and att["content_id"] in cid_whitelist
        ]

        logger.info(
            "Image attachment routing: %d total, %d in latest body, %d look like tables",
            len(image_attachments),
            sum(1 for a in image_attachments if a.get("content_id") in cid_whitelist),
            len(surviving),
        )

        if not surviving:
            return

        llm_service = self._get_llm_service()
        if not llm_service:
            logger.warning(
                "Image attachments present but no LLM service available; skipping vision extraction"
            )
            return

        sheets_dir = output_dir / "sheets"
        sheets_dir.mkdir(exist_ok=True)

        existing_sheet_files = list(
            metadata.get("content_files", {}).get("sheets", []) or []
        )
        existing_sheet_names = list(
            metadata.get("source_metadata", {}).get("sheet_names", []) or []
        )
        existing_classifications = dict(metadata.get("sheet_classifications", {}) or {})

        classifier = SheetClassifier() if SheetClassifier else None

        added_any = False
        for att in surviving:
            filename = att.get("filename", "image")
            stem = Path(filename).stem or "image"
            sheet_key = f"image::{stem}"
            # IndexedDocument.get_sheet_content() looks up by passing the
            # sheet name through `re.sub(r"[^\w\-_]", "_", name)`, which turns
            # "image::imageNNN" into "image__imageNNN". Use the same sanitiser
            # here so the file we write matches what the loader reads.
            safe_name = self._sanitize_filename(sheet_key)

            progress("extracting_image_table", f"Vision: {filename}")
            md = extract_table_markdown(Path(att["image_path"]), llm_service)
            if not md or not md.strip():
                logger.info("Vision returned empty markdown for %s; skipping", filename)
                continue

            md_path = sheets_dir / f"{safe_name}.md"
            md_path.write_text(md, encoding="utf-8")

            existing_sheet_files.append(safe_name)
            existing_sheet_names.append(sheet_key)
            added_any = True

            if classifier:
                try:
                    classification = classifier.classify_sheet(
                        sheet_name=sheet_key,
                        csv_preview=md[:4000],
                        metadata={"source": "image", "filename": filename},
                    )
                    existing_classifications[sheet_key] = classification.to_dict()
                except Exception as exc:
                    logger.warning(
                        "Sheet classification failed for image '%s': %s", sheet_key, exc
                    )

        if not added_any:
            return

        metadata.setdefault("content_files", {})["sheets"] = existing_sheet_files
        metadata.setdefault("source_metadata", {})["sheet_names"] = existing_sheet_names
        if existing_classifications:
            metadata["sheet_classifications"] = existing_classifications

    def _index_pdf(self, file_path: str, output_dir: Path, progress=None) -> dict:
        """Index PDF file."""
        logger.info(f"Indexing PDF: {file_path}")

        if progress:
            progress("extracting_content", "Extracting PDF text")
        content_str = extract_pdf_text(file_path)

        content_file = output_dir / "content.md"
        content_file.write_text(content_str, encoding="utf-8")

        metadata = {
            "source_type": "pdf",
            "content_files": {"primary": "content.md"},
            "source_metadata": {"content_length": len(content_str)},
        }

        return metadata

    def _index_excel(
        self, file_path: str, output_dir: Path, mode: str = "sheet_by_sheet",
        sheet_name_prefix: str = "", progress=None,
    ) -> dict:
        """
        Index Excel file sheet-by-sheet.

        Converts each sheet to CSV, formats with metadata, and saves to sheets/ directory.

        Args:
            sheet_name_prefix: Optional prefix for sheet names/files to avoid collisions
                when multiple Excel attachments share the same sheet names.
            progress: Optional callable(step, detail) for progress reporting.
        """
        def _p(step, detail=""):
            if progress:
                progress(step, detail)

        logger.info(f"Indexing Excel: {file_path} (mode: {mode})")

        import openpyxl

        wb = openpyxl.load_workbook(file_path, data_only=True)
        sheet_names = wb.sheetnames
        wb.close()

        logger.info(f"Found {len(sheet_names)} sheets: {sheet_names}")
        _p("converting_sheets", f"Converting {len(sheet_names)} sheet(s) to CSV")

        sheets_dir = output_dir / "sheets"
        sheets_dir.mkdir(exist_ok=True)

        csv_output_dir = output_dir / "csv_converted"
        csv_output_dir.mkdir(exist_ok=True)

        try:
            csv_data = _convert_excel_to_csv(file_path, csv_output_dir)
            sheet_contents = csv_data["sheet_contents"]

            sheet_classifications = {}
            sheet_csv_paths = {}
            sheet_header_row_counts = {}
            for sheet_result in csv_data.get("sheets", []):
                sheet_name = sheet_result.get("sheet_name")
                classification = sheet_result.get("classification")
                prefixed_name = f"{sheet_name_prefix}{sheet_name}" if sheet_name_prefix and sheet_name else sheet_name

                if sheet_name and classification:
                    sheet_classifications[prefixed_name] = {
                        "sheet_type": classification.get("sheet_type"),
                        "confidence": classification.get("confidence"),
                    }

                if sheet_name and sheet_result.get("csv_path"):
                    sheet_csv_paths[sheet_name] = sheet_result["csv_path"]

                if sheet_name and sheet_result.get("metadata_path"):
                    try:
                        meta_json = json.loads(Path(sheet_result["metadata_path"]).read_text(encoding="utf-8"))
                        sheet_header_row_counts[sheet_name] = len(
                            meta_json.get("formatting", {}).get("detected_header_rows", [])
                        )
                    except Exception:
                        sheet_header_row_counts[sheet_name] = 0
        except Exception as e:
            logger.error(f"Failed to convert Excel to CSV: {e}")
            raise

        sheet_files = []
        failed_sheets = []
        table_splits_metadata = {}

        for sheet_idx, sheet_name in enumerate(sheet_names, 1):
            try:
                logger.info(
                    f"  Processing sheet {sheet_idx}/{len(sheet_names)}: {sheet_name}"
                )
                _p(
                    "processing_sheet",
                    f"Sheet {sheet_idx}/{len(sheet_names)}: {sheet_name}",
                )

                safe_name = self._sanitize_filename(
                    f"{sheet_name_prefix}{sheet_name}" if sheet_name_prefix else sheet_name
                )

                if sheet_name not in sheet_contents:
                    raise ValueError(
                        f"Sheet '{sheet_name}' not found in CSV conversion results"
                    )

                content_str = sheet_contents[sheet_name]

                sheet_type = None
                prefixed_name = f"{sheet_name_prefix}{sheet_name}" if sheet_name_prefix else sheet_name
                if sheet_classifications and prefixed_name in sheet_classifications:
                    sheet_type = sheet_classifications[prefixed_name].get("sheet_type")
                is_master_data_sheet = sheet_type == "master_data"
                is_commission_data_sheet = sheet_type == "commission_data"

                if self.enable_cleaning and is_master_data_sheet:
                    service = self._get_llm_service()
                    if service:
                        logger.info(
                            f"  Applying Pass 0B to master data sheet '{sheet_name}'..."
                        )
                        _p("cleaning_sheet", f"Pass 0B: cleaning master data sheet '{sheet_name}'")
                        try:
                            cleaned_content, md_cleaning_meta = (
                                clean_master_data_content(content_str, service)
                            )
                            content_str = cleaned_content
                            logger.info(
                                f"  Pass 0B complete: {md_cleaning_meta.get('master_data_table_count', 0)} tables found"
                            )
                        except Exception as e:
                            logger.warning(
                                f"  Pass 0B failed for sheet '{sheet_name}', using uncleaned content: {e}"
                            )
                elif is_master_data_sheet:
                    logger.info(
                        f"  Sheet '{sheet_name}' is master data but cleaning is disabled"
                    )

                table_split_info = None
                if self.enable_cleaning and is_commission_data_sheet:
                    service = self._get_llm_service()
                    if service:
                        logger.info(
                            f"  Applying Pass 0C (Table Detection) to commission sheet '{sheet_name}'..."
                        )
                        _p("detecting_tables", f"Pass 0C: detecting tables in commission sheet '{sheet_name}'")
                        try:
                            raw_csv_path = sheet_csv_paths.get(sheet_name)
                            raw_csv_content = (
                                Path(raw_csv_path).read_text(encoding="utf-8")
                                if raw_csv_path
                                else content_str
                            )
                            tables_list, detection_meta = detect_csv_tables(
                                raw_csv_content, sheet_name, service
                            )

                            if detection_meta.get("fallback") or not tables_list:
                                logger.warning(
                                    f"  Pass 0C returned no tables for '{sheet_name}', writing full sheet"
                                )
                                md_path = sheets_dir / f"{safe_name}.md"
                                md_path.write_text(
                                    _retag_single_commission_table(content_str, sheet_name),
                                    encoding="utf-8",
                                )
                                sheet_files.append(safe_name)
                                table_split_info = {
                                    "split_applied": False,
                                    "original_sheet_name": sheet_name,
                                    "table_count": 1,
                                    "tables": [],
                                    "detection_notes": detection_meta.get(
                                        "error", "No tables detected"
                                    ),
                                    "requires_review": False,
                                }

                            elif not detection_meta.get(
                                "sheet_has_multiple_tables", False
                            ):
                                md_path = sheets_dir / f"{safe_name}.md"
                                md_path.write_text(
                                    _retag_single_commission_table(content_str, sheet_name),
                                    encoding="utf-8",
                                )
                                sheet_files.append(safe_name)
                                logger.info(
                                    f"  Pass 0C: single table in '{sheet_name}', no split needed"
                                )
                                table_split_info = {
                                    "split_applied": False,
                                    "original_sheet_name": sheet_name,
                                    "table_count": 1,
                                    "tables": [],
                                    "detection_notes": detection_meta.get(
                                        "detection_notes", ""
                                    ),
                                    "requires_review": False,
                                }

                            else:
                                split_tables_meta = []
                                requires_review = False
                                md_parts = []

                                for table_info in tables_list:
                                    table_type = table_info.get("table_type", "commission")
                                    if table_type == "notes":
                                        import csv as _csv, io as _io
                                        note_lines = []
                                        for row in _csv.reader(_io.StringIO(table_info["table_csv_content"])):
                                            seen = []
                                            for c in row:
                                                val = c.strip()
                                                if val and (not seen or val != seen[-1]):
                                                    seen.append(val)
                                            if seen:
                                                note_lines.append(" ".join(seen))
                                        table_md = "\n".join(note_lines) if note_lines else "[Empty notes section]"
                                    else:
                                        hrc = sheet_header_row_counts.get(sheet_name, 0)
                                        table_md = csv_to_markdown(table_info["table_csv_content"], header_row_count=hrc)
                                    md_parts.append(
                                        f"## Table {table_info['table_index']}: {table_info['table_name']} [type: {table_type}]\n\n{table_md}"
                                    )

                                    if table_info.get("confidence", 1.0) < 0.7:
                                        requires_review = True

                                    split_tables_meta.append(
                                        {
                                            "table_index": table_info["table_index"],
                                            "table_name": table_info["table_name"],
                                            "table_type": table_info.get(
                                                "table_type", "commission"
                                            ),
                                            "description": table_info.get(
                                                "description", ""
                                            ),
                                            "confidence": table_info.get(
                                                "confidence", 1.0
                                            ),
                                            "detection_notes": table_info.get(
                                                "detection_notes", ""
                                            ),
                                            "start_row": table_info.get("start_row"),
                                            "end_row": table_info.get("end_row"),
                                        }
                                    )

                                md_content = "\n\n".join(md_parts)
                                md_path = sheets_dir / f"{safe_name}.md"
                                md_path.write_text(md_content, encoding="utf-8")

                                sheet_files.append(safe_name)

                                table_split_info = {
                                    "split_applied": True,
                                    "original_sheet_name": sheet_name,
                                    "table_count": len(tables_list),
                                    "tables": split_tables_meta,
                                    "detection_notes": detection_meta.get(
                                        "detection_notes", ""
                                    ),
                                    "requires_review": requires_review,
                                }
                                logger.info(
                                    f"  ✓ Pass 0C: split '{sheet_name}' into {len(tables_list)} tables saved to one MD file"
                                )

                        except Exception as e:
                            logger.warning(
                                f"  Pass 0C failed for '{sheet_name}', writing full sheet: {e}"
                            )
                            md_path = sheets_dir / f"{safe_name}.md"
                            md_path.write_text(content_str, encoding="utf-8")
                            sheet_files.append(safe_name)
                            table_split_info = {
                                "split_applied": False,
                                "original_sheet_name": sheet_name,
                                "table_count": 1,
                                "tables": [],
                                "detection_notes": f"Pass 0C failed: {str(e)}",
                                "requires_review": False,
                            }
                    else:
                        md_path = sheets_dir / f"{safe_name}.md"
                        md_path.write_text(content_str, encoding="utf-8")
                        sheet_files.append(safe_name)
                else:
                    if is_commission_data_sheet:
                        logger.info(
                            f"  Sheet '{sheet_name}' is commission data but cleaning is disabled, skipping Pass 0C"
                        )
                    else:
                        logger.info(
                            f"  Sheet '{sheet_name}' is not commission data, skipping Pass 0C"
                        )
                    md_path = sheets_dir / f"{safe_name}.md"
                    md_path.write_text(content_str, encoding="utf-8")
                    sheet_files.append(safe_name)

                if table_split_info:
                    table_splits_metadata[safe_name] = table_split_info

                logger.info(f"  ✓ Sheet '{sheet_name}' indexed")

            except Exception as e:
                error_msg = str(e)
                logger.error(f"  ✗ Failed to index sheet '{sheet_name}': {error_msg}")
                failed_sheets.append({"sheet_name": sheet_name, "error": error_msg})

        if not sheet_files:
            raise ValueError(
                f"Failed to index any sheets. {len(failed_sheets)} sheets failed: "
                f"{', '.join([s['sheet_name'] for s in failed_sheets])}"
            )

        if failed_sheets:
            logger.warning(
                f"Successfully indexed {len(sheet_files)}/{len(sheet_names)} sheets. "
                f"Failed: {', '.join([s['sheet_name'] for s in failed_sheets])}"
            )

        metadata = {
            "source_type": "excel",
            "content_files": {"sheets": sheet_files},
            "source_metadata": {
                "sheet_names": [
                    f"{sheet_name_prefix}{name}" if sheet_name_prefix else name
                    for name in sheet_names
                    if self._sanitize_filename(
                        f"{sheet_name_prefix}{name}" if sheet_name_prefix else name
                    ) in sheet_files
                ],
                "total_sheets": len(sheet_names),
                "successful_sheets": len(sheet_files),
                "failed_sheets": [s["sheet_name"] for s in failed_sheets]
                if failed_sheets
                else [],
                "content_format": "md",
            },
        }

        if sheet_classifications:
            metadata["sheet_classifications"] = sheet_classifications

        if table_splits_metadata:
            metadata["table_splits"] = table_splits_metadata
            sheets_split = sum(
                1 for v in table_splits_metadata.values() if v.get("split_applied")
            )
            total_tables = sum(
                v.get("table_count", 1)
                for v in table_splits_metadata.values()
                if v.get("split_applied")
            )
            if "cleaning_metadata" not in metadata:
                metadata["cleaning_metadata"] = {}
            metadata["cleaning_metadata"]["pass0c_table_detection"] = {
                "sheets_analyzed": len(table_splits_metadata),
                "sheets_split": sheets_split,
                "total_tables_extracted": total_tables,
            }

        if failed_sheets:
            metadata["indexing_errors"] = failed_sheets

        return metadata

    def _clean_email_body(self, body: str) -> str:
        """
        Clean email body by removing caution warnings, disclaimers, quoted/forwarded
        chains, and other boilerplate. Preserves markdown table structure
        (separator rows like `| --- | --- |` and blank table rows).
        """
        import re

        if not body:
            return body

        # Strip the quoted/forwarded trail first — only the latest message body
        # is relevant for commission extraction. Replied-to / forwarded-from
        # content is duplicate noise that confuses Pass 1 metadata detection.
        body = self._strip_quoted_trail(body)

        lines = body.split("\n")
        cleaned_lines = []

        caution_patterns = [
            r"CAUTION.*external",
            r"This is an external email",
            r"Do not click any links",
            r"Do not open the attachment",
            r"unless you recognize the sender",
            r"mail is originated from outside",
            r"Watch the URL link carefully",
        ]

        disclaimer_patterns = [
            r"This email and any files transmitted",
            r"intended recipient.*confidential",
            r"sole use of the intended recipient",
            r"unauthorized review.*use.*disclosure",
            r"dissemination.*forwarding.*printing",
            r"strictly prohibited and may be unlawful",
            r"WARNING.*Computer viruses",
            r"accepts no liability",
            r"does not accept any liability",
            r"virus transmitted by this email",
            r"unable to exercise control",
            r"views expressed in this message",
            r"binding nature of the message",
            r"destroy all copies",
            r"and the original message",
            r"communications through its internal and external networks",
            r"lawful supervision.*deems necessary",
            r"prohibits.*prevent.*information systems",
            r"offensive or discriminatory",
            r"adequate virus protection",
            r"To report any incident",
            r"like us on Facebook",
        ]

        all_patterns = caution_patterns + disclaimer_patterns

        # Markdown table separator rows: `| --- | --- |`, `|---|---|`, etc.
        # These MUST be preserved — without them downstream parsers (Pass 0c,
        # codegen helpers, etc.) cannot identify table boundaries.
        sep_row_re = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")

        for line in lines:
            if sep_row_re.match(line):
                cleaned_lines.append(line)
                continue

            is_boilerplate = False
            for pattern in all_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    is_boilerplate = True
                    break

            # Drop pipe-rows that are caution banners rendered as a 1-cell table
            # (e.g. `| CAUTION: external email |`). Keep all other pipe-rows —
            # an empty data row may still be meaningful row-positioning.
            if not is_boilerplate and "|" in line:
                if any(re.search(p, line, re.IGNORECASE) for p in caution_patterns):
                    is_boilerplate = True

            if not is_boilerplate:
                cleaned_lines.append(line)

        cleaned = "\n".join(cleaned_lines)

        # Strip stale `[cid:imageNNN.ext@<hash>]` placeholders. They confuse
        # Pass 1 metadata extraction and add no signal — the underlying images
        # (when they're commission grids) are surfaced as proper sheets via
        # _extract_image_table_sheets, so the body reference is redundant.
        cleaned = re.sub(r"\[cid:[^\]\s]+\]\s*", "", cleaned)

        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

        return cleaned.strip()

    def _strip_quoted_trail(self, body: str) -> str:
        """
        Drop everything from the first quoted-reply / forwarded-message marker.

        Email clients use a variety of conventions to mark the boundary between
        the new message and the prior thread. We scan for the earliest match of
        any of these and truncate.

        Conservative on purpose: only well-known unambiguous markers; never trim
        on `From:` alone (that header appears legitimately in some bodies).
        """
        import re

        if not body:
            return body

        markers = [
            # Outlook / Exchange explicit reply boundary
            r"^\s*-{3,}\s*Original Message\s*-{3,}\s*$",
            # Outlook / Exchange forward boundary
            r"^\s*-{3,}\s*Forwarded by .+\s*-{3,}\s*$",
            r"^\s*Begin forwarded message:\s*$",
            # Underscored separator (Outlook web)
            r"^_{5,}\s*$",
            # Outlook header block: "From: ... Sent: ... To: ... Subject: ..."
            # Matched as a *line* containing both "From:" at start and another
            # quoted header on subsequent lines is too noisy; instead only trim
            # when a "From:" line is followed immediately by "Sent:" or "Date:".
            # Handled below as a 2-line lookahead.
            # Gmail / Apple reply: "On <date>, <name> wrote:"
            r"^\s*On .+ wrote:\s*$",
            r"^\s*On .+ at .+ wrote:\s*$",
        ]
        compiled = [re.compile(p, re.IGNORECASE) for p in markers]
        # Outlook quoted-header tokens. We treat a `From:` line as a quoted
        # boundary only when it's followed (within a small window) by both a
        # `Sent:` (or `Date:`) and a `Subject:` line, since some HTML→text
        # conversions split the header into one label per line:
        #
        #   From:
        #   Hemant Kumar
        #   Sent:
        #   Friday, June 20, 2025
        #   Subject:
        #   ...
        #
        # Matching only `From:`+`Sent:` adjacency misses this layout.
        from_re = re.compile(r"^\s*from\s*:", re.IGNORECASE)
        sent_or_date_re = re.compile(r"^\s*(sent|date)\s*:", re.IGNORECASE)
        subject_re = re.compile(r"^\s*subject\s*:", re.IGNORECASE)

        lines = body.split("\n")
        cut_at = None
        for idx, line in enumerate(lines):
            if any(rx.match(line) for rx in compiled):
                cut_at = idx
                break
            if from_re.match(line):
                # Window must be wide enough to cover the Outlook split-line
                # layout where label and value alternate:
                #   From: / <name> / Sent: / <date> / To: / <addr> / Cc: / <addr> / Subject: / <subject>
                window = lines[idx + 1 : idx + 14]
                has_sent = any(sent_or_date_re.match(w) for w in window)
                has_subject = any(subject_re.match(w) for w in window)
                if has_sent and has_subject:
                    cut_at = idx
                    break

        if cut_at is None:
            return body
        return "\n".join(lines[:cut_at]).rstrip()

    def _strip_quoted_trail_html(self, html: str) -> str:
        """
        Trim raw HTML to everything before the first quoted-header block.

        Used by image-attachment routing: we need the cid-reference set from
        the LATEST message only, but `extract_eml_content` returns the full
        HTML body (which contains every quoted reply). Cutting on the first
        `<b>From:` / `<b>Sent:` / `-----Original Message-----` boundary keeps
        only the new message's cid: references in scope.

        Conservative: if no boundary is found, returns the input unchanged.
        """
        import re
        if not html:
            return html
        boundary_patterns = [
            # Outlook bold-header reply block (any inline HTML between <b> and From:)
            r"<b[^>]*>(?:\s|<[^>]+>)*From\s*:",
            # Plain HTML "Original Message" / "Forwarded by" markers
            r"-{3,}\s*Original Message\s*-{3,}",
            r"-{3,}\s*Forwarded by",
            # Gmail reply line ("On <date>, <name> wrote:")
            r"On [^<]+wrote:",
        ]
        earliest = None
        for pat in boundary_patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m and (earliest is None or m.start() < earliest):
                earliest = m.start()
        if earliest is None:
            return html
        return html[:earliest]

    def _format_email_context(self, email_data: Dict) -> str:
        """Format email metadata and body for prepending to sheets."""
        body = self._clean_email_body(email_data.get("body", ""))

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
            body,
            "",
        ]
        return "\n".join(parts)

    def _calculate_content_hash(self, file_path: Path) -> str:
        """Calculate SHA-256 hash of file content."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
        return f"sha256:{sha256.hexdigest()}"

    def _sanitize_filename(self, name: str) -> str:
        """
        Sanitize filename for use as document ID.

        Replaces special characters with underscores and limits length.
        """
        import re

        safe_name = re.sub(r"[^\w\-_]", "_", str(name))
        return safe_name[:100]

    def _save_metadata(self, metadata: dict, output_dir: Path):
        """Save metadata JSON to output directory."""
        metadata_path = output_dir / "metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        logger.info(f"Saved metadata: {metadata_path}")
