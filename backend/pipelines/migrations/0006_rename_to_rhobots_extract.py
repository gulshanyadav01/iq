"""Rename the demo pipeline to its product identity: Rhobots Extract."""

from django.db import migrations


OLD_SLUG = "unstructured-to-structured"
OLD_NAME = "Unstructured → Structured"
NEW_SLUG = "rhobots-extract"
NEW_NAME = "Rhobots Extract"


def rename(apps, schema_editor):
    Pipeline = apps.get_model("pipelines", "Pipeline")
    Pipeline.objects.filter(slug=OLD_SLUG).update(slug=NEW_SLUG, name=NEW_NAME)


def revert(apps, schema_editor):
    Pipeline = apps.get_model("pipelines", "Pipeline")
    Pipeline.objects.filter(slug=NEW_SLUG).update(slug=OLD_SLUG, name=OLD_NAME)


class Migration(migrations.Migration):
    dependencies = [
        ("pipelines", "0005_merge_20260519_1617"),
    ]

    operations = [
        migrations.RunPython(rename, reverse_code=revert),
    ]
