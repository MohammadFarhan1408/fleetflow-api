import logging

from celery import shared_task

from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.shipments import selectors

logger = logging.getLogger(__name__)


@shared_task
def check_status_consistency() -> int:
    """Nightly: report shipments whose status disagrees with their latest status event
    (architecture §12). Logs and audits each one; never fixes anything automatically."""
    mismatches = 0
    for shipment in selectors.status_mismatches().iterator(chunk_size=500):
        mismatches += 1
        # Annotated by the selector; django-stubs cannot see query annotations.
        last_event_status: str | None = shipment.last_event_status  # type: ignore[attr-defined]
        logger.warning(
            "Shipment status mismatch",
            extra={
                "company_id": shipment.company_id,
                "shipment": str(shipment.public_id),
                "status": shipment.status,
                "last_event_status": last_event_status,
            },
        )
        audit.record(
            AuditAction.SHIPMENT_STATUS_MISMATCH,
            company=shipment.company,
            object_type="shipment",
            object_id=str(shipment.public_id),
            after={"status": shipment.status, "last_event_status": last_event_status},
        )
    return mismatches
