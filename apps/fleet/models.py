from datetime import timedelta

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import (
    ArrayField,
    DateRangeField,
    RangeBoundary,
    RangeOperators,
)
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Func, Q

from apps.core.models import PublicIdModel, TimeStampedModel
from apps.core.validators import validate_e164
from apps.fleet.constants import (
    MAX_LEAVE_DAYS,
    MAX_VEHICLE_VOLUME_M3,
    MAX_VEHICLE_WEIGHT_KG,
    REGISTRATION_PATTERN,
)
from apps.tenancy.models import Branch, TenantScopedModel


class LicenseCategory(models.TextChoices):
    """Driving-licence categories of the 1968 Vienna Convention. A vehicle names the one
    category it needs; a driver's licence lists the ones they hold (exact match)."""

    A = "A", "A"
    B = "B", "B"
    BE = "BE", "BE"
    C1 = "C1", "C1"
    C1E = "C1E", "C1E"
    C = "C", "C"
    CE = "CE", "CE"
    D1 = "D1", "D1"
    D1E = "D1E", "D1E"
    D = "D", "D"
    DE = "DE", "DE"


class DriverStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"


class VehicleStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    IN_MAINTENANCE = "in_maintenance", "In maintenance"
    OUT_OF_SERVICE = "out_of_service", "Out of service"


class DriverUnavailableReason(models.TextChoices):
    """Why a driver cannot be assigned on a date. Part of the API contract."""

    ARCHIVED = "archived", "Archived"
    LOGIN_DISABLED = "login_disabled", "Login disabled"
    SUSPENDED = "suspended", "Suspended"
    ON_LEAVE = "on_leave", "On leave"
    LICENSE_MISSING = "license_missing", "No licence on file"
    LICENSE_EXPIRED = "license_expired", "Licence expired"


class VehicleUnavailableReason(models.TextChoices):
    ARCHIVED = "archived", "Archived"
    IN_MAINTENANCE = "in_maintenance", "In maintenance"
    OUT_OF_SERVICE = "out_of_service", "Out of service"
    REGISTRATION_MISSING = "registration_missing", "No registration on file"
    REGISTRATION_EXPIRED = "registration_expired", "Registration expired"
    INSURANCE_MISSING = "insurance_missing", "No insurance on file"
    INSURANCE_EXPIRED = "insurance_expired", "Insurance expired"


class DriverDocumentType(models.TextChoices):
    LICENSE = "license", "Driving licence"
    ID_CARD = "id_card", "ID card"
    MEDICAL = "medical", "Medical certificate"
    OTHER = "other", "Other"


class VehicleDocumentType(models.TextChoices):
    REGISTRATION = "registration", "Registration"
    INSURANCE = "insurance", "Insurance"
    INSPECTION = "inspection", "Inspection"
    OTHER = "other", "Other"


# Types that must carry an `expires_on` (DB check). Others may.
EXPIRY_REQUIRED_DRIVER_TYPES = (DriverDocumentType.LICENSE, DriverDocumentType.MEDICAL)
EXPIRY_REQUIRED_VEHICLE_TYPES = (VehicleDocumentType.REGISTRATION, VehicleDocumentType.INSURANCE)


validate_registration = RegexValidator(
    REGISTRATION_PATTERN, "Use letters, digits, spaces and hyphens, e.g. AB 12 CD."
)


def normalize_registration(value: str) -> str:
    """Upper case with single inner spaces, so `ab  12 cd` and `AB 12 CD` are one vehicle."""
    return " ".join(value.upper().split())


class Driver(TenantScopedModel, PublicIdModel, TimeStampedModel):
    """A driver's profile. The person logs in as the linked `driver`-role user (D-API-25);
    this row holds what dispatch needs. Archived rather than deleted (architecture §10)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="driver_profile"
    )
    # The branch the driver works from; users see drivers of their own branches.
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="drivers")
    phone = models.CharField(max_length=16, validators=[validate_e164])
    status = models.CharField(
        max_length=16, choices=DriverStatus.choices, default=DriverStatus.ACTIVE
    )
    status_reason = models.CharField(max_length=255, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=DriverStatus.values), name="driver_status_valid"
            ),
            models.CheckConstraint(
                condition=(
                    Q(status=DriverStatus.SUSPENDED) & ~Q(status_reason="")
                    | ~Q(status=DriverStatus.SUSPENDED) & Q(status_reason="")
                ),
                name="driver_reason_iff_suspended",
            ),
        ]

    def __str__(self) -> str:
        return str(self.public_id)

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class Vehicle(TenantScopedModel, PublicIdModel, TimeStampedModel):
    registration_number = models.CharField(max_length=20, validators=[validate_registration])
    make = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="vehicles")
    # The licence category a driver must hold to drive this vehicle (architecture §13).
    required_license_category = models.CharField(max_length=3, choices=LicenseCategory.choices)
    capacity_weight_kg = models.DecimalField(max_digits=9, decimal_places=3)
    capacity_volume_m3 = models.DecimalField(max_digits=7, decimal_places=3)
    status = models.CharField(
        max_length=16, choices=VehicleStatus.choices, default=VehicleStatus.ACTIVE
    )
    status_reason = models.CharField(max_length=255, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "registration_number"], name="vehicle_unique_registration"
            ),
            models.CheckConstraint(
                condition=Q(registration_number__regex=REGISTRATION_PATTERN),
                name="vehicle_registration_format",
            ),
            models.CheckConstraint(
                condition=Q(status__in=VehicleStatus.values), name="vehicle_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(required_license_category__in=LicenseCategory.values),
                name="vehicle_license_category_valid",
            ),
            models.CheckConstraint(
                condition=Q(capacity_weight_kg__gt=0, capacity_weight_kg__lte=MAX_VEHICLE_WEIGHT_KG)
                & Q(capacity_volume_m3__gt=0, capacity_volume_m3__lte=MAX_VEHICLE_VOLUME_M3),
                name="vehicle_capacity_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status=VehicleStatus.ACTIVE) & Q(status_reason="")
                    | ~Q(status=VehicleStatus.ACTIVE) & ~Q(status_reason="")
                ),
                name="vehicle_reason_iff_not_active",
            ),
        ]

    def __str__(self) -> str:
        return self.registration_number

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class DateRange(Func):
    """`daterange(starts_on, ends_on, '[]')`, for the overlap constraint."""

    function = "DATERANGE"
    output_field = DateRangeField()


class DriverLeave(TenantScopedModel, PublicIdModel, TimeStampedModel):
    """A period a driver is unavailable, both days included. Leave that has not started can
    be deleted; it is a plan, not history."""

    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name="leaves")
    starts_on = models.DateField()
    ends_on = models.DateField()
    reason = models.CharField(max_length=255)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_on__gte=F("starts_on")), name="leave_dates_ordered"
            ),
            models.CheckConstraint(
                condition=Q(ends_on__lt=F("starts_on") + timedelta(days=MAX_LEAVE_DAYS)),
                name="leave_max_length",
            ),
            models.CheckConstraint(condition=~Q(reason=""), name="leave_reason_not_empty"),
            ExclusionConstraint(
                name="leave_no_overlap",
                expressions=[
                    (
                        DateRange(
                            "starts_on",
                            "ends_on",
                            RangeBoundary(inclusive_lower=True, inclusive_upper=True),
                        ),
                        RangeOperators.OVERLAPS,
                    ),
                    ("driver", RangeOperators.EQUAL),
                ],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.starts_on}..{self.ends_on}"


class FleetDocument(TenantScopedModel, PublicIdModel, TimeStampedModel):
    """A scanned document of a driver or vehicle, attached to a confirmed upload. Valid
    through `expires_on`, inclusive, in the company timezone (memory D-API-27). Archived
    rather than deleted; the file stays until the document is."""

    number = models.CharField(max_length=50, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    upload = models.OneToOneField("uploads.Upload", on_delete=models.PROTECT, related_name="+")
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class DriverDocument(FleetDocument):
    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name="documents")
    type = models.CharField(max_length=16, choices=DriverDocumentType.choices)
    # Licence categories (Vienna set). Only on a licence; there is no `Driver.license_categories`.
    categories = ArrayField(
        models.CharField(max_length=3, choices=LicenseCategory.choices), default=list, blank=True
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(type__in=DriverDocumentType.values), name="driver_doc_type_valid"
            ),
            models.CheckConstraint(
                condition=~Q(type__in=EXPIRY_REQUIRED_DRIVER_TYPES) | Q(expires_on__isnull=False),
                name="driver_doc_expiry_required",
            ),
            models.CheckConstraint(
                condition=Q(issued_on__isnull=True)
                | Q(expires_on__isnull=True)
                | Q(issued_on__lte=F("expires_on")),
                name="driver_doc_dates_ordered",
            ),
            models.CheckConstraint(
                condition=(
                    Q(type=DriverDocumentType.LICENSE)
                    & ~Q(categories=[])
                    & Q(categories__contained_by=LicenseCategory.values)
                )
                | (~Q(type=DriverDocumentType.LICENSE) & Q(categories=[])),
                name="driver_doc_categories_iff_license",
            ),
        ]
        indexes = [
            models.Index(
                fields=["driver", "type", "expires_on"],
                condition=Q(archived_at__isnull=True),
                name="driver_doc_live_idx",
            ),
        ]

    def __str__(self) -> str:
        return str(self.public_id)


class VehicleDocument(FleetDocument):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="documents")
    type = models.CharField(max_length=16, choices=VehicleDocumentType.choices)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(type__in=VehicleDocumentType.values), name="vehicle_doc_type_valid"
            ),
            models.CheckConstraint(
                condition=~Q(type__in=EXPIRY_REQUIRED_VEHICLE_TYPES) | Q(expires_on__isnull=False),
                name="vehicle_doc_expiry_required",
            ),
            models.CheckConstraint(
                condition=Q(issued_on__isnull=True)
                | Q(expires_on__isnull=True)
                | Q(issued_on__lte=F("expires_on")),
                name="vehicle_doc_dates_ordered",
            ),
        ]
        indexes = [
            models.Index(
                fields=["vehicle", "type", "expires_on"],
                condition=Q(archived_at__isnull=True),
                name="vehicle_doc_live_idx",
            ),
        ]

    def __str__(self) -> str:
        return str(self.public_id)
