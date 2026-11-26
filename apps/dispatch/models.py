from django.db import models
from django.db.models import Q

from apps.core.models import PublicIdModel, TimeStampedModel
from apps.shipments.models import Shipment, ShipmentStatus
from apps.tenancy.models import Branch, TenantScopedModel


class RouteStatus(models.TextChoices):
    """Route lifecycle (architecture §13). Part of the API contract (§25). `loading` is
    unused until warehouse loading scans exist (Phase 6)."""

    PLANNED = "planned", "Planned"
    ASSIGNED = "assigned", "Assigned"
    LOADING = "loading", "Loading"
    DISPATCHED = "dispatched", "Dispatched"
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class StopType(models.TextChoices):
    """`return` is added with the delivery workflow (Phase 5)."""

    PICKUP = "pickup", "Pickup"
    DELIVERY = "delivery", "Delivery"


class StopStatus(models.TextChoices):
    """Only `pending` is reachable until drivers work stops (Phase 5)."""

    PENDING = "pending", "Pending"
    ARRIVED = "arrived", "Arrived"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


# While a route is in one of these, its stops can change. Dispatched routes are changed
# only by reassignment (Phase 4 PR 3).
EDITABLE_STATUSES = (RouteStatus.PLANNED, RouteStatus.ASSIGNED)
# Statuses a route can be cancelled from: shipments are still `assigned` in all of them.
CANCELLABLE_STATUSES = (RouteStatus.PLANNED, RouteStatus.ASSIGNED, RouteStatus.DISPATCHED)


class Route(TenantScopedModel, PublicIdModel, TimeStampedModel):
    """A day's run from one branch. `version` is bumped by every change, so a dispatcher
    working from a stale view gets 409 instead of overwriting a colleague (architecture §20).
    """

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")
    route_date = models.DateField()
    status = models.CharField(
        max_length=16, choices=RouteStatus.choices, default=RouteStatus.PLANNED
    )
    # When true, stops must be worked in order (checked from Phase 5).
    sequence_enforced = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=RouteStatus.values), name="route_status_valid"
            ),
            models.CheckConstraint(
                condition=(
                    Q(status=RouteStatus.CANCELLED, cancelled_at__isnull=False)
                    | (~Q(status=RouteStatus.CANCELLED) & Q(cancelled_at__isnull=True))
                ),
                name="route_cancelled_at_iff_cancelled",
            ),
        ]
        indexes = [models.Index(fields=["company", "route_date"], name="route_company_date")]

    def __str__(self) -> str:
        return str(self.public_id)


class RouteStop(TenantScopedModel, PublicIdModel, TimeStampedModel):
    """One shipment's pickup or delivery on a route. A shipment can have both (a direct
    pickup-and-deliver route). `shipment_status_before` is the status the shipment had
    before the route took it (`created` or `sorted`), to which it returns when its last
    stop goes away (memory D-API-29)."""

    route = models.ForeignKey(Route, on_delete=models.PROTECT, related_name="stops")
    shipment = models.ForeignKey(Shipment, on_delete=models.PROTECT, related_name="+")
    type = models.CharField(max_length=16, choices=StopType.choices)
    sequence = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=StopStatus.choices, default=StopStatus.PENDING)
    window_start = models.DateTimeField(null=True, blank=True)
    window_end = models.DateTimeField(null=True, blank=True)
    shipment_status_before = models.CharField(max_length=32)

    class Meta:
        constraints = [
            # Deferred, so one transaction can renumber the stops of a route (reorder).
            models.UniqueConstraint(
                fields=["route", "sequence"],
                name="route_stop_unique_sequence",
                deferrable=models.Deferrable.DEFERRED,
            ),
            models.UniqueConstraint(
                fields=["route", "shipment", "type"], name="route_stop_unique_shipment_type"
            ),
            models.CheckConstraint(
                condition=Q(type__in=StopType.values), name="route_stop_type_valid"
            ),
            models.CheckConstraint(
                condition=Q(status__in=StopStatus.values), name="route_stop_status_valid"
            ),
            models.CheckConstraint(condition=Q(sequence__gte=1), name="route_stop_sequence_min"),
            models.CheckConstraint(
                condition=(
                    Q(window_start__isnull=True, window_end__isnull=True)
                    | Q(
                        window_start__isnull=False,
                        window_end__isnull=False,
                        window_end__gt=models.F("window_start"),
                    )
                ),
                name="route_stop_window_valid",
            ),
            models.CheckConstraint(
                condition=Q(
                    shipment_status_before__in=[ShipmentStatus.CREATED, ShipmentStatus.SORTED]
                ),
                name="route_stop_before_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.route_id}#{self.sequence}"
