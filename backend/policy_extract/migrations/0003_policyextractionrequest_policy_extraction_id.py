# Generated migration for policy_extraction_id field
# Uses 3-step approach to avoid unique constraint violation on existing rows.

from django.db import migrations, models
import policy_extract.models.policy_extraction_request


def _populate_policy_extraction_ids(apps, schema_editor):
    PolicyExtractionRequest = apps.get_model('policy_extract', 'PolicyExtractionRequest')
    for req in PolicyExtractionRequest.objects.all():
        req.policy_extraction_id = policy_extract.models.policy_extraction_request._generate_policy_extraction_id()
        req.save(update_fields=['policy_extraction_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('policy_extract', '0002_alter_policyextractionrequest_file'),
    ]

    operations = [
        # Step 1: add the column without unique constraint, nullable so existing rows don't need a value yet
        migrations.AddField(
            model_name='policyextractionrequest',
            name='policy_extraction_id',
            field=models.CharField(max_length=32, null=True, editable=False),
        ),
        # Step 2: backfill unique values for all existing rows
        migrations.RunPython(_populate_policy_extraction_ids, migrations.RunPython.noop),
        # Step 3: make the column non-nullable, unique, and indexed
        migrations.AlterField(
            model_name='policyextractionrequest',
            name='policy_extraction_id',
            field=models.CharField(
                db_index=True,
                default=policy_extract.models.policy_extraction_request._generate_policy_extraction_id,
                editable=False,
                max_length=32,
                unique=True,
            ),
        ),
    ]
