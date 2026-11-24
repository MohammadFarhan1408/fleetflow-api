from django.conf import settings
from django.db import models

from apps.core.models import AppendOnlyModel, AppendOnlyQuerySet, PublicIdModel, TimeStampedModel
from apps.customers.models import Address, Customer, PostalAddress
from apps.shipments.constants import MAX_TOTAL_WEIGHT_KG
from apps.tenancy.models import Branch, ServiceType, TenantQuerySet, TenantScopedModel


class ShipmentStatus(models.TextChoices):
    """Canonical shipment statuses (architecture §11). Part of the API contract (§25)."""

    DRAFT = "draft", "Draft"
    CREATED = "created", "Created"
    RECEIVED_AT_WAREHOUSE = "received_at_warehouse", "Received at warehouse"
    SORTED = "sorted", "Sorted"
    ASSIGNED = "assigned", "Assigned"
    LOADED = "loaded", "Loaded"
    OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
    DELIVERED = "delivered", "Delivered"
    DELIVERY_FAILED = "delivery_failed", "Delivery failed"
    RETURNING = "returning", "Returning"
    RETURNED_TO_SENDER = "returned_to_sender", "Returned to sender"
    CANCELLED = "cancelled", "Cancelled"


_S = ShipmentStatus

# Every edge of the lifecycle diagram (architecture §11). No status leads to itself, so a
# repeated transition without an idempotency key is a conflict, never a duplicate event.
ALLOWED_TRANSITIONS: dict[ShipmentStatus, frozenset[ShipmentStatus]] = {
    _S.DRAFT: frozenset({_S.CREATED}),
    _S.CREATED: frozenset({_S.RECEIVED_AT_WAREHOUSE, _S.ASSIGNED, _S.CANCELLED}),
    _S.RECEIVED_AT_WAREHOUSE: frozenset({_S.SORTED}),
    _S.SORTED: frozenset({_S.ASSIGNED, _S.CANCELLED}),
    _S.ASSIGNED: frozenset({_S.LOADED, _S.OUT_FOR_DELIVERY, _S.CANCELLED}),
    _S.LOADED: frozenset({_S.OUT_FOR_DELIVERY}),
    _S.OUT_FOR_DELIVERY: frozenset({_S.DELIVERED, _S.DELIVERY_FAILED}),
    _S.DELIVERED: frozenset(),
    _S.DELIVERY_FAILED: frozenset({_S.ASSIGNED, _S.RETURNING, _S.CANCELLED}),
    _S.RETURNING: frozenset({_S.RECEIVED_AT_WAREHOUSE, _S.RETURNED_TO_SENDER}),
    _S.RETURNED_TO_SENDER: frozenset(),
    _S.CANCELLED: frozenset({_S.CREATED}),
}


class ShipmentPriority(models.TextChoices):
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class Shipment(TenantScopedModel, PublicIdModel, TimeStampedModel):
    """A consignment from a customer (the sender) to a delivery address.

    `status` changes only through the transition service (rules.md §2). It mirrors the
    latest status_change TrackingEvent; drafts have no events until they are confirmed.
    """

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="shipments")
    service_type = models.ForeignKey(ServiceType, on_delete=models.PROTECT, related_name="+")
    tracking_number = models.CharField(max_length=32)
    # Public tracking credential (architecture §7): 128-bit, rotatable, shown to staff.
    tracking_token = models.CharField(max_length=32, unique=True)
    status = models.CharField(max_length=32, choices=ShipmentStatus.choices)
    priority = models.CharField(
        max_length=10, choices=ShipmentPriority.choices, default=ShipmentPriority.NORMAL
    )
    scheduled_pickup_date = models.DateField(null=True, blank=True)
    external_reference = models.CharField(max_length=100, blank=True)
    # Sum of the item weights, cached for lists and capacity checks (architecture §10).
    total_weight_kg = models.DecimalField(max_digits=9, decimal_places=3)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "tracking_number"], name="shipment_unique_tracking_number"
            ),
            models.UniqueConstraint(
                fields=["company", "external_reference"],
                condition=~models.Q(external_reference=""),
                name="shipment_unique_external_reference",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=ShipmentStatus.values), name="shipment_status_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(priority__in=ShipmentPriority.values),
                name="shipment_priority_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(total_weight_kg__gt=0, total_weight_kg__lte=MAX_TOTAL_WEIGHT_KG),
                name="shipment_total_weight_range",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "status", "created_at"], name="shipment_company_status"
            ),
        ]

    def __str__(self) -> str:
        return self.tracking_number


class ShipmentItem(TenantScopedModel):
    """One parcel, with its own barcode (memory D-API-21: no quantity field)."""

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="items")
    barcode = models.CharField(max_length=40)
    description = models.CharField(max_length=200, blank=True)
    weight_kg = models.DecimalField(max_digits=9, decimal_places=3)
    length_cm = models.DecimalField(max_digits=7, decimal_places=1)
    width_cm = models.DecimalField(max_digits=7, decimal_places=1)
    height_cm = models.DecimalField(max_digits=7, decimal_places=1)

    class Meta:
        ordering = ["id"]  # parcel order as entered; barcodes are numbered in this order
        constraints = [
            models.UniqueConstraint(fields=["company", "barcode"], name="shipment_item_barcode"),
            models.CheckConstraint(
                condition=models.Q(
                    weight_kg__gt=0, length_cm__gt=0, width_cm__gt=0, height_cm__gt=0
                ),
                name="shipment_item_measures_positive",
            ),
        ]

    def __str__(self) -> str:
        return self.barcode


class AddressRole(models.TextChoices):
    PICKUP = "pickup", "Pickup"
    DELIVERY = "delivery", "Delivery"


class ShipmentAddress(TenantScopedModel, PostalAddress):
    """Immutable copy of a pickup or delivery address (memory D-API-20), so editing the
    address book never rewrites shipment history."""

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="addresses")
    role = models.CharField(max_length=10, choices=AddressRole.choices)
    # The address-book entry this was copied from, if any. Kept for reference only.
    source_address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta(PostalAddress.Meta):
        verbose_name_plural = "shipment addresses"
        constraints = [
            *PostalAddress.Meta.constraints,
            models.UniqueConstraint(fields=["shipment", "role"], name="shipment_address_role"),
            # The recipient (or pickup contact) is required on a shipment.
            models.CheckConstraint(
                condition=~models.Q(contact_name=""), name="shipment_address_contact_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.role}: {self.line1}"


class TrackingEventType(models.TextChoices):
    STATUS_CHANGE = "status_change", "Status change"
    # An admin override of the status (architecture §11); never shown on public tracking.
    CORRECTION = "correction", "Correction"


# Event types that set `Shipment.status`; the latest recorded one must match it.
STATUS_EVENT_TYPES = (TrackingEventType.STATUS_CHANGE, TrackingEventType.CORRECTION)


class EventSource(models.TextChoices):
    ADMIN = "admin", "Admin console"


# The django-stubs plugin types each queryset's as_manager() as a distinct generated class,
# so combining two custom querysets (and replacing the base model's manager) trips mypy
# although Django composes them correctly at runtime.
class TrackingEventQuerySet(  # type: ignore[override]
    TenantQuerySet["TrackingEvent"], AppendOnlyQuerySet["TrackingEvent"]
):
    pass


class TrackingEvent(TenantScopedModel, AppendOnlyModel):
    """Append-only shipment history (architecture §12). Only the types and sources in use
    so far are defined; later phases add scans, exceptions, notes and corrections."""

    shipment = models.ForeignKey(Shipment, on_delete=models.PROTECT, related_name="events")
    type = models.CharField(max_length=32, choices=TrackingEventType.choices)
    status_after = models.CharField(max_length=32, choices=ShipmentStatus.choices)
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+"
    )
    source = models.CharField(max_length=20, choices=EventSource.choices)
    # Visible on customer-facing tracking.
    public = models.BooleanField(default=True)
    # Why the change was made (required for cancel, recovery and correction).
    reason = models.CharField(max_length=500, blank=True)

    objects = TrackingEventQuerySet.as_manager()  # type: ignore[misc]  # see TrackingEventQuerySet

    class Meta:
        indexes = [models.Index(fields=["shipment", "occurred_at"], name="tracking_event_timeline")]

    def __str__(self) -> str:
        return f"{self.type} → {self.status_after}"


class BranchTrackingSequence(models.Model):
    """Last tracking-number sequence issued per branch. Locked while issuing a number."""

    branch = models.OneToOneField(Branch, on_delete=models.PROTECT, related_name="+")
    last_value = models.PositiveBigIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.branch_id}: {self.last_value}"
