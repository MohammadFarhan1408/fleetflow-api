import uuid
from datetime import datetime

from django.db.models import F, OuterRef, Prefetch, Q, QuerySet, Subquery
from django.shortcuts import get_object_or_404

from apps.accounts.models import User
from apps.accounts.permissions import get_user_permissions
from apps.accounts.selectors import branches_in_scope
from apps.shipments.models import (
    STATUS_EVENT_TYPES,
    AddressRole,
    Shipment,
    ShipmentAddress,
    ShipmentPriority,
    ShipmentStatus,
    TrackingEvent,
)
from apps.shipments.permissions import SHIPMENTS_ALL_BRANCHES
from apps.tenancy.models import Branch, CompanyStatus


def branches_for_user(user: User) -> QuerySet[Branch]:
    """Branches whose shipments `user` may see and create (architecture §8)."""
    return branches_in_scope(user, SHIPMENTS_ALL_BRANCHES)


def shipments_for_user(user: User) -> QuerySet[Shipment]:
    """Tenant- and branch-scoped shipments. Every shipment read starts here."""
    shipments = Shipment.objects.for_company(user.company)
    if SHIPMENTS_ALL_BRANCHES in get_user_permissions(user):
        return shipments
    return shipments.filter(branch__in=user.branches.values("pk"))


def list_shipments(
    user: User,
    *,
    status: list[ShipmentStatus] | None = None,
    branch: uuid.UUID | None = None,
    customer: uuid.UUID | None = None,
    service_type: str = "",
    priority: ShipmentPriority | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    search: str = "",
) -> QuerySet[Shipment]:
    shipments = shipments_for_user(user).select_related("branch", "customer", "service_type")
    if status:
        shipments = shipments.filter(status__in=status)
    if branch is not None:
        shipments = shipments.filter(branch__public_id=branch)
    if customer is not None:
        shipments = shipments.filter(customer__public_id=customer)
    if service_type:
        shipments = shipments.filter(service_type__code=service_type)
    if priority is not None:
        shipments = shipments.filter(priority=priority)
    if created_after is not None:
        shipments = shipments.filter(created_at__gte=created_after)
    if created_before is not None:
        shipments = shipments.filter(created_at__lt=created_before)
    if search:
        shipments = shipments.filter(
            Q(tracking_number__istartswith=search) | Q(external_reference__istartswith=search)
        )
    return shipments


def get_shipment(user: User, public_id: uuid.UUID) -> Shipment:
    """One visible shipment with everything the detail response shows; 404 otherwise."""
    shipments = (
        shipments_for_user(user)
        .select_related("branch", "customer", "service_type")
        .prefetch_related(
            "items",
            Prefetch("addresses", ShipmentAddress.objects.select_related("source_address")),
        )
    )
    return get_object_or_404(shipments, public_id=public_id)


def timeline(user: User, shipment_id: uuid.UUID) -> QuerySet[TrackingEvent]:
    """A visible shipment's events, internal ones included. 404 if not visible. Ordered
    by the pagination class (occurred_at, then recorded_at)."""
    shipment = get_object_or_404(shipments_for_user(user).only("pk"), public_id=shipment_id)
    return (
        TrackingEvent.objects.for_company(user.company)
        .filter(shipment=shipment)
        .select_related("actor")
    )


def get_public_shipment(token: str) -> Shipment:
    """The shipment a public tracking token opens, with only what the public page shows.

    The token is the credential, so this is deliberately not tenant-scoped. Drafts and
    suspended companies' shipments answer 404, like unknown or rotated tokens.
    """
    shipments = (
        Shipment.objects.exclude(status=ShipmentStatus.DRAFT)
        .filter(company__status=CompanyStatus.ACTIVE)
        .select_related("company", "service_type")
        .prefetch_related(
            Prefetch(
                "addresses",
                ShipmentAddress.objects.filter(role=AddressRole.DELIVERY).only(
                    "shipment_id", "city", "country"
                ),
                to_attr="delivery_addresses",
            ),
            Prefetch(
                "events",
                TrackingEvent.objects.filter(public=True).order_by("occurred_at", "recorded_at"),
                to_attr="public_events",
            ),
        )
    )
    return get_object_or_404(shipments, tracking_token=token)


def status_mismatches() -> QuerySet[Shipment]:
    """Live shipments whose status differs from their latest recorded status event
    (architecture §12), annotated with `last_event_status` (None if they have none).

    Operator job across every company, so it is not tenant-scoped. "Latest" is the last
    one recorded, the order in which statuses were applied; late offline events may carry
    an earlier `occurred_at`.
    """
    last_status = (
        TrackingEvent.objects.filter(shipment=OuterRef("pk"), type__in=STATUS_EVENT_TYPES)
        .order_by("-recorded_at", "-pk")
        .values("status_after")[:1]
    )
    return (
        Shipment.objects.exclude(status=ShipmentStatus.DRAFT)
        .annotate(last_event_status=Subquery(last_status))
        .filter(Q(last_event_status__isnull=True) | ~Q(status=F("last_event_status")))
        .select_related("company")
    )
