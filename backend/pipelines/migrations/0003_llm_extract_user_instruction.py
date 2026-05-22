"""Split LLM_EXTRACT prompt into fixed system contract + free-form user instruction.

The earlier shape (a single ``prompt`` field containing the full template) made
the textarea unusable for short natural-language instructions: typing
"only invoice and biller" silently dropped the JSON-shape contract AND the
``{{markdown_with_block_markers}}`` placeholder, so Gemini was called with no
document attached. The new shape stores only the user-supplied refinement;
the system contract is hardcoded in ``pipelines.steps.llm_extract``.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    PipelineStep = apps.get_model("pipelines", "PipelineStep")
    for step in PipelineStep.objects.filter(step_type="LLM_EXTRACT"):
        cfg = dict(step.config or {})
        cfg.pop("prompt", None)
        cfg.setdefault("user_instruction", "")
        cfg.setdefault("model", "gemini-2.5-flash-lite")
        step.config = cfg
        step.save(update_fields=["config"])


def backwards(apps, schema_editor):
    PipelineStep = apps.get_model("pipelines", "PipelineStep")
    for step in PipelineStep.objects.filter(step_type="LLM_EXTRACT"):
        cfg = dict(step.config or {})
        cfg.pop("user_instruction", None)
        step.config = cfg
        step.save(update_fields=["config"])


class Migration(migrations.Migration):
    dependencies = [
        ("pipelines", "0002_seed_pdf_extraction_pipeline"),
    ]

    operations = [
        migrations.RunPython(forwards, reverse_code=backwards),
    ]
