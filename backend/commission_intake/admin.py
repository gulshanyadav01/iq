from django.contrib import admin

from commission_intake.models import (
    CommissionDocument,
    IndexedContent,
    ExtractionPass,
    CICSResult,
    ValidationResult,
)


@admin.register(CommissionDocument)
class CommissionDocumentAdmin(admin.ModelAdmin):
    list_display = ("id", "original_filename", "provider", "source_type", "status", "created_at")
    list_filter = ("status", "source_type", "llm_provider", "created_at")
    search_fields = ("original_filename", "provider", "id")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(IndexedContent)
class IndexedContentAdmin(admin.ModelAdmin):
    list_display = ("id", "document", "source_type", "indexer_version", "created_at")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(ExtractionPass)
class ExtractionPassAdmin(admin.ModelAdmin):
    list_display = ("id", "document", "pass_number", "llm_model_used", "duration_seconds", "created_at")
    list_filter = ("pass_number",)
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(CICSResult)
class CICSResultAdmin(admin.ModelAdmin):
    list_display = ("id", "document", "duration_seconds", "created_at")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(ValidationResult)
class ValidationResultAdmin(admin.ModelAdmin):
    list_display = ("id", "document", "is_valid", "has_critical", "has_high", "created_at")
    list_filter = ("is_valid", "has_critical", "has_high")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)
