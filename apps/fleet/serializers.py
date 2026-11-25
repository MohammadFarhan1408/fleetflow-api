from datetime import date
from decimal import Decimal
from typing import Any

from rest_framework import serializers
from rest_framework.fields import empty

from apps.core.validators import validate_e164
from apps.fleet import selectors
from apps.fleet.constants import MAX_LEAVE_DAYS, MAX_VEHICLE_VOLUME_M3, MAX_VEHICLE_WEIGHT_KG
from apps.fleet.models import (
    Driver,
    DriverLeave,
    DriverStatus,
    DriverUnavailableReason,
    LicenseCategory,
    Vehicle,
    VehicleStatus,
    VehicleUnavailableReason,
    normalize_registration,
    validate_registration,
)


class AvailabilityDateQuerySerializer(serializers.Serializer[dict[str, Any]]):
    availability_date = serializers.DateField(
        required=False,
        help_text="Date to evaluate availability for (default: today in the company timezone).",
    )


class OptionalBooleanField(serializers.BooleanField):
    """A filter that is absent from the query string unless the client sends it. DRF's plain
    BooleanField reads a missing query parameter as `false`, which would silently filter."""

    default_empty_html = empty


class ListQuerySerializer(serializers.Serializer[dict[str, Any]]):
    branch = serializers.UUIDField(required=False, help_text="Branch public ID.")
    include_archived = serializers.BooleanField(required=False, default=False)
    available = OptionalBooleanField(
        required=False, help_text="Only available (true) or only unavailable (false) rows."
    )


class DriverListQuerySerializer(AvailabilityDateQuerySerializer, ListQuerySerializer):
    status = serializers.ChoiceField(choices=DriverStatus.choices, required=False)
    search = serializers.CharField(
        required=False, default="", max_length=100, help_text="Name or email contains."
    )


class VehicleListQuerySerializer(ListQuerySerializer):
    status = serializers.ChoiceField(choices=VehicleStatus.choices, required=False)
    license_category = serializers.ChoiceField(choices=LicenseCategory.choices, required=False)
    search = serializers.CharField(
        required=False, default="", max_length=20, help_text="Registration number prefix."
    )


class _BranchSerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()


class _DriverUserSerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField()
    full_name = serializers.CharField()
    email = serializers.EmailField()


class DriverAvailabilitySerializer(serializers.Serializer[Any]):
    date = serializers.DateField()
    available = serializers.BooleanField()
    reasons = serializers.ListField(child=serializers.ChoiceField(DriverUnavailableReason.choices))


class VehicleAvailabilitySerializer(serializers.Serializer[Any]):
    available = serializers.BooleanField()
    reasons = serializers.ListField(child=serializers.ChoiceField(VehicleUnavailableReason.choices))


class DriverSerializer(serializers.ModelSerializer[Driver]):
    """Read model. The driver must come from `selectors.drivers_for_user`, which carries the
    leave annotation; the evaluated date is `context["on_date"]`."""

    user = _DriverUserSerializer(read_only=True)
    branch = _BranchSerializer(read_only=True)
    availability = serializers.SerializerMethodField()

    class Meta:
        model = Driver
        fields = [
            "public_id",
            "user",
            "branch",
            "phone",
            "status",
            "status_reason",
            "archived_at",
            "availability",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_availability(self, driver: Driver) -> dict[str, Any]:
        reasons = selectors.driver_unavailability(driver)
        return DriverAvailabilitySerializer(
            {"date": self.context["on_date"], "available": not reasons, "reasons": reasons}
        ).data


class DriverCreateSerializer(serializers.Serializer[dict[str, Any]]):
    user_id = serializers.UUIDField(help_text="Public ID of an existing user with the driver role.")
    branch_id = serializers.UUIDField()
    phone = serializers.CharField(max_length=16, validators=[validate_e164])


class DriverUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    branch_id = serializers.UUIDField()
    phone = serializers.CharField(max_length=16, validators=[validate_e164])


class StatusChangeSerializer(serializers.Serializer[dict[str, Any]]):
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=255,
        help_text="Required for every status except active.",
    )


class DriverStatusSerializer(StatusChangeSerializer):
    status = serializers.ChoiceField(choices=DriverStatus.choices)


class VehicleStatusChangeSerializer(StatusChangeSerializer):
    status = serializers.ChoiceField(choices=VehicleStatus.choices)


class LeaveSerializer(serializers.ModelSerializer[DriverLeave]):
    class Meta:
        model = DriverLeave
        fields = ["public_id", "starts_on", "ends_on", "reason", "created_at"]
        read_only_fields = fields


class LeaveWriteSerializer(serializers.Serializer[dict[str, Any]]):
    starts_on = serializers.DateField()
    ends_on = serializers.DateField(help_text="Last day of leave (included).")
    reason = serializers.CharField(max_length=255)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        starts_on: date = attrs["starts_on"]
        ends_on: date = attrs["ends_on"]
        if ends_on < starts_on:
            raise serializers.ValidationError({"ends_on": "Must not be before starts_on."})
        if (ends_on - starts_on).days >= MAX_LEAVE_DAYS:
            raise serializers.ValidationError(
                {"ends_on": f"Leave can last at most {MAX_LEAVE_DAYS} days."}
            )
        return attrs


class VehicleSerializer(serializers.ModelSerializer[Vehicle]):
    branch = _BranchSerializer(read_only=True)
    availability = serializers.SerializerMethodField()

    class Meta:
        model = Vehicle
        fields = [
            "public_id",
            "registration_number",
            "make",
            "model",
            "branch",
            "required_license_category",
            "capacity_weight_kg",
            "capacity_volume_m3",
            "status",
            "status_reason",
            "archived_at",
            "availability",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_availability(self, vehicle: Vehicle) -> dict[str, Any]:
        reasons = selectors.vehicle_unavailability(vehicle)
        return VehicleAvailabilitySerializer({"available": not reasons, "reasons": reasons}).data


class VehicleWriteSerializer(serializers.Serializer[dict[str, Any]]):
    registration_number = serializers.CharField(max_length=30)
    branch_id = serializers.UUIDField()
    required_license_category = serializers.ChoiceField(choices=LicenseCategory.choices)
    capacity_weight_kg = serializers.DecimalField(
        max_digits=9,
        decimal_places=3,
        min_value=Decimal("0.001"),
        max_value=Decimal(MAX_VEHICLE_WEIGHT_KG),
    )
    capacity_volume_m3 = serializers.DecimalField(
        max_digits=7,
        decimal_places=3,
        min_value=Decimal("0.001"),
        max_value=Decimal(MAX_VEHICLE_VOLUME_M3),
    )
    make = serializers.CharField(required=False, allow_blank=True, default="", max_length=100)
    model = serializers.CharField(required=False, allow_blank=True, default="", max_length=100)

    def validate_registration_number(self, value: str) -> str:
        registration = normalize_registration(value)
        validate_registration(registration)  # raises a Django ValidationError that DRF maps
        return registration
