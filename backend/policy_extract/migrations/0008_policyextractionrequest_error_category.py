from django.db import migrations, models

from policy_extract.services.error_classification import classify_error


def backfill_error_category(apps, schema_editor):
    # Classify every already-failed row's free-text error into a bucket so the
    # new breakdown covers history, not just failures from here on. The helper is
    # a pure function with no Django/model deps, safe to call from a migration.
    PolicyExtractionRequest = apps.get_model(
        "policy_extract", "PolicyExtractionRequest"
    )
    failed = PolicyExtractionRequest.objects.filter(status="failed")
    for req in failed.iterator():
        category = classify_error(req.error)
        if req.error_category != category:
            req.error_category = category
            req.save(update_fields=["error_category"])


def noop_reverse(apps, schema_editor):
    # Field is dropped by the reverse of AddField; nothing to undo here.
    pass


class Migration(migrations.Migration):
    dependencies = [
        (
            "policy_extract",
            "0007_policyextractionrequest_pext_filename_created_idx_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="policyextractionrequest",
            name="error_category",
            field=models.CharField(
                blank=True,
                choices=[
                    ("expired_link", "Expired / invalid link"),
                    ("unsupported_file", "Unsupported file type"),
                    ("missing_source", "Missing source"),
                    ("timeout", "Timed out"),
                    ("download_failed", "Download failed"),
                    ("parser_failed", "Extraction failed"),
                    ("other", "Other"),
                ],
                db_index=True,
                max_length=32,
                null=True,
            ),
        ),
        migrations.RunPython(backfill_error_category, noop_reverse),
    ]
