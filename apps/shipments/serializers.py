import uuid
from decimal import Decimal
from typing import Any

from drf_spectacular.utils import PolymorphicProxySerializer, extend_schema_field
from rest_framework import serializers

from apps.customers.serializers import AddressWriteSerializer
from apps.customers.services import AddressFields
from apps.shipments.constants import MAX_ITEMS
from apps.shipments.models import (
    AddressRole,
    Shipment,
    ShipmentAddress,
    ShipmentItem,
    ShipmentPriority,
    ShipmentStatus,
    TrackingEvent,
)
from apps.shipments.services import ItemFields

# --- Input ----------------------------------------------------------------------------


class ShipmentListQuerySerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.ListField(
        child=serializers.ChoiceField(choices=ShipmentStatus.choices),
        required=False,
        help_text="Repeat to match any of several statuses.",
    )
    branch = serializers.UUIDField(required=False, help_text="Branch public ID.")
    customer = serializers.UUIDField(required=False, help_text="Sender customer public ID.")
    service_type = serializers.CharField(required=False, default="", max_length=32)
    priority = serializers.ChoiceField(choices=ShipmentPriority.choices, required=False)
    created_after = serializers.DateTimeField(required=False, help_text="Inclusive.")
    created_before = serializers.DateTimeField(required=False, help_text="Exclusive.")
    search = serializers.CharField(
        required=False,
        default="",
        max_length=100,
        help_text="Prefix of the tracking number or external reference, case-insensitive.",
    )


class AddressRefSerializer(serializers.Serializer[dict[str, Any]]):
    address_id = serializers.UUIDField(help_text="Address-book entry to copy.")


class InlineAddressSerializer(AddressWriteSerializer):
    """A full address typed in for this shipment. The contact (recipient) is required."""

    # The address-book label has no meaning on a shipment; None removes the inherited field.
    label = None  # type: ignore[assignment]
    contact_name = serializers.CharField(max_length=200)


@extend_schema_field(
    PolymorphicProxySerializer(
        component_name="ShipmentAddressInput",
        serializers=[AddressRefSerializer, InlineAddressSerializer],
        resource_type_field_name=None,
    )
)
class AddressSpecField(serializers.Field):  # type: ignore[type-arg]  # DRF Field generics vary per stub version
    """Either `{"address_id": ...}` (copied from the address book) or an inline address.
    Both become an immutable snapshot on the shipment (memory D-API-20)."""

    def to_internal_value(self, data: Any) -> uuid.UUID | AddressFields:
        if not isinstance(data, dict):
            raise serializers.ValidationError("Expected an object.")
        if "address_id" in data:
            if len(data) != 1:
                raise serializers.ValidationError(
                    "Send address_id alone, or the address fields without it."
                )
            reference = AddressRefSerializer(data=data)
            reference.is_valid(raise_exception=True)
            address_id: uuid.UUID = reference.validated_data["address_id"]
            return address_id
        inline = InlineAddressSerializer(data=data)
        inline.is_valid(raise_exception=True)
        return AddressFields(**inline.validated_data)

    def to_representation(self, value: Any) -> Any:
        raise NotImplementedError("write-only field")


class ItemWriteSerializer(serializers.Serializer[dict[str, Any]]):
    # Weights ≤ 0 are rejected by the service with `shipment.invalid_weight` (§26.4).
    weight_kg = serializers.DecimalField(max_digits=9, decimal_places=3)
    length_cm = serializers.DecimalField(max_digits=7, decimal_places=1, min_value=Decimal("0.1"))
    width_cm = serializers.DecimalField(max_digits=7, decimal_places=1, min_value=Decimal("0.1"))
    height_cm = serializers.DecimalField(max_digits=7, decimal_places=1, min_value=Decimal("0.1"))
    description = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=200
    )


class ShipmentWriteSerializer(serializers.Serializer[dict[str, Any]]):
    """Shipment fields. On a draft PATCH, sent fields replace the stored ones; `items`, if
    sent, replaces every parcel."""

    branch_id = serializers.UUIDField()
    customer_id = serializers.UUIDField(help_text="The sender.")
    service_type = serializers.CharField(max_length=32, help_text="Service type code.")
    pickup = AddressSpecField()
    delivery = AddressSpecField()
    items = serializers.ListField(child=ItemWriteSerializer(), min_length=1, max_length=MAX_ITEMS)
    priority = serializers.ChoiceField(
        choices=ShipmentPriority.choices, required=False, default=ShipmentPriority.NORMAL
    )
    scheduled_pickup_date = serializers.DateField(required=False, allow_null=True, default=None)
    external_reference = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=100
    )

    def validate_items(self, value: list[dict[str, Any]]) -> tuple[ItemFields, ...]:
        return tuple(ItemFields(**item) for item in value)


class ShipmentCreateSerializer(ShipmentWriteSerializer):
    draft = serializers.BooleanField(
        required=False, default=False, help_text="Create as an editable draft."
    )


class TransitionSerializer(serializers.Serializer[dict[str, Any]]):
    """A status change requested through the API. Phase 2 exposes confirm
    (`draft → created`), cancel (`→ cancelled`) and recovery (`cancelled → created`);
    other edges belong to later phases (memory D-API-21)."""

    to = serializers.ChoiceField(
        choices=[ShipmentStatus.CREATED, ShipmentStatus.CANCELLED],
        help_text="created: confirm a draft or recover a cancelled shipment; cancelled: cancel.",
    )
    expected_status = serializers.ChoiceField(
        choices=ShipmentStatus.choices,
        help_text="The status the client last saw; 409 shipment.status_conflict if it changed.",
    )
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=500,
        help_text="Required to cancel or recover.",
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        needs_reason = (
            attrs["to"] == ShipmentStatus.CANCELLED
            or attrs["expected_status"] == ShipmentStatus.CANCELLED
        )
        if needs_reason and not attrs["reason"].strip():
            raise serializers.ValidationError({"reason": ["A reason is required."]})
        return attrs


class CorrectionSerializer(serializers.Serializer[dict[str, Any]]):
    to = serializers.ChoiceField(
        choices=[c for c in ShipmentStatus.choices if c[0] != ShipmentStatus.DRAFT]
    )
    expected_status = serializers.ChoiceField(choices=ShipmentStatus.choices)
    reason = serializers.CharField(max_length=500)


# --- Output ---------------------------------------------------------------------------


class BranchRefSerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField()
    code = serializers.CharField()


class CustomerRefSerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField()
    name = serializers.CharField()


class ShipmentListSerializer(serializers.ModelSerializer[Shipment]):
    branch = BranchRefSerializer()
    customer = CustomerRefSerializer()
    service_type = serializers.CharField(source="service_type.code")

    class Meta:
        model = Shipment
        fields = [
            "public_id",
            "tracking_number",
            "status",
            "priority",
            "branch",
            "customer",
            "service_type",
            "scheduled_pickup_date",
            "external_reference",
            "total_weight_kg",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ShipmentItemSerializer(serializers.ModelSerializer[ShipmentItem]):
    class Meta:
        model = ShipmentItem
        fields = ["barcode", "description", "weight_kg", "length_cm", "width_cm", "height_cm"]
        read_only_fields = fields


class ShipmentAddressSerializer(serializers.ModelSerializer[ShipmentAddress]):
    latitude = serializers.SerializerMethodField()
    longitude = serializers.SerializerMethodField()
    source_address_id = serializers.UUIDField(
        source="source_address.public_id", allow_null=True, default=None
    )

    class Meta:
        model = ShipmentAddress
        fields = [
            "contact_name",
            "phone",
            "line1",
            "line2",
            "city",
            "region",
            "postal_code",
            "country",
            "latitude",
            "longitude",
            "geocode_confidence",
            "source_address_id",
        ]
        read_only_fields = fields

    def get_latitude(self, address: ShipmentAddress) -> float | None:
        return address.location.y if address.location else None

    def get_longitude(self, address: ShipmentAddress) -> float | None:
        return address.location.x if address.location else None


class ShipmentDetailSerializer(ShipmentListSerializer):
    pickup = serializers.SerializerMethodField()
    delivery = serializers.SerializerMethodField()
    items = ShipmentItemSerializer(many=True)

    class Meta(ShipmentListSerializer.Meta):
        fields = [
            *ShipmentListSerializer.Meta.fields,
            "tracking_token",
            "pickup",
            "delivery",
            "items",
        ]
        read_only_fields = fields

    @extend_schema_field(ShipmentAddressSerializer)
    def get_pickup(self, shipment: Shipment) -> dict[str, Any]:
        return self._address(shipment, AddressRole.PICKUP)

    @extend_schema_field(ShipmentAddressSerializer)
    def get_delivery(self, shipment: Shipment) -> dict[str, Any]:
        return self._address(shipment, AddressRole.DELIVERY)

    def _address(self, shipment: Shipment, role: AddressRole) -> dict[str, Any]:
        # Reads the prefetched snapshots; a shipment always has exactly one per role.
        snapshot = next(a for a in shipment.addresses.all() if a.role == role)
        return ShipmentAddressSerializer(snapshot).data


class TrackingTokenSerializer(serializers.ModelSerializer[Shipment]):
    class Meta:
        model = Shipment
        fields = ["tracking_token"]
        read_only_fields = fields


class ActorRefSerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField()
    full_name = serializers.CharField()


class TrackingEventSerializer(serializers.ModelSerializer[TrackingEvent]):
    actor = ActorRefSerializer(allow_null=True)

    class Meta:
        model = TrackingEvent
        fields = [
            "type",
            "status_after",
            "occurred_at",
            "recorded_at",
            "source",
            "public",
            "reason",
            "actor",
        ]
        read_only_fields = fields


class PublicEventSerializer(serializers.ModelSerializer[TrackingEvent]):
    status = serializers.CharField(source="status_after")

    class Meta:
        model = TrackingEvent
        fields = ["status", "occurred_at"]
        read_only_fields = fields


class PublicDeliverySerializer(serializers.Serializer[Any]):
    city = serializers.CharField()
    country = serializers.CharField()


class PublicTrackingSerializer(serializers.ModelSerializer[Shipment]):
    """The only fields public tracking may show (owner-approved, memory D-API-24): no
    names, phones, street addresses, sender or internal events."""

    carrier = serializers.CharField(source="company.name")
    service = serializers.CharField(source="service_type.name")
    delivery = serializers.SerializerMethodField()
    events = PublicEventSerializer(source="public_events", many=True)

    class Meta:
        model = Shipment
        fields = [
            "tracking_number",
            "status",
            "carrier",
            "service",
            "delivery",
            "scheduled_pickup_date",
            "events",
        ]
        read_only_fields = fields

    @extend_schema_field(PublicDeliverySerializer)
    def get_delivery(self, shipment: Shipment) -> dict[str, Any]:
        (address,) = shipment.delivery_addresses  # type: ignore[attr-defined]  # prefetch to_attr
        return PublicDeliverySerializer(address).data
