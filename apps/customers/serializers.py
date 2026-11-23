from typing import Any

from django.contrib.gis.geos import Point
from rest_framework import serializers

from apps.customers.models import Address, Customer, CustomerKind
from apps.customers.validators import validate_country, validate_e164


class ListQuerySerializer(serializers.Serializer[dict[str, Any]]):
    include_archived = serializers.BooleanField(
        required=False, default=False, help_text="Include archived rows."
    )


class CustomerListQuerySerializer(ListQuerySerializer):
    search = serializers.CharField(
        required=False, default="", max_length=100, help_text="Case-insensitive name match."
    )


class CustomerSerializer(serializers.ModelSerializer[Customer]):
    class Meta:
        model = Customer
        fields = [
            "public_id",
            "kind",
            "name",
            "email",
            "phone",
            "archived_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class CustomerWriteSerializer(serializers.Serializer[dict[str, Any]]):
    kind = serializers.ChoiceField(choices=CustomerKind.choices)
    name = serializers.CharField(max_length=200)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    phone = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=16, validators=[validate_e164]
    )


class AddressSerializer(serializers.ModelSerializer[Address]):
    latitude = serializers.SerializerMethodField()
    longitude = serializers.SerializerMethodField()

    class Meta:
        model = Address
        fields = [
            "public_id",
            "label",
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
            "archived_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_latitude(self, address: Address) -> float | None:
        return address.location.y if address.location else None

    def get_longitude(self, address: Address) -> float | None:
        return address.location.x if address.location else None


class AddressWriteSerializer(serializers.Serializer[dict[str, Any]]):
    """Address input. Coordinates are optional and client-supplied (memory D-API-18): send
    both `latitude` and `longitude`, or neither; on PATCH, both `null` removes them."""

    # Shadows Field.label only in the type stubs; DRF's metaclass moves declared fields off
    # the class, so this is a normal input field at runtime.
    label = serializers.CharField(  # type: ignore[assignment]
        required=False, allow_blank=True, default="", max_length=100
    )
    contact_name = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=200
    )
    phone = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=16, validators=[validate_e164]
    )
    line1 = serializers.CharField(max_length=200)
    line2 = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    city = serializers.CharField(max_length=100)
    region = serializers.CharField(required=False, allow_blank=True, default="", max_length=100)
    postal_code = serializers.CharField(required=False, allow_blank=True, default="", max_length=20)
    country = serializers.CharField(
        min_length=2, max_length=2, help_text="ISO 3166-1 alpha-2, e.g. DE."
    )
    latitude = serializers.FloatField(
        required=False, allow_null=True, min_value=-90, max_value=90, write_only=True
    )
    longitude = serializers.FloatField(
        required=False, allow_null=True, min_value=-180, max_value=180, write_only=True
    )

    def validate_country(self, value: str) -> str:
        country = value.upper()
        validate_country(country)
        return country

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Replace latitude/longitude with `location` (a Point, or None to clear). The key
        is absent when neither coordinate was sent, so a PATCH keeps the stored point."""
        has_lat, has_lng = "latitude" in attrs, "longitude" in attrs
        if not (has_lat or has_lng):
            return attrs
        latitude, longitude = attrs.pop("latitude", None), attrs.pop("longitude", None)
        if not (has_lat and has_lng) or (latitude is None) != (longitude is None):
            raise serializers.ValidationError(
                {"latitude": ["Send latitude and longitude together, or neither."]}
            )
        attrs["location"] = None if latitude is None else Point(longitude, latitude, srid=4326)
        return attrs
