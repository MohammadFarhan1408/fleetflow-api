from django.contrib.gis.db import models as gis_models
from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel
from apps.customers.validators import validate_country, validate_e164
from apps.tenancy.models import TenantScopedModel


class CustomerKind(models.TextChoices):
    INDIVIDUAL = "individual", "Individual"
    BUSINESS = "business", "Business"


class Customer(TenantScopedModel, PublicIdModel, TimeStampedModel):
    """A shipper or recipient. Archived rather than deleted (architecture §10)."""

    kind = models.CharField(max_length=20, choices=CustomerKind.choices)
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=16, blank=True, validators=[validate_e164])
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=~models.Q(name=""), name="customer_name_not_empty"),
            models.CheckConstraint(
                condition=models.Q(kind__in=CustomerKind.values), name="customer_kind_valid"
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class GeocodeConfidence(models.TextChoices):
    """How the address location was obtained (memory D-API-18: no geocoding provider yet)."""

    MANUAL = "manual", "Manual (client-supplied coordinates)"
    NONE = "none", "None (no coordinates)"


class Address(TenantScopedModel, PublicIdModel, TimeStampedModel):
    """An entry in a customer's address book. Shipments snapshot it (D-API-20), so edits
    never change shipment history."""

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="addresses")
    label = models.CharField(max_length=100, blank=True)
    contact_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=16, blank=True, validators=[validate_e164])
    line1 = models.CharField(max_length=200)
    line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100)
    region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2, validators=[validate_country])
    location = gis_models.PointField(geography=True, srid=4326, null=True, blank=True)
    geocode_confidence = models.CharField(
        max_length=10, choices=GeocodeConfidence.choices, default=GeocodeConfidence.NONE
    )
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "addresses"
        constraints = [
            models.CheckConstraint(condition=~models.Q(line1=""), name="address_line1_not_empty"),
            models.CheckConstraint(condition=~models.Q(city=""), name="address_city_not_empty"),
            models.CheckConstraint(
                condition=models.Q(country__regex=r"^[A-Z]{2}$"), name="address_country_format"
            ),
            # A location exists exactly when its confidence says so.
            models.CheckConstraint(
                condition=(
                    models.Q(location__isnull=True, geocode_confidence=GeocodeConfidence.NONE)
                    | models.Q(location__isnull=False, geocode_confidence=GeocodeConfidence.MANUAL)
                ),
                name="address_location_matches_confidence",
            ),
        ]

    def __str__(self) -> str:
        return self.label or self.line1

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None
