# Generated migration for source_url field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('policy_extract', '0003_policyextractionrequest_policy_extraction_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='policyextractionrequest',
            name='source_url',
            field=models.URLField(blank=True, null=True),
        ),
    ]
