import uuid
from datetime import datetime

from django.db.models import Prefetch, Q, QuerySet
from django.shortcuts import get_object_or_404

from apps.accounts.models import User
from apps.accounts.permissions import get_user_permissions
from apps.shipments.models import Shipment, ShipmentAddress, ShipmentPriority, ShipmentStatus
from apps.shipments.permissions import SHIPMENTS_ALL_BRANCHES
from apps.tenancy.models import Branch


def branches_for_user(user: User) -> QuerySet[Branch]:
    """Branches whose shipments `user` may see and create (architecture §8): every branch
    of the company with `shipments.all_branches`, otherwise the user's own branches."""
    # ponytail: shipments only for now; generalise when routes need branch scoping (Phase 4).
    branches = Branch.objects.for_company(user.company)
    if SHIPMENTS_ALL_BRANCHES in get_user_permissions(user):
        return branches
    return branches.filter(users=user)


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
