from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("policy_extract", "0005_remove_file_field"),
    ]

    operations = [
        migrations.AddField(
            model_name="policyextractionrequest",
            name="callback_response",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
