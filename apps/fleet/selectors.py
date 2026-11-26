"""Tenant- and branch-scoped fleet reads (architecture §8, §9). Every read starts here.

Availability is computed from the row itself (plus `Exists` annotations for leave and
documents), so lists need no query per row.

A document is valid on a date when it is not archived and its `expires_on` is that date or
later (inclusive). `_live_valid` is the one place that says so; the annotations, the
selectors Phase 4 calls and the tests all go through it.
"""

import uuid
from datetime import date

from django.db.models import Exists, F, OuterRef, Q, QuerySet
from django.shortcuts import get_object_or_404

from apps.accounts.models import User
from apps.accounts.selectors import branches_in_scope
from apps.core.errors import DomainError, ErrorCode
from apps.fleet.models import (
    Driver,
    DriverDocument,
    DriverDocumentType,
    DriverLeave,
    DriverStatus,
    DriverUnavailableReason,
    LicenseCategory,
    Shift,
    Vehicle,
    VehicleDocument,
    VehicleDocumentType,
    VehicleStatus,
    VehicleUnavailableReason,
)
from apps.fleet.permissions import FLEET_ALL_BRANCHES

# A driver can take work only if none of these hold. `driver_unavailability` lists which do;
# the list filter uses this same condition, and a test keeps the two in step.
_DRIVER_AVAILABLE = Q(
    archived_at__isnull=True,
    user__is_active=True,
    status=DriverStatus.ACTIVE,
    on_leave=False,
    license_valid=True,
)
_VEHICLE_AVAILABLE = Q(
    archived_at__isnull=True,
    status=VehicleStatus.ACTIVE,
    registration_valid=True,
    insurance_valid=True,
)


def _live_valid(on_date: date) -> Q:
    """Not archived, and valid through `expires_on` (inclusive). A document without an
    expiry never matches: the types that matter for assignment must have one (DB check)."""
    return Q(archived_at__isnull=True, expires_on__gte=on_date)


def drivers_in_scope(user: User) -> QuerySet[Driver]:
    """The user's company's drivers whose home branch the user may see. Services lock
    through this, so they enforce the same scope as the views."""
    return Driver.objects.for_company(user.company).filter(
        branch__in=branches_in_scope(user, FLEET_ALL_BRANCHES)
    )


def _with_availability(drivers: QuerySet[Driver], on_date: date) -> QuerySet[Driver]:
    """`drivers` with what `driver_unavailability` reads: leave and licence for `on_date`."""
    covering_leave = DriverLeave.objects.filter(
        driver=OuterRef("pk"), starts_on__lte=on_date, ends_on__gte=on_date
    )
    licenses = DriverDocument.objects.filter(driver=OuterRef("pk"), type=DriverDocumentType.LICENSE)
    return drivers.select_related("user", "branch").annotate(
        on_leave=Exists(covering_leave),
        has_license=Exists(licenses.filter(archived_at__isnull=True)),
        license_valid=Exists(licenses.filter(_live_valid(on_date))),
    )


def drivers_for_user(user: User, on_date: date) -> QuerySet[Driver]:
    """Scoped drivers with what the serializer reads, plus the availability annotations
    for `on_date`."""
    return _with_availability(drivers_in_scope(user), on_date)


def own_driver(user: User, on_date: date) -> Driver:
    """The signed-in driver's own live profile. Not branch-scoped: a driver needs no
    branch membership to work. Without an active profile every driver endpoint answers
    403 `driver.profile_inactive` (architecture §7)."""
    qs = Driver.objects.for_company(user.company).filter(user=user, archived_at__isnull=True)
    driver = _with_availability(qs, on_date).first()
    if driver is None:
        raise DomainError(ErrorCode.DRIVER_PROFILE_INACTIVE)
    return driver


def active_shift(driver: Driver) -> Shift | None:
    """The driver's open shift, if any (at most one, by constraint). Phase 7 ping
    ingestion asks this too."""
    return Shift.objects.for_company(driver.company).filter(driver=driver, ended_at=None).first()


def list_drivers(
    user: User,
    *,
    on_date: date,
    status: DriverStatus | None = None,
    branch: uuid.UUID | None = None,
    include_archived: bool = False,
    search: str = "",
    available: bool | None = None,
) -> QuerySet[Driver]:
    drivers = drivers_for_user(user, on_date)
    if not include_archived:
        drivers = drivers.filter(archived_at__isnull=True)
    if status:
        drivers = drivers.filter(status=status)
    if branch:
        drivers = drivers.filter(branch__public_id=branch)
    if search:
        drivers = drivers.filter(
            Q(user__full_name__icontains=search) | Q(user__email__icontains=search)
        )
    if available is not None:
        drivers = drivers.filter(_DRIVER_AVAILABLE if available else ~_DRIVER_AVAILABLE)
    return drivers


def get_driver(user: User, public_id: uuid.UUID, on_date: date) -> Driver:
    """A driver the user may see, or 404 (other tenants' and other branches' drivers are
    indistinguishable from missing ones)."""
    return get_object_or_404(drivers_for_user(user, on_date), public_id=public_id)


def driver_unavailability(driver: Driver) -> list[DriverUnavailableReason]:
    """Reasons `driver` cannot take work on the date the row was loaded for (empty means
    available). Needs a driver from `drivers_for_user`, which carries the annotations."""
    on_leave: bool = driver.on_leave  # type: ignore[attr-defined]  # query annotation
    has_license: bool = driver.has_license  # type: ignore[attr-defined]  # query annotation
    license_valid: bool = driver.license_valid  # type: ignore[attr-defined]  # query annotation
    reasons = []
    if driver.is_archived:
        reasons.append(DriverUnavailableReason.ARCHIVED)
    if not driver.user.is_active:
        reasons.append(DriverUnavailableReason.LOGIN_DISABLED)
    if driver.status == DriverStatus.SUSPENDED:
        reasons.append(DriverUnavailableReason.SUSPENDED)
    if on_leave:
        reasons.append(DriverUnavailableReason.ON_LEAVE)
    if not has_license:
        reasons.append(DriverUnavailableReason.LICENSE_MISSING)
    elif not license_valid:
        reasons.append(DriverUnavailableReason.LICENSE_EXPIRED)
    return reasons


def leaves_of(driver: Driver) -> QuerySet[DriverLeave]:
    return DriverLeave.objects.for_company(driver.company).filter(driver=driver)


def get_leave(driver: Driver, public_id: uuid.UUID) -> DriverLeave:
    return get_object_or_404(leaves_of(driver), public_id=public_id)


def vehicles_in_scope(user: User) -> QuerySet[Vehicle]:
    """The user's company's vehicles whose home branch the user may see. Services lock
    through this, so they enforce the same scope as the views."""
    return (
        Vehicle.objects.for_company(user.company)
        .filter(branch__in=branches_in_scope(user, FLEET_ALL_BRANCHES))
        .select_related("branch")
    )


def vehicles_for_user(user: User, on_date: date) -> QuerySet[Vehicle]:
    """Scoped vehicles plus the registration and insurance annotations for `on_date`."""

    def has(type_: VehicleDocumentType, *, valid: bool) -> Exists:
        documents = VehicleDocument.objects.filter(vehicle=OuterRef("pk"), type=type_)
        return Exists(documents.filter(_live_valid(on_date) if valid else Q(archived_at=None)))

    return vehicles_in_scope(user).annotate(
        has_registration=has(VehicleDocumentType.REGISTRATION, valid=False),
        registration_valid=has(VehicleDocumentType.REGISTRATION, valid=True),
        has_insurance=has(VehicleDocumentType.INSURANCE, valid=False),
        insurance_valid=has(VehicleDocumentType.INSURANCE, valid=True),
    )


def list_vehicles(
    user: User,
    *,
    on_date: date,
    status: VehicleStatus | None = None,
    branch: uuid.UUID | None = None,
    include_archived: bool = False,
    search: str = "",
    license_category: LicenseCategory | None = None,
    available: bool | None = None,
) -> QuerySet[Vehicle]:
    vehicles = vehicles_for_user(user, on_date)
    if not include_archived:
        vehicles = vehicles.filter(archived_at__isnull=True)
    if status:
        vehicles = vehicles.filter(status=status)
    if branch:
        vehicles = vehicles.filter(branch__public_id=branch)
    if search:
        vehicles = vehicles.filter(registration_number__istartswith=search)
    if license_category:
        vehicles = vehicles.filter(required_license_category=license_category)
    if available is not None:
        vehicles = vehicles.filter(_VEHICLE_AVAILABLE if available else ~_VEHICLE_AVAILABLE)
    return vehicles


def get_vehicle(user: User, public_id: uuid.UUID, on_date: date) -> Vehicle:
    return get_object_or_404(vehicles_for_user(user, on_date), public_id=public_id)


def vehicle_unavailability(vehicle: Vehicle) -> list[VehicleUnavailableReason]:
    """Reasons `vehicle` cannot take work on the date the row was loaded for. Needs a
    vehicle from `vehicles_for_user`, which carries the document annotations."""
    reasons = []
    if vehicle.is_archived:
        reasons.append(VehicleUnavailableReason.ARCHIVED)
    if vehicle.status == VehicleStatus.IN_MAINTENANCE:
        reasons.append(VehicleUnavailableReason.IN_MAINTENANCE)
    if vehicle.status == VehicleStatus.OUT_OF_SERVICE:
        reasons.append(VehicleUnavailableReason.OUT_OF_SERVICE)
    # query annotations: see vehicles_for_user
    if not vehicle.has_registration:  # type: ignore[attr-defined]
        reasons.append(VehicleUnavailableReason.REGISTRATION_MISSING)
    elif not vehicle.registration_valid:  # type: ignore[attr-defined]
        reasons.append(VehicleUnavailableReason.REGISTRATION_EXPIRED)
    if not vehicle.has_insurance:  # type: ignore[attr-defined]
        reasons.append(VehicleUnavailableReason.INSURANCE_MISSING)
    elif not vehicle.insurance_valid:  # type: ignore[attr-defined]
        reasons.append(VehicleUnavailableReason.INSURANCE_EXPIRED)
    return reasons


# --- documents -----------------------------------------------------------------------------


def driver_documents(driver: Driver) -> QuerySet[DriverDocument]:
    return DriverDocument.objects.for_company(driver.company).filter(driver=driver)


def vehicle_documents(vehicle: Vehicle) -> QuerySet[VehicleDocument]:
    return VehicleDocument.objects.for_company(vehicle.company).filter(vehicle=vehicle)


def list_documents[D: DriverDocument | VehicleDocument](
    documents: QuerySet[D],
    *,
    type: str | None = None,  # the query parameter's name
    expires_before: date | None = None,
    include_archived: bool = False,
) -> QuerySet[D]:
    """`expires_before` is strict: a document expiring on that date is not listed."""
    documents = documents.select_related("upload")
    if not include_archived:
        documents = documents.filter(archived_at__isnull=True)
    if type:
        documents = documents.filter(type=type)
    if expires_before:
        documents = documents.filter(expires_on__lt=expires_before)
    return documents


def get_document[D: DriverDocument | VehicleDocument](
    documents: QuerySet[D], public_id: uuid.UUID
) -> D:
    return get_object_or_404(documents.select_related("upload"), public_id=public_id)


def _newest_valid[D: DriverDocument | VehicleDocument](
    documents: QuerySet[D], type_: str, on_date: date
) -> D | None:
    """The valid document of `type_` that was issued last (undated ones last)."""
    return (
        documents.filter(_live_valid(on_date), type=type_)
        .order_by(F("issued_on").desc(nulls_last=True), "-created_at")
        .first()
    )


def valid_license(driver: Driver, on_date: date) -> DriverDocument | None:
    """The driver's newest non-archived licence that is still valid on `on_date`."""
    return _newest_valid(driver_documents(driver), DriverDocumentType.LICENSE, on_date)


def license_covers(driver: Driver, vehicle: Vehicle, on_date: date) -> bool:
    """Whether the driver's valid licence lists the vehicle's required category (exact
    match: `C` does not cover `C1` or `CE`). Phase 4 assignment calls this."""
    licence = valid_license(driver, on_date)
    return licence is not None and vehicle.required_license_category in licence.categories


def valid_vehicle_document(
    vehicle: Vehicle, type_: VehicleDocumentType, on_date: date
) -> VehicleDocument | None:
    """The vehicle's newest valid registration or insurance (or other type)."""
    return _newest_valid(vehicle_documents(vehicle), type_, on_date)


def expiring_documents(
    user: User, before: date
) -> tuple[QuerySet[DriverDocument], QuerySet[VehicleDocument]]:
    """Live documents, of live drivers and vehicles in the user's scope, that expire before
    `before` (already expired ones included)."""
    drivers = DriverDocument.objects.filter(
        driver__in=drivers_in_scope(user).filter(archived_at__isnull=True),
        archived_at__isnull=True,
        expires_on__lt=before,
    )
    vehicles = VehicleDocument.objects.filter(
        vehicle__in=vehicles_in_scope(user).filter(archived_at__isnull=True),
        archived_at__isnull=True,
        expires_on__lt=before,
    )
    return drivers, vehicles
