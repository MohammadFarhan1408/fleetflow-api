from datetime import datetime
from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.dispatch.models import Route, RouteStatus, RouteStop, StopType
from apps.dispatch.selectors import DRIVER_VISIBLE_STATUSES
from apps.fleet.serializers import _BranchSerializer  # the same {public_id, code, name} summary
from apps.shipments.models import Shipment
from apps.shipments.serializers import ShipmentAddressSerializer


class RouteListQuerySerializer(serializers.Serializer[dict[str, Any]]):
    route_date = serializers.DateField(required=False)
    branch = serializers.UUIDField(required=False, help_text="Branch public ID.")
    status = serializers.ChoiceField(choices=RouteStatus.choices, required=False)


class _DriverSummarySerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField()
    full_name = serializers.CharField(source="user.full_name")


class _VehicleSummarySerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField()
    registration_number = serializers.CharField()


class RouteSerializer(serializers.ModelSerializer[Route]):
    """A route row. It must come from `selectors.routes_for_user`, which carries
    `stop_count`."""

    branch = _BranchSerializer(read_only=True)
    driver = _DriverSummarySerializer(read_only=True, allow_null=True)
    vehicle = _VehicleSummarySerializer(read_only=True, allow_null=True)
    stop_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Route
        fields = [
            "public_id",
            "branch",
            "driver",
            "vehicle",
            "capacity_override_reason",
            "route_date",
            "status",
            "sequence_enforced",
            "version",
            "stop_count",
            "dispatched_at",
            "started_at",
            "cancelled_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class StopShipmentSerializer(serializers.ModelSerializer[Shipment]):
    class Meta:
        model = Shipment
        fields = ["public_id", "tracking_number", "status", "total_weight_kg"]
        read_only_fields = fields


class RouteStopSerializer(serializers.ModelSerializer[RouteStop]):
    """A stop, from `selectors.stops_with_details` (which carries `has_location` and the
    prefetched addresses)."""

    shipment = StopShipmentSerializer(read_only=True)
    has_location = serializers.BooleanField(
        read_only=True, help_text="Whether the stop's address has coordinates."
    )
    address = serializers.SerializerMethodField()

    class Meta:
        model = RouteStop
        fields = [
            "public_id",
            "type",
            "sequence",
            "status",
            "window_start",
            "window_end",
            "has_location",
            "shipment",
            "address",
        ]
        read_only_fields = fields

    @extend_schema_field(ShipmentAddressSerializer)
    def get_address(self, stop: RouteStop) -> dict[str, Any]:
        address = next(a for a in stop.shipment.addresses.all() if a.role == stop.type)
        return dict(ShipmentAddressSerializer(address).data)


class RouteDetailSerializer(RouteSerializer):
    stops = RouteStopSerializer(many=True, read_only=True, source="ordered_stops")

    class Meta(RouteSerializer.Meta):
        fields = [*RouteSerializer.Meta.fields, "stops"]
        read_only_fields = fields


class DriverRouteSerializer(serializers.ModelSerializer[Route]):
    """A route as its driver sees it (from `selectors.own_routes`, which carries
    `stop_count`): no assignment internals, only the vehicle and the stops to work."""

    branch = _BranchSerializer(read_only=True)
    vehicle = _VehicleSummarySerializer(read_only=True, allow_null=True)
    stop_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Route
        fields = [
            "public_id",
            "branch",
            "vehicle",
            "route_date",
            "status",
            "sequence_enforced",
            "version",
            "stop_count",
            "dispatched_at",
            "started_at",
        ]
        read_only_fields = fields


class DriverRouteDetailSerializer(DriverRouteSerializer):
    stops = RouteStopSerializer(many=True, read_only=True, source="ordered_stops")

    class Meta(DriverRouteSerializer.Meta):
        fields = [*DriverRouteSerializer.Meta.fields, "stops"]
        read_only_fields = fields


class DriverRouteListQuerySerializer(serializers.Serializer[dict[str, Any]]):
    route_date = serializers.DateField(required=False)
    status = serializers.ChoiceField(
        choices=[(s, s.label) for s in DRIVER_VISIBLE_STATUSES], required=False
    )


class RouteCreateSerializer(serializers.Serializer[dict[str, Any]]):
    branch_id = serializers.UUIDField()
    route_date = serializers.DateField(help_text="Today or later, in the company timezone.")
    sequence_enforced = serializers.BooleanField(required=False, default=True)


class RouteVersionSerializer(serializers.Serializer[dict[str, Any]]):
    version = serializers.IntegerField(
        min_value=1, help_text="The route's `version` as the client last saw it."
    )


class RouteUpdateSerializer(RouteVersionSerializer):
    route_date = serializers.DateField(required=False)
    sequence_enforced = serializers.BooleanField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "route_date" not in attrs and "sequence_enforced" not in attrs:
            raise serializers.ValidationError("Send route_date or sequence_enforced.")
        return attrs


class StopAddSerializer(RouteVersionSerializer):
    shipment_id = serializers.UUIDField()
    type = serializers.ChoiceField(choices=StopType.choices)
    window_start = serializers.DateTimeField(required=False, allow_null=True, default=None)
    window_end = serializers.DateTimeField(required=False, allow_null=True, default=None)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        start: datetime | None = attrs["window_start"]
        end: datetime | None = attrs["window_end"]
        if (start is None) != (end is None):
            raise serializers.ValidationError("Send both window_start and window_end, or neither.")
        if start and end and end <= start:
            raise serializers.ValidationError({"window_end": "Must be after window_start."})
        return attrs


class AssignSerializer(RouteVersionSerializer):
    driver_id = serializers.UUIDField()
    vehicle_id = serializers.UUIDField()
    override_reason = serializers.CharField(
        max_length=255,
        required=False,
        default="",
        help_text="Required to go over the vehicle's capacity, which also needs "
        "`dispatch.override_capacity`. Ignored when the load fits.",
    )


class ReassignSerializer(RouteVersionSerializer):
    driver_id = serializers.UUIDField()
    vehicle_id = serializers.UUIDField()


class ReorderSerializer(RouteVersionSerializer):
    stop_ids = serializers.ListField(
        child=serializers.UUIDField(),
        max_length=300,
        help_text="Every stop of the route exactly once, in the new order.",
    )


class SuggestedOrderSerializer(serializers.Serializer[dict[str, Any]]):
    optimization = serializers.ChoiceField(choices=["nearest_neighbour"])
    version = serializers.IntegerField(help_text="The route's version the suggestion is based on.")
    stop_ids = serializers.ListField(
        child=serializers.UUIDField(),
        help_text="Every stop once, in the suggested order; send it to `reorder` to apply it.",
    )
