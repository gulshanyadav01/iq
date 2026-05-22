"""Rename the seeded demo to a product-shape title ('Unstructured → Structured')."""

from django.db import migrations


NEW_NAME = "Unstructured → Structured"
OLD_NAME = "PDF Field Extraction"


def rename(apps, schema_editor):
    Pipeline = apps.get_model("pipelines", "Pipeline")
    Pipeline.objects.filter(slug="pdf-field-extraction").update(name=NEW_NAME)


def revert(apps, schema_editor):
    Pipeline = apps.get_model("pipelines", "Pipeline")
    Pipeline.objects.filter(slug="pdf-field-extraction").update(name=OLD_NAME)


class Migration(migrations.Migration):
    dependencies = [
        ("pipelines", "0002_seed_pdf_extraction_pipeline"),
    ]

    operations = [
        migrations.RunPython(rename, reverse_code=revert),
    ]
