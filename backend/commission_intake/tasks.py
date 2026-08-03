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

from typing import TYPE_CHECKING

from celery import chain, shared_task
from celery.exceptions import Ignore

if TYPE_CHECKING:
    from commission_intake.models import IndexedContent

from cins.processors.llm_services.langfuse_client import (
    set_trace_context,
    clear_trace_context,
    flush as langfuse_flush,
    make_pass_trace_id,
)

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
        set_trace_context(session_id=document_id)
        doc = CommissionDocument.objects.get(id=document_id)

        # Re-indexing supersedes everything downstream (the freshly indexed
        # content/sheets won't match the prior extraction's rules or validation
        # output). Wipe stale artefacts BEFORE indexing so the UI doesn't keep
        # showing old CICS rules against new sheets.
        _purge_extraction_artifacts(doc)

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

            indexer = ContentIndexer(
                indexed_dir=index_dir, llm_provider=doc.llm_provider
            )
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
                            storage_key = f"commission_intake/attachments/{document_id}/{att_file.name}"
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
                        "source_metadata": indexed_doc.metadata.get(
                            "source_metadata", {}
                        ),
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
    finally:
        langfuse_flush()
        clear_trace_context()


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

    try:
        set_trace_context(session_id=document_id)
        doc = CommissionDocument.objects.get(id=document_id)
        indexed = IndexedContent.objects.get(document=doc)

        # ── Output-cache lookup (before any LLM work) ────────────────────────
        # Same byte-identical input + same prompts/model/pipeline version → serve
        # the pinned, gate-passing result verbatim instead of re-rolling codegen.
        from commission_intake import cics_cache
        from cins.processors.llm_services.factory import get_llm_service

        try:
            _resolved_model = get_llm_service(
                provider=doc.llm_provider,
                **({"model": doc.llm_model} if doc.llm_model else {}),
            ).model
        except Exception:
            _resolved_model = doc.llm_model or ""

        cache_key = cics_cache.compute_cache_key(
            content_hash=indexed.content_hash,
            indexer_version=indexed.indexer_version,
            prompt_version_hash=cics_cache.compute_prompt_version_hash(),
            llm_provider=doc.llm_provider or "",
            llm_model=_resolved_model,
        )

        # Clear stale data from any previous extraction run.
        ExtractionPass.objects.filter(document=doc).delete()
        CICSResult.objects.filter(document=doc).delete()
        ValidationResult.objects.filter(document=doc).delete()

        cached = cics_cache.lookup(cache_key)
        if cached is not None:
            logger.info(
                f"CICS output-cache HIT for {document_id} (key={cache_key[:12]}…) "
                f"— repopulating without LLM"
            )
            _repopulate_from_cache(doc, cached)
            cached.hit_count = (cached.hit_count or 0) + 1
            cached.save(update_fields=["hit_count", "updated_at"])
            return document_id

        # Pre-create ExtractionPass rows as PENDING for each sheet × 3 passes the
        # pipeline will actually run. Use the pipeline's own routing helper so
        # the (sheet_name, attachment_name) keys exactly match what the pipeline
        # writes during execution — otherwise pre-created rows orphan as PENDING
        # and the UI sees an inconsistent pass set.
        if indexed.sheet_contents:
            from cins.processors.core.pipeline import derive_pipeline_routing
            from cins.processors.core.sheet_visibility import (
                all_hidden_error,
                hidden_sheet_names,
            )

            sheet_metadata = indexed.sheet_metadata or {}
            source_meta = sheet_metadata.get("source_metadata", {})
            classifications = sheet_metadata.get("classifications", {})
            sheets_to_process, attachment_name = derive_pipeline_routing(
                source_type=indexed.source_type,
                sheet_classifications=classifications,
                sheet_names=list(indexed.sheet_contents.keys()),
                excel_attachments=source_meta.get("excel_attachments", []),
            )

            # Nothing routable — every sheet's tab is hidden. Fail loudly here,
            # before any LLM call, rather than completing with zero rules and a
            # green status the reviewer would read as "no commission data".
            if not sheets_to_process:
                raise ValueError(
                    all_hidden_error(
                        len(indexed.sheet_contents),
                        hidden_sheet_names(classifications),
                    )
                )

            for sheet_name in sheets_to_process:
                for pass_num in ExtractionPass.PassNumber.values:
                    ExtractionPass.objects.get_or_create(
                        document=doc,
                        pass_number=pass_num,
                        sheet_name=sheet_name,
                        attachment_name=attachment_name,
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

            result = pipeline.process_from_indexed(
                indexed_doc, output_path=fake_dir / "cics.json"
            )

            # Final upsert for any entries not yet persisted by the callback.
            _save_extraction_passes(doc, result)

            # ── Cross-run regression guard ───────────────────────────────────
            # Diff this (cache-miss) extraction against the last gate-passing run
            # of the same source content. A lost product/segment or big shortfall
            # → surface a loud warning and refuse to pin the regressed result, so a
            # prompt/pipeline bump can't silently drop a segment the good run had.
            regression = cics_cache.RegressionReport()
            try:
                baseline = cics_cache.find_prior_baseline(
                    indexed.content_hash, exclude_cache_key=cache_key
                )
                if baseline is not None:
                    regression = cics_cache.detect_regression(
                        baseline.cics_doc, result.cics_doc
                    )
                    if regression.is_regression:
                        result.warnings = list(result.warnings or []) + [
                            regression.warning
                        ]
                        logger.warning(
                            f"Regression guard for {document_id}: "
                            f"{regression.reason} "
                            f"(baseline {regression.baseline_total} → "
                            f"{regression.new_total} rules)"
                        )
            except Exception as re_exc:  # guard is best-effort; never fail extraction
                logger.warning(f"Regression guard failed: {re_exc}")

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

            # Write the canonical carrier short code (e.g. "FG", "New India")
            # back to the document. We use provider_id (which is the short code
            # with spaces -> underscores per CICS schema regex) and reverse the
            # underscore conversion so the DB matches the user-facing list.
            # Skip UNKNOWN_PROVIDER so the column isn't polluted with sentinel.
            provider_id = (
                (result.cics_doc or {}).get("provider", {}).get("provider_id", "")
            )
            if provider_id and provider_id != "UNKNOWN_PROVIDER":
                doc.provider = provider_id.replace("_", " ")
                doc.save(update_fields=["provider", "updated_at"])

            # ── Output-cache write ───────────────────────────────────────────
            # Pin only clean, gate-passing results so a rerun is reproducible and
            # a segment-dropping / requires_review run is never frozen in.
            try:
                if cics_cache.should_cache(result) and not regression.is_regression:
                    cics_cache.store(
                        cache_key=cache_key,
                        content_hash=indexed.content_hash,
                        indexer_version=indexed.indexer_version,
                        prompt_version_hash=cics_cache.compute_prompt_version_hash(),
                        llm_provider=doc.llm_provider or "",
                        llm_model=_resolved_model,
                        result=result,
                        provider_id=provider_id or "",
                    )
                    logger.info(
                        f"CICS output-cache STORE for {document_id} "
                        f"(key={cache_key[:12]}…)"
                    )
                else:
                    reason = (
                        "regression vs prior good run"
                        if regression.is_regression
                        else "errors or requires_review"
                    )
                    logger.info(
                        f"CICS output-cache SKIP for {document_id} "
                        f"({reason} — not pinning)"
                    )
            except Exception as ce:  # cache is best-effort; never fail extraction
                logger.warning(f"CICS output-cache write failed: {ce}")

        finally:
            shutil.rmtree(fake_dir, ignore_errors=True)

        return document_id

    except Exception as exc:
        logger.exception(f"Extraction failed for document {document_id}: {exc}")
        _fail_document(document_id, str(exc))
        raise Ignore()
    finally:
        langfuse_flush()
        clear_trace_context()


def _build_content_files(indexed: "IndexedContent") -> dict:
    """Build the content_files metadata dict from IndexedContent DB row."""
    content_files: dict = {}
    if indexed.primary_content:
        suffix = ".md" if indexed.source_type in ("email", "excel") else ".txt"
        content_files["primary"] = f"primary_content{suffix}"
    if indexed.sheet_contents:
        content_files["sheets"] = list(indexed.sheet_contents.keys())
    return content_files


def _purge_extraction_artifacts(doc) -> None:
    """
    Drop everything downstream of indexing for `doc`: the prior CICS result,
    validation result, every ExtractionPass row, the cached provider name,
    and any attachment objects we previously uploaded to S3 (the new index
    run will re-upload only the attachments it actually emits).

    Safe to call before any (re-)indexing run. The IndexedContent row itself
    is left alone — `index_document` will overwrite it via update_or_create.
    """
    from django.core.files.storage import default_storage

    from commission_intake.models import (
        CICSResult,
        ExtractionPass,
        IndexedContent,
        ValidationResult,
    )

    ExtractionPass.objects.filter(document=doc).delete()
    CICSResult.objects.filter(document=doc).delete()
    ValidationResult.objects.filter(document=doc).delete()

    # Stale attachments uploaded by a prior index run can have different
    # filenames than what the new run will produce (e.g. image attachment
    # support added later, or a re-indexed email with a fresh image set).
    indexed = IndexedContent.objects.filter(document=doc).first()
    if indexed:
        for key in indexed.attachment_keys or []:
            try:
                default_storage.delete(key)
            except Exception as exc:
                logger.warning("Could not delete stale attachment %s: %s", key, exc)

    update_fields = []
    if doc.is_validated:
        doc.is_validated = False
        doc.validated_at = None
        update_fields += ["is_validated", "validated_at"]
    if doc.provider:
        # Provider name is recomputed from the next extraction; clearing here
        # avoids a stale provider sticking on the doc card during re-index.
        doc.provider = ""
        update_fields.append("provider")
    if doc.error:
        doc.error = None
        update_fields.append("error")
    if update_fields:
        update_fields.append("updated_at")
        doc.save(update_fields=update_fields)


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
                "system_instruction": entry.get("system_instruction", ""),
                "llm_model_used": entry.get("model", ""),
                "extraction_method": entry.get("extraction_method", "llm"),
                "codegen_code": entry.get("codegen_code", ""),
                "duration_seconds": entry.get("duration", 0.0),
                "error": entry.get("error", ""),
            },
        )


def _save_extraction_passes(doc, result) -> None:
    """Final upsert of any sheet_pass_results not already persisted by the callback."""
    if result.sheet_pass_results:
        _upsert_extraction_passes(doc, result.sheet_pass_results)


def _repopulate_from_cache(doc, cached) -> None:
    """Rebuild ExtractionPass + CICSResult rows from a cache hit, no LLM.

    ``run_extraction`` deletes those rows before the lookup, so a hit must fully
    re-materialise them or the UI would show an empty document. Validation is left
    to the normal ``run_validation`` task that follows in the chain (it reads the
    repopulated CICSResult and is deterministic/cheap).
    """
    from commission_intake.models import CICSResult

    _upsert_extraction_passes(doc, cached.sheet_pass_results or [])

    CICSResult.objects.update_or_create(
        document=doc,
        defaults={
            "cics_doc": cached.cics_doc,
            "raw_extraction": None,
            "warnings": cached.warnings or [],
            "errors": cached.errors or [],
            "duration_seconds": cached.duration_seconds or 0.0,
            "sheet_results": cached.sheet_results or [],
        },
    )

    provider_id = cached.provider_id or (
        (cached.cics_doc or {}).get("provider", {}).get("provider_id", "")
    )
    if provider_id and provider_id != "UNKNOWN_PROVIDER":
        doc.provider = provider_id.replace("_", " ")
        doc.save(update_fields=["provider", "updated_at"])


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

    from commission_intake.models import (
        CommissionDocument,
        CICSResult,
        ValidationResult,
    )

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
                "severity": issue.severity.value
                if hasattr(issue.severity, "value")
                else str(issue.severity),
                "category": getattr(issue, "category", ""),
                "field_path": getattr(issue, "field_path", ""),
                "message": getattr(issue, "message", str(issue)),
                "suggestion": getattr(issue, "suggestion", ""),
            }

        def _overlap_to_dict(overlap):
            return {
                "severity": overlap.severity.value
                if hasattr(overlap.severity, "value")
                else str(overlap.severity),
                "overlap_type": getattr(overlap, "overlap_type", ""),
                "product_code": getattr(overlap, "product_code", ""),
                "rule1_id": getattr(overlap, "rule1_id", ""),
                "rule2_id": getattr(overlap, "rule2_id", ""),
                "description": getattr(overlap, "description", ""),
                "overlapping_dimensions": getattr(
                    overlap, "overlapping_dimensions", []
                ),
            }

        schema_issues = [
            _issue_to_dict(i) for i in (schema_result.errors + schema_result.warnings)
        ]
        overlap_issues = [_overlap_to_dict(o) for o in overlap_result.overlaps]

        ValidationResult.objects.update_or_create(
            document=doc,
            defaults={
                "schema_issues": schema_issues,
                "overlap_issues": overlap_issues,
                "is_valid": not (schema_result.has_critical or schema_result.has_high),
                "has_critical": schema_result.has_critical
                or overlap_result.has_critical,
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
def rerun_single_pass(
    document_id: str, pass_number: int, sheet_name: str, attachment_name: str
):
    """
    Re-run a single extraction pass (1, 2, or 3) for a specific sheet.
    Reads indexed content from DB, rebuilds the same per-pass content strings
    that the pipeline builds in _process_sheets, and updates the ExtractionPass row.
    Does NOT rerun transform or validation.
    """
    import time as _time

    from commission_intake.models import (
        CommissionDocument,
        IndexedContent,
        ExtractionPass,
    )
    from cins.processors.llm_services.factory import get_llm_service
    from cins.processors.core.llm_extract import (
        _extract_pass1_metadata,
        _extract_pass2_via_codegen,
        _extract_pass3_via_codegen,
    )
    from cins.schema.extraction_passes import MetadataExtraction, MasterDataExtraction

    doc = None
    try:
        set_trace_context(
            session_id=document_id,
            trace_id=make_pass_trace_id(
                document_id, pass_number, sheet_name, attachment_name
            ),
            trace_name=f"rerun-pass-{pass_number}",
            metadata={
                "document_id": document_id,
                "pass": pass_number,
                "sheet": sheet_name,
                "attachment": attachment_name,
            },
        )
        doc = CommissionDocument.objects.get(id=document_id)
        indexed = IndexedContent.objects.get(document=doc)

        # Mark the targeted pass as running
        ExtractionPass.objects.filter(
            document=doc,
            pass_number=pass_number,
            sheet_name=sheet_name,
            attachment_name=attachment_name,
        ).update(status=ExtractionPass.Status.RUNNING, error="")

        from cins.processors.core.pipeline import build_pass_contents
        from cins.processors.core.sheet_visibility import derive_sheet_sets

        sheet_contents = indexed.sheet_contents or {}
        sheet_metadata = indexed.sheet_metadata or {}
        classifications = sheet_metadata.get("classifications", {})

        # Same derivation the pipeline routes on. This used to be a local
        # comprehension keyed on `classification or sheet_type` while routing
        # keyed on `sheet_type` alone — two readers, one document, two different
        # Pass 2 prompts. Sharing the helper also means the rerun cannot
        # re-inject a hidden master sheet the main run excluded.
        commission_sheets, master_sheets, _hidden = derive_sheet_sets(classifications)

        master_context = ""
        for ms in master_sheets:
            content = sheet_contents.get(ms, "")
            if content:
                master_context += f"\n\n## Master Data Reference: {ms}\n\n{content}"

        sheet_content = sheet_contents.get(sheet_name, "")
        email_body = indexed.primary_content if indexed.source_type == "email" else ""

        pass1_prompt, pass2_prompt, pass3_prompt, email_is_primary_commission = (
            build_pass_contents(
                sheet_name=sheet_name,
                sheet_content=sheet_content,
                master_context=master_context,
                email_body=email_body,
                commission_sheets=commission_sheets,
            )
        )

        service = get_llm_service(
            provider=doc.llm_provider,
            **({"model": doc.llm_model} if doc.llm_model else {}),
        )

        start = _time.monotonic()
        p2_codegen_code = ""
        p2_extraction_method = "llm"
        p3_codegen_code = ""
        p3_extraction_method = "llm"

        if pass_number == 1:
            result_obj = _extract_pass1_metadata(pass1_prompt, service)
            result_dict = result_obj.model_dump()
        elif pass_number == 2:
            pass1_row = ExtractionPass.objects.filter(
                document=doc,
                pass_number=1,
                sheet_name=sheet_name,
                attachment_name=attachment_name,
            ).first()
            metadata_obj = (
                MetadataExtraction.model_validate(pass1_row.result)
                if pass1_row and pass1_row.result
                else MetadataExtraction()
            )
            # Pass 2 always runs via codegen (matches main pipeline; the JSON
            # path was removed for perf reasons — see plan A1).
            result_obj, p2_codegen_code, _user_prompt = _extract_pass2_via_codegen(
                pass2_prompt, service, metadata_obj
            )
            p2_extraction_method = "codegen"
            result_dict = result_obj.model_dump()
        else:  # pass 3
            pass1_row = ExtractionPass.objects.filter(
                document=doc,
                pass_number=1,
                sheet_name=sheet_name,
                attachment_name=attachment_name,
            ).first()
            pass2_row = ExtractionPass.objects.filter(
                document=doc,
                pass_number=2,
                sheet_name=sheet_name,
                attachment_name=attachment_name,
            ).first()
            metadata_obj = (
                MetadataExtraction.model_validate(pass1_row.result)
                if pass1_row and pass1_row.result
                else MetadataExtraction()
            )
            master_data_obj = (
                MasterDataExtraction.model_validate(pass2_row.result)
                if pass2_row and pass2_row.result
                else MasterDataExtraction()
            )

            # email_is_primary_commission: email body already IS pass3_content — no prepend needed.
            # Otherwise fall back to the has_commission_data flag (email body mixed with sheet content).
            if (
                not email_is_primary_commission
                and metadata_obj.has_commission_data
                and email_body
            ):
                pass3_prompt = (
                    f"## Email Commission Data\n\n{email_body}\n\n---\n\n{pass3_prompt}"
                )

            # Per-sheet shape for the codegen completeness gate (fails open if
            # the doc was indexed before rate_cell_count existed).
            from cins.processors.core.pipeline import (
                get_safe_sheet_name,
                _table_shape_for_sheet,
            )

            pass3_table_shape = _table_shape_for_sheet(
                (indexed.sheet_metadata or {}).get("table_splits", {}),
                sheet_name,
                get_safe_sheet_name(sheet_name),
            )

            # Pass 3 always runs via codegen (matches the main pipeline).
            result_obj, p3_codegen_code, _ = _extract_pass3_via_codegen(
                pass3_prompt,
                service,
                metadata_obj,
                master_data_obj,
                table_shape=pass3_table_shape,
            )
            p3_extraction_method = "codegen"
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
                "extraction_method": p3_extraction_method
                if pass_number == 3
                else p2_extraction_method
                if pass_number == 2
                else "llm",
                "codegen_code": p3_codegen_code
                if pass_number == 3
                else p2_codegen_code
                if pass_number == 2
                else "",
                "duration_seconds": round(duration, 2),
                "error": "",
            },
        )

        return document_id

    except Exception as exc:
        logger.exception(
            f"Rerun pass {pass_number} failed for document {document_id}: {exc}"
        )
        if doc:
            ExtractionPass.objects.filter(
                document=doc,
                pass_number=pass_number,
                sheet_name=sheet_name,
                attachment_name=attachment_name,
            ).update(status=ExtractionPass.Status.FAILED, error=str(exc))
        raise Ignore()
    finally:
        langfuse_flush()
        clear_trace_context()


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

    from commission_intake.models import (
        CommissionDocument,
        ExtractionPass,
        CICSResult,
        ValidationResult,
    )
    from cins.schema.extraction_passes import (
        MetadataExtraction,
        MasterDataExtraction,
        RulesExtraction,
    )
    from cins.schema.raw_extraction import RawExtraction
    from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
    from cins.processors.core.cpa_split import materialize_cpa_split

    try:
        doc = CommissionDocument.objects.get(id=document_id)

        # Gather all completed pass results grouped by sheet
        passes = list(
            ExtractionPass.objects.filter(
                document=doc, status=ExtractionPass.Status.COMPLETED
            ).order_by("sheet_name", "attachment_name", "pass_number")
        )

        if not passes:
            raise ValueError(
                "No completed extraction passes found — run extraction first."
            )

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
                merged_raw.commission_entries = (
                    merged_raw.commission_entries or []
                ) + (raw.commission_entries or [])
                merged_raw.master_data = (merged_raw.master_data or []) + (
                    raw.master_data or []
                )

        if merged_raw is None:
            raise ValueError("Failed to build RawExtraction from passes.")

        transformer = RawToCICSTransformer()
        cics_doc, transform_warnings, transform_errors = transformer.transform(
            merged_raw
        )
        cics_json = cics_doc.model_dump(exclude_none=False)
        # Materialize CPA-deduction rules into With/Without-CPA variants — the
        # full pipeline (pipeline.py) applies this immediately after transform,
        # so re-transform must too or the rebuilt document silently loses the
        # CPA dimension (every rule collapses to cpa=None, halving CV counts).
        cics_json = materialize_cpa_split(cics_json)

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

        # Mirror the writeback semantics in run_extraction: store the canonical
        # short code (provider_id with underscores -> spaces), skip the
        # UNKNOWN_PROVIDER sentinel.
        provider_id = cics_json.get("provider", {}).get("provider_id", "")
        update_fields: list[str] = []
        if provider_id and provider_id != "UNKNOWN_PROVIDER":
            doc.provider = provider_id.replace("_", " ")
            update_fields.append("provider")

        # Re-transform is a recovery action: a successful rebuild supersedes
        # any prior failure, so clear the stale error banner and flip status
        # back to extracting (run_validation will set completed on success).
        if doc.error:
            doc.error = None
            update_fields.append("error")
        if doc.status == CommissionDocument.Status.FAILED:
            doc.status = CommissionDocument.Status.EXTRACTING
            update_fields.append("status")
        if update_fields:
            update_fields.append("updated_at")
            doc.save(update_fields=update_fields)

        # Run validation immediately after transform, then mark completed —
        # re-transform is a recovery action, so it must drive the doc all the
        # way back to COMPLETED on its own (no enclosing chain to do it).
        ValidationResult.objects.filter(document=doc).delete()
        chain(
            run_validation.si(document_id),
            _set_completed.si(document_id),
        ).apply_async(queue=COMMISSION_INTAKE_QUEUE)

        return document_id

    except Exception as exc:
        logger.exception(f"Transform failed for document {document_id}: {exc}")
        _fail_document(document_id, str(exc))
        raise Ignore()
