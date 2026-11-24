import logging

from celery import shared_task
from django.utils import timezone

from apps.core import storage
from apps.core.errors import DomainError
from apps.uploads.constants import UNATTACHED_RETENTION
from apps.uploads.models import Upload

logger = logging.getLogger(__name__)


@shared_task
def purge_unattached_uploads() -> int:
    """Hourly: delete uploads nobody attached within 24 h, object first and then row
    (architecture §19). Safe to run twice; a storage failure leaves the row for the next
    run instead of orphaning the object."""
    cutoff = timezone.now() - UNATTACHED_RETENTION
    purged = 0
    for upload in Upload.objects.filter(attached_at__isnull=True, created_at__lt=cutoff).iterator():
        try:
            storage.delete(upload.object_key)
        except DomainError:
            logger.warning("Could not delete object of upload %s; will retry", upload.public_id)
            continue
        upload.delete()
        purged += 1
    return purged
