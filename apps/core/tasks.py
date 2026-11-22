from celery import shared_task

from apps.core.idempotency import purge_expired_records


@shared_task
def purge_idempotency_records() -> None:
    """Daily: drop idempotency records past the 72-hour retention window."""
    purge_expired_records()
