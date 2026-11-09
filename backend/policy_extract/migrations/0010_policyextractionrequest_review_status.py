from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("policy_extract", "0009_policyextractionrequest_ocr_markdown"),
    ]

    operations = [
        migrations.AddField(
            model_name="policyextractionrequest",
            name="review_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("auto_approved", "Auto-approved"),
                    ("needs_review", "Needs review"),
                    ("approved", "Approved"),
                ],
                db_index=True,
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="policyextractionrequest",
            name="reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
