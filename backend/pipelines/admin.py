from django.contrib import admin

from pipelines.models import Pipeline, PipelineStep, PipelineRun, StepRun


class PipelineStepInline(admin.TabularInline):
    model = PipelineStep
    extra = 0
    fields = ("order", "step_type", "name", "config")
    ordering = ("order",)


@admin.register(Pipeline)
class PipelineAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "is_seeded", "created_by", "created_at")
    list_filter = ("is_seeded",)
    search_fields = ("slug", "name")
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [PipelineStepInline]


class StepRunInline(admin.TabularInline):
    model = StepRun
    extra = 0
    fields = ("order", "step_type", "status", "started_at", "finished_at")
    readonly_fields = fields
    ordering = ("order",)
    can_delete = False


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    list_display = (
        "pipeline_run_id",
        "pipeline",
        "status",
        "created_by",
        "original_filename",
        "created_at",
    )
    list_filter = ("status", "pipeline", "created_at")
    search_fields = ("pipeline_run_id", "original_filename")
    readonly_fields = ("id", "pipeline_run_id", "created_at", "updated_at")
    ordering = ("-created_at",)
    inlines = [StepRunInline]


@admin.register(StepRun)
class StepRunAdmin(admin.ModelAdmin):
    list_display = (
        "pipeline_run",
        "order",
        "step_type",
        "status",
        "started_at",
        "finished_at",
    )
    list_filter = ("status", "step_type")
    search_fields = ("pipeline_run__pipeline_run_id",)
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("pipeline_run", "order")
