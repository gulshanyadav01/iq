from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models.commission_document import CommissionDocument


@receiver(post_delete, sender=CommissionDocument)
def delete_document_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)
