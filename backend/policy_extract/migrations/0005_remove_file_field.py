# Generated migration to remove file field and standardize on source_url

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('policy_extract', '0004_policyextractionrequest_source_url'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='policyextractionrequest',
            name='file',
        ),
    ]
