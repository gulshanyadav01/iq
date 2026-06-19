"""Seed the v1 demo pipeline: PDF Field Extraction (PDF_TO_MD → LLM_EXTRACT)."""

from django.db import migrations


def seed(apps, schema_editor):
    Pipeline = apps.get_model("pipelines", "Pipeline")
    PipelineStep = apps.get_model("pipelines", "PipelineStep")

    pipeline, _ = Pipeline.objects.update_or_create(
        slug="pdf-field-extraction",
        defaults={
            "name": "PDF Field Extraction",
            "description": (
                "Upload a PDF, watch it convert to Markdown alongside the original, "
                "then run a prompt to extract structured fields with citations back "
                "to the source region."
            ),
            "is_seeded": True,
        },
    )

    PipelineStep.objects.update_or_create(
        pipeline=pipeline,
        order=0,
        defaults={
            "step_type": "PDF_TO_MD",
            "name": "Convert PDF to Markdown",
            "config": {},
        },
    )
    PipelineStep.objects.update_or_create(
        pipeline=pipeline,
        order=1,
        defaults={
            "step_type": "LLM_EXTRACT",
            "name": "Extract Fields with Prompt",
            "config": {
                "user_instruction": "",
                "model": "gemini-3.1-flash-lite",
            },
        },
    )


def unseed(apps, schema_editor):
    Pipeline = apps.get_model("pipelines", "Pipeline")
    Pipeline.objects.filter(slug="pdf-field-extraction").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("pipelines", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, reverse_code=unseed),
    ]
