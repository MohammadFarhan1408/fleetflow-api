"""Shipment writes (architecture §5, §11). Views pass the acting user; every service
re-checks tenant and branch scope (rules.md §3). After creation, `status` changes only
through `transition` and `correct_status`."""

import secrets
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal

import psycopg
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.permissions import get_user_permissions
from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.core.errors import DomainError, ErrorCode, invalid_reference
from apps.customers.models import Address, Customer, GeocodeConfidence
from apps.customers.services import AddressFields
from apps.shipments import selectors
from apps.shipments.constants import MAX_TOTAL_WEIGHT_KG
from apps.shipments.models import (
    ALLOWED_TRANSITIONS,
    SERVICE_ONLY_EDGES,
    AddressRole,
    BranchTrackingSequence,
    EventSource,
    Shipment,
    ShipmentAddress,
    ShipmentItem,
    ShipmentPriority,
    ShipmentStatus,
    TrackingEvent,
    TrackingEventType,
)
from apps.shipments.permissions import (
    SHIPMENTS_CANCEL,
    SHIPMENTS_CORRECT_STATUS,
    SHIPMENTS_CREATE,
    SHIPMENTS_RECOVER,
)
from apps.tenancy.models import Branch, Company, ServiceType

# An address is copied from the address book (its public ID), given inline, or, on a
# draft edit that does not touch it, the existing snapshot kept as is.
type AddressSpec = uuid.UUID | AddressFields | ShipmentAddress


@dataclass(frozen=True)
class ItemFields:
    weight_kg: Decimal
    length_cm: Decimal
    width_cm: Decimal
    height_cm: Decimal
    description: str = ""


@dataclass(frozen=True)
class ShipmentFields:
    branch_id: uuid.UUID
    customer_id: uuid.UUID
    service_type: str
    pickup: AddressSpec
    delivery: AddressSpec
    items: tuple[ItemFields, ...]
    priority: ShipmentPriority = ShipmentPriority.NORMAL
    scheduled_pickup_date: date | None = None
    external_reference: str = ""

    @classmethod
    def of(cls, shipment: Shipment) -> ShipmentFields:
        """A draft's current values, so a PATCH can replace only what it sends."""
        snapshots = {snapshot.role: snapshot for snapshot in shipment.addresses.all()}
        return cls(
            branch_id=shipment.branch.public_id,
            customer_id=shipment.customer.public_id,
            service_type=shipment.service_type.code,
            pickup=snapshots[AddressRole.PICKUP],
            delivery=snapshots[AddressRole.DELIVERY],
            items=tuple(
                ItemFields(
                    weight_kg=item.weight_kg,
                    length_cm=item.length_cm,
                    width_cm=item.width_cm,
                    height_cm=item.height_cm,
                    description=item.description,
                )
                for item in shipment.items.all()
            ),
            priority=ShipmentPriority(shipment.priority),
            scheduled_pickup_date=shipment.scheduled_pickup_date,
            external_reference=shipment.external_reference,
        )


@dataclass(frozen=True)
class _Resolved:
    branch: Branch
    customer: Customer
    service_type: ServiceType
    total_weight_kg: Decimal


def new_tracking_token() -> str:
    return secrets.token_urlsafe(16)  # 128 bits (architecture §7)


@transaction.atomic
def create_shipment(
    *, actor: User, fields: ShipmentFields, draft: bool, request: HttpRequest
) -> Shipment:
    """Create a shipment in `created` (with its first TrackingEvent) or in `draft`.

    Drafts get a tracking number but no event: they are not live until confirmed, so
    deleting one never touches the append-only history.
    """
    resolved = _resolve(actor, fields)
    status = ShipmentStatus.DRAFT if draft else ShipmentStatus.CREATED
    shipment = Shipment(company=actor.company, status=status, tracking_token=new_tracking_token())
    _apply(shipment, fields, resolved)
    shipment.tracking_number = _next_tracking_number(resolved.branch)
    _save(shipment)
    _save_contents(shipment, fields)
    if not draft:
        _append_status_event(shipment, actor, TrackingEventType.STATUS_CHANGE)
    _audit(AuditAction.SHIPMENT_CREATED, actor, shipment, request, after={"status": status})
    return shipment


@transaction.atomic
def update_draft(*, actor: User, shipment: Shipment, fields: ShipmentFields) -> Shipment:
    """Replace a draft's contents. Moving it to another branch issues a new tracking
    number; items are always rewritten, so barcodes follow the current number."""
    shipment = _lock_visible(actor, shipment)
    if shipment.status != ShipmentStatus.DRAFT:
        raise DomainError(ErrorCode.SHIPMENT_NOT_DRAFT)
    resolved = _resolve(actor, fields)
    if resolved.branch.pk != shipment.branch_id:
        shipment.tracking_number = _next_tracking_number(resolved.branch)
    _apply(shipment, fields, resolved)
    _save(shipment)
    _save_contents(shipment, fields)
    return shipment


@transaction.atomic
def delete_draft(*, actor: User, shipment: Shipment, request: HttpRequest) -> None:
    """Hard-delete a draft with its items and address copies (architecture §10)."""
    shipment = _lock_visible(actor, shipment)
    if shipment.status != ShipmentStatus.DRAFT:
        raise DomainError(ErrorCode.SHIPMENT_NOT_DRAFT)
    _audit(AuditAction.SHIPMENT_DRAFT_DELETED, actor, shipment, request)
    shipment.delete()


@transaction.atomic
def rotate_tracking_token(*, actor: User, shipment: Shipment, request: HttpRequest) -> Shipment:
    """Issue a new public tracking token; links built on the old one stop working."""
    shipment = _lock_visible(actor, shipment)
    shipment.tracking_token = new_tracking_token()
    shipment.save(update_fields=["tracking_token", "updated_at"])
    _audit(AuditAction.SHIPMENT_TRACKING_TOKEN_ROTATED, actor, shipment, request)
    return shipment


# --- Status changes (architecture §11) ---------------------------------------------------

_S = ShipmentStatus
# Who may take each edge. Edges not listed are denied unless the calling service passes
# `authorized_by_caller=True`: later phases (warehouse scans, routes, POD) reach them only
# from services that authorize the action and check its preconditions themselves.
EDGE_PERMISSIONS: dict[tuple[ShipmentStatus, ShipmentStatus], str] = {
    (_S.DRAFT, _S.CREATED): SHIPMENTS_CREATE,
    (_S.CANCELLED, _S.CREATED): SHIPMENTS_RECOVER,
    **{
        (origin, _S.CANCELLED): SHIPMENTS_CANCEL
        for origin, targets in ALLOWED_TRANSITIONS.items()
        if _S.CANCELLED in targets and (origin, _S.CANCELLED) not in SERVICE_ONLY_EDGES
    },
}


@transaction.atomic
def transition(
    *,
    actor: User,
    shipment: Shipment,
    to: ShipmentStatus,
    expected_status: ShipmentStatus,
    reason: str = "",
    request: HttpRequest,
    authorized_by_caller: bool = False,
    source: EventSource = EventSource.ADMIN,
    scope: QuerySet[Shipment] | None = None,
) -> Shipment:
    """Move a shipment along one edge of the lifecycle and append its status event.

    The row is locked and `expected_status` must still hold, so of two concurrent changes
    exactly one wins and the other gets `shipment.status_conflict`.

    `scope` replaces the actor's branch scope for callers that have already proven the
    shipment visible another way (a driver working a route they were assigned has no
    shipment branches); it must still be a company-scoped queryset. `source` is the event's
    origin.
    """
    shipment = _lock_visible(actor, shipment, scope)
    current = _check_expected(shipment, expected_status)
    if to not in ALLOWED_TRANSITIONS[current]:
        raise DomainError(ErrorCode.SHIPMENT_INVALID_TRANSITION)
    if (current, to) in SERVICE_ONLY_EDGES and not authorized_by_caller:
        raise DomainError(ErrorCode.SHIPMENT_INVALID_TRANSITION)
    permission = EDGE_PERMISSIONS.get((current, to))
    if permission is None:
        if not authorized_by_caller:  # deny by default
            raise DomainError(ErrorCode.PERMISSION_DENIED)
    elif permission not in get_user_permissions(actor):
        raise DomainError(ErrorCode.PERMISSION_DENIED)
    if current == _S.DRAFT:
        # A draft may have gone stale (archived customer, retired service, past date).
        _resolve(actor, ShipmentFields.of(shipment))

    shipment.status = to
    shipment.save(update_fields=["status", "updated_at"])
    _append_status_event(
        shipment, actor, TrackingEventType.STATUS_CHANGE, reason=reason, source=source
    )
    if current == _S.CANCELLED:
        _audit(
            AuditAction.SHIPMENT_RECOVERED,
            actor,
            shipment,
            request,
            after={"status": to},
        )
    return shipment


@transaction.atomic
def correct_status(
    *,
    actor: User,
    shipment: Shipment,
    to: ShipmentStatus,
    expected_status: ShipmentStatus,
    reason: str,
    request: HttpRequest,
) -> Shipment:
    """Admin override to any live status, including out of `delivered`. Appends a
    non-public correction event; earlier history is never rewritten."""
    if SHIPMENTS_CORRECT_STATUS not in get_user_permissions(actor):
        raise DomainError(ErrorCode.PERMISSION_DENIED)
    shipment = _lock_visible(actor, shipment)
    current = _check_expected(shipment, expected_status)
    # Drafts are not live (confirm them instead), and a shipment never becomes one again.
    if _S.DRAFT in (current, to) or to == current:
        raise DomainError(ErrorCode.SHIPMENT_INVALID_TRANSITION)

    shipment.status = to
    shipment.save(update_fields=["status", "updated_at"])
    _append_status_event(shipment, actor, TrackingEventType.CORRECTION, reason=reason, public=False)
    audit.record(
        AuditAction.SHIPMENT_STATUS_CORRECTED,
        company=actor.company,
        actor=actor,
        object_type="shipment",
        object_id=str(shipment.public_id),
        before={"status": current},
        after={"status": to},
        request=request,
    )
    return shipment


def _check_expected(shipment: Shipment, expected_status: ShipmentStatus) -> ShipmentStatus:
    current = ShipmentStatus(shipment.status)
    if current != expected_status:
        raise DomainError(
            ErrorCode.SHIPMENT_STATUS_CONFLICT,
            detail=f"The shipment is {current}, not {expected_status}.",
        )
    return current


def _append_status_event(
    shipment: Shipment,
    actor: User,
    event_type: TrackingEventType,
    *,
    reason: str = "",
    public: bool = True,
    source: EventSource = EventSource.ADMIN,
) -> None:
    # Same transaction as the status write, so the two can never disagree (§12).
    TrackingEvent.objects.create(
        company_id=shipment.company_id,
        shipment=shipment,
        type=event_type,
        status_after=shipment.status,
        occurred_at=timezone.now(),
        actor=actor,
        source=source,
        reason=reason,
        public=public,
    )


def _lock_visible(
    actor: User, shipment: Shipment, scope: QuerySet[Shipment] | None = None
) -> Shipment:
    # Defence in depth: views resolve the shipment through the scoped selector, but the
    # service must not rely on that. Same answer as a missing shipment.
    visible = scope if scope is not None else selectors.shipments_for_user(actor)
    locked = visible.select_for_update().filter(pk=shipment.pk).first()
    if locked is None:
        raise DomainError(ErrorCode.NOT_FOUND)
    return locked


def _resolve(actor: User, fields: ShipmentFields) -> _Resolved:
    company = actor.company
    branch = selectors.branches_for_user(actor).filter(public_id=fields.branch_id).first()
    if branch is None:
        raise invalid_reference("branch_id", "Branch not found.")
    customer = Customer.objects.for_company(company).filter(public_id=fields.customer_id).first()
    if customer is None:
        raise invalid_reference("customer_id", "Customer not found.")
    if customer.is_archived:
        raise DomainError(
            ErrorCode.CUSTOMER_INACTIVE, errors={"customer_id": ["Customer is archived."]}
        )
    service_type = (
        ServiceType.objects.for_company(company)
        .filter(code=fields.service_type, is_active=True)
        .first()
    )
    if service_type is None:
        raise DomainError(
            ErrorCode.SHIPMENT_UNSUPPORTED_TYPE,
            errors={"service_type": [f"Unknown or inactive service type {fields.service_type!r}."]},
        )
    total_weight_kg = _check_items(service_type, fields.items)
    _check_schedule(company, fields.scheduled_pickup_date)
    return _Resolved(branch, customer, service_type, total_weight_kg)


def _check_items(service_type: ServiceType, items: tuple[ItemFields, ...]) -> Decimal:
    """Validate parcels against the weight rules and the service type's limits and
    return the total weight. Errors are keyed by item index."""
    not_positive = {
        str(index): {"weight_kg": ["Must be greater than 0."]}
        for index, item in enumerate(items)
        if item.weight_kg <= 0
    }
    if not_positive:
        raise DomainError(ErrorCode.SHIPMENT_INVALID_WEIGHT, errors={"items": not_positive})
    total = sum((item.weight_kg for item in items), Decimal(0))
    if total > MAX_TOTAL_WEIGHT_KG:
        raise DomainError(
            ErrorCode.SHIPMENT_INVALID_WEIGHT,
            errors={"items": [f"Total weight must not exceed {MAX_TOTAL_WEIGHT_KG} kg."]},
        )

    limits = {
        "weight_kg": service_type.max_weight_kg,
        "length_cm": service_type.max_length_cm,
        "width_cm": service_type.max_width_cm,
        "height_cm": service_type.max_height_cm,
    }
    over_limit: dict[str, dict[str, list[str]]] = {}
    for index, item in enumerate(items):
        exceeded = {
            name: [f"Exceeds the {service_type.code} limit of {limit}."]
            for name, limit in limits.items()
            if getattr(item, name) > limit
        }
        if exceeded:
            over_limit[str(index)] = exceeded
    if over_limit:
        raise DomainError(ErrorCode.VALIDATION_INVALID, errors={"items": over_limit})
    return total


def _check_schedule(company: Company, scheduled: date | None) -> None:
    # "Today" is the company's business day, not the server's (architecture §18).
    if scheduled is not None and scheduled < company.local_today():
        raise DomainError(
            ErrorCode.SHIPMENT_PAST_SCHEDULE,
            errors={"scheduled_pickup_date": ["Must be today or later."]},
        )


def _apply(shipment: Shipment, fields: ShipmentFields, resolved: _Resolved) -> None:
    shipment.branch = resolved.branch
    shipment.customer = resolved.customer
    shipment.service_type = resolved.service_type
    shipment.total_weight_kg = resolved.total_weight_kg
    shipment.priority = fields.priority
    shipment.scheduled_pickup_date = fields.scheduled_pickup_date
    shipment.external_reference = fields.external_reference


def _next_tracking_number(branch: Branch) -> str:
    """`FF-<BRANCH>-<seq>` from a per-branch counter. The counter row stays locked until
    the transaction ends, so concurrent creations in one branch get distinct numbers."""
    # ponytail: serializes shipment creation per branch; fine at dispatcher scale, switch
    # to a PostgreSQL sequence per branch if bulk imports (Phase 6) contend here.
    BranchTrackingSequence.objects.bulk_create(
        [BranchTrackingSequence(branch=branch)], ignore_conflicts=True
    )
    sequence = BranchTrackingSequence.objects.select_for_update().get(branch=branch)
    sequence.last_value += 1
    sequence.save(update_fields=["last_value"])
    return f"FF-{branch.code}-{sequence.last_value:06d}"


def _save(shipment: Shipment) -> None:
    try:
        with transaction.atomic():
            shipment.save()
    except IntegrityError as exc:
        cause = exc.__cause__
        if (
            isinstance(cause, psycopg.errors.UniqueViolation)
            and cause.diag.constraint_name == "shipment_unique_external_reference"
        ):
            raise DomainError(
                ErrorCode.SHIPMENT_DUPLICATE_REFERENCE,
                errors={"external_reference": ["Already used by another shipment."]},
            ) from exc
        raise


def _save_contents(shipment: Shipment, fields: ShipmentFields) -> None:
    for role, spec in (
        (AddressRole.PICKUP, fields.pickup),
        (AddressRole.DELIVERY, fields.delivery),
    ):
        if isinstance(spec, ShipmentAddress):
            continue  # unchanged on this draft edit
        snapshot = _snapshot(shipment, role, spec)
        shipment.addresses.filter(role=role).delete()
        snapshot.save()

    shipment.items.all().delete()
    ShipmentItem.objects.bulk_create(
        ShipmentItem(
            company_id=shipment.company_id,
            shipment=shipment,
            barcode=f"{shipment.tracking_number}-{number:03d}",
            **asdict(item),
        )
        for number, item in enumerate(fields.items, start=1)
    )


def _snapshot(
    shipment: Shipment, role: AddressRole, spec: uuid.UUID | AddressFields
) -> ShipmentAddress:
    source: Address | None = None
    if isinstance(spec, uuid.UUID):
        source = (
            Address.objects.for_company(shipment.company)
            .filter(public_id=spec, archived_at__isnull=True)
            .select_related("customer")
            .first()
        )
        if source is None:
            raise invalid_reference(role, "Address not found.", field="address_id")
        # An address-book entry may have no contact; its customer is the contact then.
        spec = replace(
            AddressFields.of(source), contact_name=source.contact_name or source.customer.name
        )
    copied = asdict(spec)
    del copied["label"]  # address-book only
    return ShipmentAddress(
        company_id=shipment.company_id,
        shipment=shipment,
        role=role,
        source_address=source,
        geocode_confidence=GeocodeConfidence.for_location(spec.location),
        **copied,
    )


def _audit(
    action: AuditAction,
    actor: User,
    shipment: Shipment,
    request: HttpRequest,
    after: dict[str, str] | None = None,
) -> None:
    # IDs and enums only: addresses, names and the tracking token stay out of the log.
    audit.record(
        action,
        company=actor.company,
        actor=actor,
        object_type="shipment",
        object_id=str(shipment.public_id),
        after=after,
        request=request,
    )
