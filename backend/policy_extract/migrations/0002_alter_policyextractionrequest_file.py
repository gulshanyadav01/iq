# Generated migration for file field nullability

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('policy_extract', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='policyextractionrequest',
            name='file',
            field=models.FileField(blank=True, null=True, upload_to='policy_extractions/%Y/%m/%d/'),
        ),
    ]
