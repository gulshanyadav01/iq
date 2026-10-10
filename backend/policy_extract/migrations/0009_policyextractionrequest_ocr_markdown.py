from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("policy_extract", "0008_policyextractionrequest_error_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="policyextractionrequest",
            name="ocr_markdown",
            field=models.TextField(blank=True, default=""),
        ),
    ]
