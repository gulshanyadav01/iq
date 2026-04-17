"""
Celery task chain for commission document processing.

Chain: set_indexing → index_document → set_extracting → run_extraction
       → run_validation → set_completed

Each stage updates CommissionDocument.status so the API can report progress.
Failures at any stage mark the document as FAILED and stop the chain.
"""

import hashlib
import logging
import os
import shutil
import tempfile

from celery import chain, shared_task
from celery.exceptions import Ignore

logger = logging.getLogger("commission_intake.tasks")

COMMISSION_INTAKE_QUEUE = "commission_intake"


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, queue=COMMISSION_INTAKE_QUEUE)
def _set_status(self, document_id: str, new_status: str):
    from commission_intake.models import CommissionDocument

    try:
        doc = CommissionDocument.objects.get(id=document_id)
        doc.status = new_status
        doc.save(update_fields=["status", "updated_at"])
        return document_id
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


# ---------------------------------------------------------------------------
# Stage 1: Index document content
# ---------------------------------------------------------------------------


@shared_task(queue=COMMISSION_INTAKE_QUEUE)
def index_document(document_id: str):
    """
    Download source file from S3, extract content via ContentIndexer,
    and save structured text to IndexedContent model.
    """
    from django.core.files.storage import default_storage

    from commission_intake.models import CommissionDocument, IndexedContent
    from cins.processors.content.indexer import ContentIndexer, INDEXER_VERSION

    try:
        doc = CommissionDocument.objects.get(id=document_id)

        # Download file from S3/MinIO to a local temp file
        suffix = os.path.splitext(doc.original_filename)[-1] or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            with default_storage.open(doc.file.name, "rb") as src:
                content = src.read()
                tmp.write(content)
                content_hash = hashlib.sha256(content).hexdigest()

        # Use a temp dir for the indexer's disk cache (we'll read it then discard it)
        index_dir = tempfile.mkdtemp()

        try:
            def _on_progress(step: str, detail: str = "") -> None:
                """Persist each indexing step to the document for real-time polling."""
                try:
                    CommissionDocument.objects.filter(id=document_id).update(
                        index_progress={"step": step, "detail": detail}
                    )
                except Exception:
                    pass

            indexer = ContentIndexer(indexed_dir=index_dir)
            indexed_doc = indexer.index_document(
                file_path=tmp_path,
                provider=doc.provider or "unknown",
                excel_mode=doc.excel_mode,
                force_reindex=True,
                progress_callback=_on_progress,
            )

            # Detect source type
            source_type = indexed_doc.source_type
            doc.source_type = source_type
            doc.save(update_fields=["source_type", "updated_at"])

            # Read all indexed content into memory before we delete the temp dir
            primary_content = ""
            email_context = ""
            sheet_contents = {}
            sheet_metadata = {}
            attachment_keys = []

            try:
                primary_content = indexed_doc.get_primary_content()
            except Exception:
                pass

            if source_type == "email":
                try:
                    email_context = indexed_doc.get_email_context() or ""
                except Exception:
                    pass

                # Upload any extracted attachments to S3
                attachments_dir = indexed_doc.indexed_dir / "attachments"
                if attachments_dir.exists():
                    for att_file in attachments_dir.iterdir():
                        if att_file.is_file():
                            storage_key = (
                                f"commission_intake/attachments/{document_id}/{att_file.name}"
                            )
                            with open(att_file, "rb") as f:
                                default_storage.save(storage_key, f)
                            attachment_keys.append(storage_key)

                # If email had Excel attachments, capture their sheet content too
                if indexed_doc.metadata.get("content_files", {}).get("sheets"):
                    try:
                        sheet_contents = indexed_doc.get_all_sheet_contents()
                    except Exception:
                        pass
                    sheet_metadata = {
                        "classifications": indexed_doc.get_sheet_classifications(),
                        "table_splits": indexed_doc.get_table_splits(),
                        "source_metadata": indexed_doc.metadata.get("source_metadata", {}),
                    }

            if source_type == "excel":
                try:
                    sheet_contents = indexed_doc.get_all_sheet_contents()
                except Exception:
                    pass
                sheet_metadata = {
                    "classifications": indexed_doc.get_sheet_classifications(),
                    "table_splits": indexed_doc.get_table_splits(),
                    "source_metadata": indexed_doc.metadata.get("source_metadata", {}),
                }

            IndexedContent.objects.update_or_create(
                document=doc,
                defaults={
                    "source_type": source_type,
                    "primary_content": primary_content,
                    "email_context": email_context,
                    "sheet_contents": sheet_contents,
                    "sheet_metadata": sheet_metadata,
                    "attachment_keys": attachment_keys,
                    "content_hash": content_hash,
                    "indexer_version": INDEXER_VERSION,
                },
            )

            # Clear transient progress now that indexing is done
            CommissionDocument.objects.filter(id=document_id).update(index_progress={})

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            shutil.rmtree(index_dir, ignore_errors=True)

        return document_id

    except Exception as exc:
        logger.exception(f"Indexing failed for document {document_id}: {exc}")
        _fail_document(document_id, str(exc))
        raise Ignore()


# ---------------------------------------------------------------------------
# Stage 2: LLM extraction
# ---------------------------------------------------------------------------


@shared_task(queue=COMMISSION_INTAKE_QUEUE)
def run_extraction(document_id: str):
    """
    Reconstruct IndexedDocument from DB, run CINS Pipeline extraction,
    and save CICSResult + ExtractionPass rows.
    """
    from pathlib import Path

    from commission_intake.models import (
        CommissionDocument,
        IndexedContent,
        ExtractionPass,
        CICSResult,
        ValidationResult,
    )
    from cins.processors.content.indexer import IndexedDocument
    from cins.processors.core.pipeline import Pipeline
    from cins.processors.core.llm_extract import is_multi_pass_enabled

    try:
        doc = CommissionDocument.objects.get(id=document_id)
        indexed = IndexedContent.objects.get(document=doc)

        # Clear stale data from any previous extraction run.
        ExtractionPass.objects.filter(document=doc).delete()
        CICSResult.objects.filter(document=doc).delete()
        ValidationResult.objects.filter(document=doc).delete()

        # Pre-create ExtractionPass rows as PENDING for each known sheet × 3 passes.
        # This lets the UI display pending state before extraction starts.
        if is_multi_pass_enabled() and indexed.sheet_contents:
            sheet_metadata = indexed.sheet_metadata or {}
            classifications = sheet_metadata.get("classifications", {})
            # Determine attachment grouping: for email source types, attachment_keys hints at the
            # original filenames. For direct Excel the attachment_name stays blank.
            attachment_names = _derive_attachment_names(indexed)
            for att_name in attachment_names:
                sheet_names = list(indexed.sheet_contents.keys())
                for sheet_name in sheet_names:
                    cls_entry = classifications.get(sheet_name, {})
                    classification = cls_entry.get("classification") or cls_entry.get("sheet_type", "unknown")
                    # Only pre-create for commission_data sheets (master_data is not run through passes)
                    if classification not in ("commission_data", "unknown"):
                        continue
                    for pass_num in ExtractionPass.PassNumber.values:
                        ExtractionPass.objects.get_or_create(
                            document=doc,
                            pass_number=pass_num,
                            sheet_name=sheet_name,
                            attachment_name=att_name,
                            defaults={"status": ExtractionPass.Status.PENDING},
                        )

        # Reconstruct an IndexedDocument dataclass populated from DB
        # (no disk reads — content is pre-loaded from DB fields)
        fake_dir = Path(tempfile.mkdtemp())  # Pipeline needs a valid path; unused here
        try:
            sheet_metadata = indexed.sheet_metadata or {}
            metadata = {
                "content_files": _build_content_files(indexed),
                "table_splits": sheet_metadata.get("table_splits", {}),
                "source_metadata": sheet_metadata.get("source_metadata", {}),
                "sheet_classifications": sheet_metadata.get("classifications", {}),
            }

            indexed_doc = IndexedDocument(
                document_id=doc.original_filename,
                provider=doc.provider or "unknown",
                source_file=fake_dir / doc.original_filename,
                source_type=indexed.source_type,
                indexed_dir=fake_dir,
                metadata=metadata,
                _primary_content=indexed.primary_content or None,
                _sheet_contents=indexed.sheet_contents or None,
                _email_context=indexed.email_context or None,
            )

            pipeline = Pipeline(
                llm_provider=doc.llm_provider,
                model=doc.llm_model or None,
                output_dir=None,
                excel_mode=doc.excel_mode,
            )

            # Persist each sheet's passes to DB as soon as the sheet finishes.
            def _on_sheet_done(entries):
                _upsert_extraction_passes(doc, entries)

            pipeline._on_sheet_complete_callback = _on_sheet_done

            result = pipeline.process_from_indexed(indexed_doc, output_path=fake_dir / "cics.json")

            # Final upsert for any entries not yet persisted (e.g. non-multi-pass path).
            _save_extraction_passes(doc, result)

            # Save final CICS result
            CICSResult.objects.update_or_create(
                document=doc,
                defaults={
                    "cics_doc": result.cics_doc,
                    "raw_extraction": None,  # populated via ExtractionPass rows
                    "warnings": result.warnings,
                    "errors": result.errors,
                    "duration_seconds": result.duration,
                    "sheet_results": result.sheet_results,
                },
            )

            # Write provider name back to the document from the CICS result
            provider_name = (result.cics_doc or {}).get("provider", {}).get("provider_name", "")
            if provider_name:
                doc.provider = provider_name
                doc.save(update_fields=["provider", "updated_at"])

        finally:
            shutil.rmtree(fake_dir, ignore_errors=True)

        return document_id

    except Exception as exc:
        logger.exception(f"Extraction failed for document {document_id}: {exc}")
        _fail_document(document_id, str(exc))
        raise Ignore()


def _build_content_files(indexed: "IndexedContent") -> dict:
    """Build the content_files metadata dict from IndexedContent DB row."""
    content_files: dict = {}
    if indexed.primary_content:
        suffix = ".md" if indexed.source_type in ("email", "excel") else ".txt"
        content_files["primary"] = f"primary_content{suffix}"
    if indexed.sheet_contents:
        content_files["sheets"] = list(indexed.sheet_contents.keys())
    return content_files


def _upsert_extraction_passes(doc, entries: list) -> None:
    """Upsert a list of pass-result dicts into ExtractionPass rows."""
    from commission_intake.models import ExtractionPass

    for entry in entries:
        ExtractionPass.objects.update_or_create(
            document=doc,
            pass_number=entry["pass_number"],
            sheet_name=entry.get("sheet_name", ""),
            attachment_name=entry.get("attachment_name", ""),
            defaults={
                "status": entry.get("status", "completed"),
                "result": entry.get("result"),
                "prompt_text": entry.get("prompt_text", ""),
                "llm_model_used": entry.get("model", ""),
                "duration_seconds": entry.get("duration", 0.0),
                "error": entry.get("error", ""),
            },
        )


def _save_extraction_passes(doc, result) -> None:
    """Final upsert of any sheet_pass_results not already persisted by the callback."""
    if result.sheet_pass_results:
        _upsert_extraction_passes(doc, result.sheet_pass_results)


def _derive_attachment_names(indexed) -> list:
    """
    Return the list of attachment identifiers for pre-creating ExtractionPass rows.

    For email source types with a single Excel attachment: process_from_indexed runs
    _process_sheets with that attachment name.  Use excel_attachments from source_metadata
    (populated by the indexer) as the authoritative list.
    For direct Excel/PDF: return [""] so passes are keyed with blank attachment_name.
    """
    if indexed.source_type != "email":
        return [""]
    sheet_metadata = indexed.sheet_metadata or {}
    source_meta = sheet_metadata.get("source_metadata", {})
    excel_attachments = source_meta.get("excel_attachments", [])
    if excel_attachments:
        # Only single-attachment emails are handled by _process_sheets;
        # multi-attachment emails would need _process_email_with_attachments (not yet wired).
        return list(excel_attachments)
    return [""]


# ---------------------------------------------------------------------------
# Stage 3: Validation
# ---------------------------------------------------------------------------


@shared_task(queue=COMMISSION_INTAKE_QUEUE)
def run_validation(document_id: str):
    """
    Load CICSResult from DB, run schema + overlap validators,
    and save ValidationResult.
    """
    import json
    import tempfile

    from commission_intake.models import CommissionDocument, CICSResult, ValidationResult

    try:
        doc = CommissionDocument.objects.get(id=document_id)
        cics_result = CICSResult.objects.get(document=doc)

        # Validators expect a file path — write to a temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            tmp_path = tmp.name
            json.dump(cics_result.cics_doc, tmp, ensure_ascii=False)

        try:
            from cins.validators.schema_validator import SchemaValidator
            from cins.validators.rule_overlap_checker import RuleOverlapChecker

            schema_result = SchemaValidator().validate(tmp_path)
            overlap_result = RuleOverlapChecker().check(tmp_path)
        finally:
            os.unlink(tmp_path)

        def _issue_to_dict(issue):
            return {
                "severity": issue.severity.value if hasattr(issue.severity, "value") else str(issue.severity),
                "category": getattr(issue, "category", ""),
                "field_path": getattr(issue, "field_path", ""),
                "message": getattr(issue, "message", str(issue)),
                "suggestion": getattr(issue, "suggestion", ""),
            }

        def _overlap_to_dict(overlap):
            return {
                "severity": overlap.severity.value if hasattr(overlap.severity, "value") else str(overlap.severity),
                "overlap_type": getattr(overlap, "overlap_type", ""),
                "product_code": getattr(overlap, "product_code", ""),
                "rule1_id": getattr(overlap, "rule1_id", ""),
                "rule2_id": getattr(overlap, "rule2_id", ""),
                "description": getattr(overlap, "description", ""),
                "overlapping_dimensions": getattr(overlap, "overlapping_dimensions", []),
            }

        schema_issues = [_issue_to_dict(i) for i in (schema_result.errors + schema_result.warnings)]
        overlap_issues = [_overlap_to_dict(o) for o in overlap_result.overlaps]

        ValidationResult.objects.update_or_create(
            document=doc,
            defaults={
                "schema_issues": schema_issues,
                "overlap_issues": overlap_issues,
                "is_valid": not (schema_result.has_critical or schema_result.has_high),
                "has_critical": schema_result.has_critical or overlap_result.has_critical,
                "has_high": schema_result.has_high or overlap_result.has_high,
            },
        )

        return document_id

    except Exception as exc:
        logger.exception(f"Validation failed for document {document_id}: {exc}")
        _fail_document(document_id, str(exc))
        raise Ignore()


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, queue=COMMISSION_INTAKE_QUEUE)
def _set_indexed(self, document_id: str):
    from commission_intake.models import CommissionDocument

    try:
        doc = CommissionDocument.objects.get(id=document_id)
        doc.status = CommissionDocument.Status.INDEXED
        doc.save(update_fields=["status", "updated_at"])
        return document_id
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


@shared_task(bind=True, max_retries=3, queue=COMMISSION_INTAKE_QUEUE)
def _set_completed(self, document_id: str):
    from commission_intake.models import CommissionDocument

    try:
        doc = CommissionDocument.objects.get(id=document_id)
        doc.status = CommissionDocument.Status.COMPLETED
        doc.save(update_fields=["status", "updated_at"])
        return document_id
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fail_document(document_id: str, error: str) -> None:
    try:
        from commission_intake.models import CommissionDocument

        doc = CommissionDocument.objects.get(id=document_id)
        doc.status = CommissionDocument.Status.FAILED
        doc.error = error
        doc.save(update_fields=["status", "error", "updated_at"])
    except Exception:
        pass


def build_processing_chain(document_id: str, mode: str = "full"):
    """Build the Celery chain for the given processing mode (not yet applied)."""
    from commission_intake.models import CommissionDocument

    if mode == "index_only":
        return chain(
            _set_status.si(document_id, CommissionDocument.Status.INDEXING),
            index_document.si(document_id),
            _set_indexed.si(document_id),
        )

    if mode == "extract_only":
        return chain(
            _set_status.si(document_id, CommissionDocument.Status.EXTRACTING),
            run_extraction.si(document_id),
            run_validation.si(document_id),
            _set_completed.si(document_id),
        )

    # default: "full"
    return chain(
        _set_status.si(document_id, CommissionDocument.Status.INDEXING),
        index_document.si(document_id),
        _set_status.si(document_id, CommissionDocument.Status.EXTRACTING),
        run_extraction.si(document_id),
        run_validation.si(document_id),
        _set_completed.si(document_id),
    )


def enqueue_processing_chain(document_id: str, mode: str = "full") -> None:
    """Enqueue a processing chain according to mode (full, index_only, extract_only)."""
    build_processing_chain(document_id, mode).apply_async(queue=COMMISSION_INTAKE_QUEUE)


# ---------------------------------------------------------------------------
# Rerun: specific pass for a specific sheet
# ---------------------------------------------------------------------------


@shared_task(queue=COMMISSION_INTAKE_QUEUE)
def rerun_single_pass(document_id: str, pass_number: int, sheet_name: str, attachment_name: str):
    """
    Re-run a single extraction pass (1, 2, or 3) for a specific sheet.
    Reads indexed content from DB, rebuilds the same per-pass content strings
    that the pipeline builds in _process_sheets, and updates the ExtractionPass row.
    Does NOT rerun transform or validation.
    """
    import shutil
    import tempfile
    import time as _time
    from pathlib import Path

    from commission_intake.models import CommissionDocument, IndexedContent, ExtractionPass
    from cins.processors.llm_services.factory import get_llm_service
    from cins.processors.core.llm_extract import (
        _extract_pass1_metadata,
        _extract_pass2_master_data,
        _extract_pass2_via_codegen,
        _extract_pass3_products_and_rules,
        _extract_pass3_via_codegen,
        _should_use_codegen_for_pass2,
        _should_use_codegen_for_pass3,
    )
    from cins.schema.extraction_passes import MetadataExtraction, MasterDataExtraction

    doc = None
    try:
        doc = CommissionDocument.objects.get(id=document_id)
        indexed = IndexedContent.objects.get(document=doc)

        # Mark the targeted pass as running
        ExtractionPass.objects.filter(
            document=doc,
            pass_number=pass_number,
            sheet_name=sheet_name,
            attachment_name=attachment_name,
        ).update(status=ExtractionPass.Status.RUNNING, error="")

        # ── Replicate pipeline._process_sheets content-building logic ──────────
        sheet_contents = indexed.sheet_contents or {}
        sheet_metadata = indexed.sheet_metadata or {}
        classifications = sheet_metadata.get("classifications", {})

        # Identify master sheets (same logic as IndexedDocument.get_master_sheets)
        master_sheets = [
            s for s, meta in classifications.items()
            if (meta.get("classification") or meta.get("sheet_type")) == "master_data"
        ]

        master_context = ""
        for ms in master_sheets:
            content = sheet_contents.get(ms, "")
            if content:
                master_context += f"\n\n## Master Data Reference: {ms}\n\n{content}"

        sheet_content = sheet_contents.get(sheet_name, "")

        # Build per-pass content strings (mirrors pipeline._process_sheets lines 640-660)
        pass1_content = ""
        if indexed.source_type == "email" and indexed.primary_content:
            pass1_content = indexed.primary_content + "\n\n"
        pass1_content += master_context

        pass2_content = master_context
        pass3_content = f"## Commission Data Sheet: {sheet_name}\n\n{sheet_content}"

        # prepare_extraction_request just wraps content in "Extract commission data from…"
        def _wrap(content: str) -> str:
            return f"Extract commission data from this document:\n\n{content}"

        pass1_prompt = _wrap(pass1_content)
        pass2_prompt = _wrap(pass2_content)
        pass3_prompt = _wrap(pass3_content)

        service = get_llm_service(**({"model": doc.llm_model} if doc.llm_model else {}))

        start = _time.monotonic()

        if pass_number == 1:
            result_obj = _extract_pass1_metadata(pass1_prompt, service)
            result_dict = result_obj.model_dump()
        elif pass_number == 2:
            pass1_row = ExtractionPass.objects.filter(
                document=doc, pass_number=1,
                sheet_name=sheet_name, attachment_name=attachment_name,
            ).first()
            metadata_obj = MetadataExtraction.model_validate(pass1_row.result) if pass1_row and pass1_row.result else MetadataExtraction()
            if _should_use_codegen_for_pass2(pass2_prompt):
                result_obj = _extract_pass2_via_codegen(pass2_prompt, service, metadata_obj)
            else:
                result_obj = _extract_pass2_master_data(pass2_prompt, service, metadata_obj)
            result_dict = result_obj.model_dump()
        else:  # pass 3
            pass1_row = ExtractionPass.objects.filter(
                document=doc, pass_number=1,
                sheet_name=sheet_name, attachment_name=attachment_name,
            ).first()
            pass2_row = ExtractionPass.objects.filter(
                document=doc, pass_number=2,
                sheet_name=sheet_name, attachment_name=attachment_name,
            ).first()
            metadata_obj = MetadataExtraction.model_validate(pass1_row.result) if pass1_row and pass1_row.result else MetadataExtraction()
            master_data_obj = MasterDataExtraction.model_validate(pass2_row.result) if pass2_row and pass2_row.result else MasterDataExtraction()
            if _should_use_codegen_for_pass3(pass3_prompt):
                result_obj = _extract_pass3_via_codegen(pass3_prompt, service, metadata_obj, master_data_obj)
            else:
                result_obj = _extract_pass3_products_and_rules(pass3_prompt, service, metadata_obj, master_data_obj)
            result_dict = result_obj.model_dump()

        duration = _time.monotonic() - start

        ExtractionPass.objects.update_or_create(
            document=doc,
            pass_number=pass_number,
            sheet_name=sheet_name,
            attachment_name=attachment_name,
            defaults={
                "status": ExtractionPass.Status.COMPLETED,
                "result": result_dict,
                "llm_model_used": service.model,
                "duration_seconds": round(duration, 2),
                "error": "",
            },
        )

        return document_id

    except Exception as exc:
        logger.exception(f"Rerun pass {pass_number} failed for document {document_id}: {exc}")
        if doc:
            ExtractionPass.objects.filter(
                document=doc,
                pass_number=pass_number,
                sheet_name=sheet_name,
                attachment_name=attachment_name,
            ).update(status=ExtractionPass.Status.FAILED, error=str(exc))
        raise Ignore()


# ---------------------------------------------------------------------------
# Rerun: raw→CICS transform from existing pass results
# ---------------------------------------------------------------------------


@shared_task(queue=COMMISSION_INTAKE_QUEUE)
def rerun_transform(document_id: str):
    """
    Rebuild the CICSResult by merging existing ExtractionPass rows and re-running
    the RawToCICSTransformer.  Does NOT re-run any LLM passes.
    Followed by validation automatically.
    """
    import shutil
    import tempfile

    from commission_intake.models import CommissionDocument, ExtractionPass, CICSResult, ValidationResult
    from cins.schema.extraction_passes import MetadataExtraction, MasterDataExtraction, RulesExtraction
    from cins.schema.raw_extraction import RawExtraction
    from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer

    try:
        doc = CommissionDocument.objects.get(id=document_id)

        # Gather all completed pass results grouped by sheet
        passes = list(
            ExtractionPass.objects.filter(document=doc, status=ExtractionPass.Status.COMPLETED)
            .order_by("sheet_name", "attachment_name", "pass_number")
        )

        if not passes:
            raise ValueError("No completed extraction passes found — run extraction first.")

        # Group by (sheet_name, attachment_name)
        from collections import defaultdict
        sheet_passes: dict = defaultdict(dict)
        for p in passes:
            key = (p.sheet_name, p.attachment_name)
            sheet_passes[key][p.pass_number] = p.result

        # Merge all sheets into a single RawExtraction (use first sheet as base, extend commission_entries)
        merged_raw = None
        for key, pass_results in sheet_passes.items():
            p1 = MetadataExtraction.model_validate(pass_results.get(1) or {})
            p2 = MasterDataExtraction.model_validate(pass_results.get(2) or {})
            p3 = RulesExtraction.model_validate(pass_results.get(3) or {})
            raw = RawExtraction.from_passes(p1, p2, p3)
            if merged_raw is None:
                merged_raw = raw
            else:
                merged_raw.commission_entries = (merged_raw.commission_entries or []) + (raw.commission_entries or [])
                merged_raw.master_data = (merged_raw.master_data or []) + (raw.master_data or [])

        if merged_raw is None:
            raise ValueError("Failed to build RawExtraction from passes.")

        transformer = RawToCICSTransformer()
        cics_doc, transform_warnings, transform_errors = transformer.transform(merged_raw)
        cics_json = cics_doc.model_dump(exclude_none=True)

        CICSResult.objects.update_or_create(
            document=doc,
            defaults={
                "cics_doc": cics_json,
                "raw_extraction": None,
                "warnings": transform_warnings or [],
                "errors": transform_errors or [],
                "duration_seconds": 0.0,
                "sheet_results": [],
            },
        )

        provider_name = cics_json.get("provider", {}).get("provider_name", "")
        if provider_name:
            doc.provider = provider_name
            doc.save(update_fields=["provider", "updated_at"])

        # Clear stale validation so the UI knows it needs re-running
        ValidationResult.objects.filter(document=doc).delete()

        return document_id

    except Exception as exc:
        logger.exception(f"Transform failed for document {document_id}: {exc}")
        _fail_document(document_id, str(exc))
        raise Ignore()
