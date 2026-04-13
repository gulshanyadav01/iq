from django.contrib import admin
from policy_extract.models import PolicyExtractionRequest


@admin.register(PolicyExtractionRequest)
class PolicyExtractionRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "original_filename", "status", "created_at", "updated_at")
    list_filter = ("status", "created_at")
    search_fields = ("original_filename", "id")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)
