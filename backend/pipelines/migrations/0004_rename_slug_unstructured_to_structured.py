"""Rename the seeded demo's slug + name to product-shape identifiers."""

from django.db import migrations


OLD_SLUG = "pdf-field-extraction"
NEW_SLUG = "unstructured-to-structured"
NEW_NAME = "Unstructured → Structured"
OLD_NAME = "PDF Field Extraction"


def rename(apps, schema_editor):
    Pipeline = apps.get_model("pipelines", "Pipeline")
    Pipeline.objects.filter(slug=OLD_SLUG).update(slug=NEW_SLUG, name=NEW_NAME)


def revert(apps, schema_editor):
    Pipeline = apps.get_model("pipelines", "Pipeline")
    Pipeline.objects.filter(slug=NEW_SLUG).update(slug=OLD_SLUG, name=OLD_NAME)


class Migration(migrations.Migration):
    dependencies = [
        ("pipelines", "0003_rename_pdf_extraction_pipeline"),
    ]

    operations = [
        migrations.RunPython(rename, reverse_code=revert),
    ]
