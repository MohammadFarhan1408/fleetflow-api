"""Tenant- and branch-scoped fleet reads (architecture §8, §9). Every read starts here.

Availability is computed from the row itself (plus one annotation for leave), so lists
need no query per row.
"""

import uuid
from datetime import date

from django.db.models import Exists, OuterRef, Q, QuerySet
from django.shortcuts import get_object_or_404

from apps.accounts.models import User
from apps.accounts.selectors import branches_in_scope
from apps.fleet.models import (
    Driver,
    DriverLeave,
    DriverStatus,
    DriverUnavailableReason,
    LicenseCategory,
    Vehicle,
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
)
_VEHICLE_AVAILABLE = Q(archived_at__isnull=True, status=VehicleStatus.ACTIVE)


def drivers_in_scope(user: User) -> QuerySet[Driver]:
    """The user's company's drivers whose home branch the user may see. Services lock
    through this, so they enforce the same scope as the views."""
    return Driver.objects.for_company(user.company).filter(
        branch__in=branches_in_scope(user, FLEET_ALL_BRANCHES)
    )


def drivers_for_user(user: User, on_date: date) -> QuerySet[Driver]:
    """Scoped drivers with what the serializer reads, plus `on_leave` for `on_date`."""
    covering_leave = DriverLeave.objects.filter(
        driver=OuterRef("pk"), starts_on__lte=on_date, ends_on__gte=on_date
    )
    return (
        drivers_in_scope(user)
        .select_related("user", "branch")
        .annotate(on_leave=Exists(covering_leave))
    )


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
    available). Needs a driver from `drivers_for_user`, which carries `on_leave`."""
    on_leave: bool = driver.on_leave  # type: ignore[attr-defined]  # query annotation
    reasons = []
    if driver.is_archived:
        reasons.append(DriverUnavailableReason.ARCHIVED)
    if not driver.user.is_active:
        reasons.append(DriverUnavailableReason.LOGIN_DISABLED)
    if driver.status == DriverStatus.SUSPENDED:
        reasons.append(DriverUnavailableReason.SUSPENDED)
    if on_leave:
        reasons.append(DriverUnavailableReason.ON_LEAVE)
    return reasons


def leaves_of(driver: Driver) -> QuerySet[DriverLeave]:
    return DriverLeave.objects.for_company(driver.company).filter(driver=driver)


def get_leave(driver: Driver, public_id: uuid.UUID) -> DriverLeave:
    return get_object_or_404(leaves_of(driver), public_id=public_id)


def vehicles_for_user(user: User) -> QuerySet[Vehicle]:
    return (
        Vehicle.objects.for_company(user.company)
        .filter(branch__in=branches_in_scope(user, FLEET_ALL_BRANCHES))
        .select_related("branch")
    )


def list_vehicles(
    user: User,
    *,
    status: VehicleStatus | None = None,
    branch: uuid.UUID | None = None,
    include_archived: bool = False,
    search: str = "",
    license_category: LicenseCategory | None = None,
    available: bool | None = None,
) -> QuerySet[Vehicle]:
    vehicles = vehicles_for_user(user)
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


def get_vehicle(user: User, public_id: uuid.UUID) -> Vehicle:
    return get_object_or_404(vehicles_for_user(user), public_id=public_id)


def vehicle_unavailability(vehicle: Vehicle) -> list[VehicleUnavailableReason]:
    reasons = []
    if vehicle.is_archived:
        reasons.append(VehicleUnavailableReason.ARCHIVED)
    if vehicle.status == VehicleStatus.IN_MAINTENANCE:
        reasons.append(VehicleUnavailableReason.IN_MAINTENANCE)
    if vehicle.status == VehicleStatus.OUT_OF_SERVICE:
        reasons.append(VehicleUnavailableReason.OUT_OF_SERVICE)
    return reasons
