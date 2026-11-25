"""Fleet writes (architecture §5). Views pass the acting user; every service re-locks the
row through the actor's company and branch scope (rules.md §3), so a caller that skipped
the selector still cannot touch another tenant's or branch's data."""

import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone

from apps.accounts.models import RoleCode, User
from apps.accounts.selectors import branches_in_scope
from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.core.errors import DomainError, ErrorCode, invalid_reference
from apps.fleet import selectors
from apps.fleet.models import (
    Driver,
    DriverDocument,
    DriverLeave,
    DriverStatus,
    FleetDocument,
    LicenseCategory,
    Vehicle,
    VehicleDocument,
    VehicleStatus,
    normalize_registration,
)
from apps.fleet.permissions import FLEET_ALL_BRANCHES
from apps.tenancy.models import Branch
from apps.uploads import services as uploads
from apps.uploads.models import UploadPurpose


@dataclass(frozen=True)
class DriverFields:
    branch_id: uuid.UUID
    phone: str

    @classmethod
    def of(cls, driver: Driver) -> DriverFields:
        return cls(branch_id=driver.branch.public_id, phone=driver.phone)


@dataclass(frozen=True)
class VehicleFields:
    registration_number: str
    branch_id: uuid.UUID
    required_license_category: LicenseCategory
    capacity_weight_kg: Decimal
    capacity_volume_m3: Decimal
    make: str = ""
    model: str = ""

    @classmethod
    def of(cls, vehicle: Vehicle) -> VehicleFields:
        return cls(
            registration_number=vehicle.registration_number,
            branch_id=vehicle.branch.public_id,
            required_license_category=LicenseCategory(vehicle.required_license_category),
            capacity_weight_kg=vehicle.capacity_weight_kg,
            capacity_volume_m3=vehicle.capacity_volume_m3,
            make=vehicle.make,
            model=vehicle.model,
        )


def _branch_in_scope(actor: User, branch_id: uuid.UUID) -> Branch:
    branch = branches_in_scope(actor, FLEET_ALL_BRANCHES).filter(public_id=branch_id).first()
    if branch is None:
        raise invalid_reference("branch_id", "Branch not found.")
    return branch


def _audit(
    action: AuditAction,
    actor: User,
    obj: Driver | Vehicle | DriverLeave | FleetDocument,
    object_type: str,
    request: HttpRequest,
    after: dict[str, str] | None = None,
) -> None:
    # IDs and enums only: names, phones, plates and reasons stay out of the log (rules.md §3).
    audit.record(
        action,
        company=actor.company,
        actor=actor,
        object_type=object_type,
        object_id=str(obj.public_id),
        after=after,
        request=request,
    )


def _status_reason(status: str, active_value: str, reason: str) -> str:
    """The stored reason: required for any non-active status, cleared for the active one."""
    if status == active_value:
        return ""
    reason = reason.strip()
    if not reason:
        raise DomainError(
            ErrorCode.VALIDATION_INVALID, errors={"reason": ["A reason is required."]}
        )
    return reason


# --- drivers -------------------------------------------------------------------------------


def _lock_driver(actor: User, driver: Driver) -> Driver:
    locked = selectors.drivers_in_scope(actor).select_for_update().filter(pk=driver.pk).first()
    if locked is None:
        raise DomainError(ErrorCode.NOT_FOUND)
    return locked


def _ensure_editable_driver(driver: Driver) -> None:
    if driver.is_archived:
        raise DomainError(ErrorCode.DRIVER_ARCHIVED)


def _has_profile(user: User) -> bool:
    return Driver.objects.filter(user=user).exists()


@transaction.atomic
def create_driver(
    *, actor: User, user_id: uuid.UUID, fields: DriverFields, request: HttpRequest
) -> Driver:
    """Create the profile of an existing active user who has the `driver` role."""
    user = (
        User.objects.for_company(actor.company)
        .filter(public_id=user_id, is_active=True, roles__code=RoleCode.DRIVER)
        .first()
    )
    if user is None:
        raise invalid_reference("user_id", "Driver user not found.")
    branch = _branch_in_scope(actor, fields.branch_id)
    if _has_profile(user):
        raise DomainError(ErrorCode.DRIVER_ALREADY_EXISTS)
    driver = Driver(company=actor.company, user=user, branch=branch, phone=fields.phone)
    driver.full_clean(validate_constraints=False)
    try:
        with transaction.atomic():
            driver.save()
    except IntegrityError:
        if _has_profile(user):  # lost a race with a concurrent create
            raise DomainError(ErrorCode.DRIVER_ALREADY_EXISTS) from None
        raise
    _audit(AuditAction.DRIVER_CREATED, actor, driver, "driver", request)
    return driver


@transaction.atomic
def update_driver(*, actor: User, driver: Driver, fields: DriverFields) -> Driver:
    driver = _lock_driver(actor, driver)
    _ensure_editable_driver(driver)
    driver.branch = _branch_in_scope(actor, fields.branch_id)
    driver.phone = fields.phone
    driver.full_clean(validate_constraints=False)
    driver.save()
    return driver


@transaction.atomic
def set_driver_status(
    *, actor: User, driver: Driver, status: DriverStatus, reason: str, request: HttpRequest
) -> Driver:
    driver = _lock_driver(actor, driver)
    _ensure_editable_driver(driver)
    new_reason = _status_reason(status, DriverStatus.ACTIVE, reason)
    if driver.status == status and driver.status_reason == new_reason:
        return driver
    changed = driver.status != status
    driver.status = status
    driver.status_reason = new_reason
    driver.full_clean(validate_constraints=False)
    driver.save()
    if changed:
        _audit(
            AuditAction.DRIVER_STATUS_CHANGED, actor, driver, "driver", request, {"status": status}
        )
    return driver


@transaction.atomic
def archive_driver(*, actor: User, driver: Driver, request: HttpRequest) -> Driver:
    """Archive the driver. Archiving twice is a no-op."""
    driver = _lock_driver(actor, driver)
    if driver.is_archived:
        return driver
    driver.archived_at = timezone.now()
    driver.save(update_fields=["archived_at", "updated_at"])
    _audit(AuditAction.DRIVER_ARCHIVED, actor, driver, "driver", request)
    return driver


@transaction.atomic
def add_leave(
    *,
    actor: User,
    driver: Driver,
    starts_on: date,
    ends_on: date,
    reason: str,
    request: HttpRequest,
) -> DriverLeave:
    driver = _lock_driver(actor, driver)
    _ensure_editable_driver(driver)
    leave = DriverLeave(
        company=actor.company,
        driver=driver,
        starts_on=starts_on,
        ends_on=ends_on,
        reason=reason.strip(),
    )
    leave.full_clean(exclude=["driver"], validate_constraints=False)
    if _overlapping_leave(driver, starts_on, ends_on):
        raise DomainError(ErrorCode.DRIVER_LEAVE_OVERLAP)
    try:
        with transaction.atomic():
            leave.save()
    except IntegrityError:
        if _overlapping_leave(driver, starts_on, ends_on):  # lost a race (also locked above)
            raise DomainError(ErrorCode.DRIVER_LEAVE_OVERLAP) from None
        raise
    _audit(AuditAction.DRIVER_LEAVE_ADDED, actor, leave, "driver_leave", request)
    return leave


def _overlapping_leave(driver: Driver, starts_on: date, ends_on: date) -> bool:
    return (
        selectors.leaves_of(driver).filter(starts_on__lte=ends_on, ends_on__gte=starts_on).exists()
    )


@transaction.atomic
def delete_leave(*, actor: User, driver: Driver, leave: DriverLeave, request: HttpRequest) -> None:
    """Delete leave that has not started yet. Leave in progress or past is history."""
    driver = _lock_driver(actor, driver)
    locked = selectors.leaves_of(driver).select_for_update().filter(pk=leave.pk).first()
    if locked is None:
        raise DomainError(ErrorCode.NOT_FOUND)
    if locked.starts_on <= actor.company.local_today():
        raise DomainError(ErrorCode.DRIVER_LEAVE_STARTED)
    _audit(AuditAction.DRIVER_LEAVE_DELETED, actor, locked, "driver_leave", request)
    locked.delete()


# --- vehicles ------------------------------------------------------------------------------


def _lock_vehicle(actor: User, vehicle: Vehicle) -> Vehicle:
    locked = (
        selectors.vehicles_in_scope(actor)
        .select_for_update(of=("self",))
        .filter(pk=vehicle.pk)
        .first()
    )
    if locked is None:
        raise DomainError(ErrorCode.NOT_FOUND)
    return locked


def _ensure_editable_vehicle(vehicle: Vehicle) -> None:
    if vehicle.is_archived:
        raise DomainError(ErrorCode.VEHICLE_ARCHIVED)


def _apply_vehicle_fields(actor: User, vehicle: Vehicle, fields: VehicleFields) -> None:
    for name, value in asdict(fields).items():
        if name != "branch_id":
            setattr(vehicle, name, value)
    vehicle.registration_number = normalize_registration(fields.registration_number)
    vehicle.branch = _branch_in_scope(actor, fields.branch_id)
    vehicle.full_clean(
        validate_unique=False, validate_constraints=False
    )  # the DB enforces constraints; uniqueness is checked with a clear code


def _check_registration_free(vehicle: Vehicle) -> None:
    taken = Vehicle.objects.for_company(vehicle.company).filter(
        registration_number=vehicle.registration_number
    )
    if vehicle.pk:
        taken = taken.exclude(pk=vehicle.pk)
    if taken.exists():
        raise DomainError(ErrorCode.VEHICLE_DUPLICATE_REGISTRATION)


def _save_vehicle(vehicle: Vehicle) -> None:
    _check_registration_free(vehicle)
    try:
        with transaction.atomic():
            vehicle.save()
    except IntegrityError:
        _check_registration_free(vehicle)  # lost a race with a concurrent create or edit
        raise


@transaction.atomic
def create_vehicle(*, actor: User, fields: VehicleFields, request: HttpRequest) -> Vehicle:
    vehicle = Vehicle(company=actor.company)
    _apply_vehicle_fields(actor, vehicle, fields)
    _save_vehicle(vehicle)
    _audit(AuditAction.VEHICLE_CREATED, actor, vehicle, "vehicle", request)
    return vehicle


@transaction.atomic
def update_vehicle(*, actor: User, vehicle: Vehicle, fields: VehicleFields) -> Vehicle:
    vehicle = _lock_vehicle(actor, vehicle)
    _ensure_editable_vehicle(vehicle)
    _apply_vehicle_fields(actor, vehicle, fields)
    _save_vehicle(vehicle)
    return vehicle


@transaction.atomic
def set_vehicle_status(
    *, actor: User, vehicle: Vehicle, status: VehicleStatus, reason: str, request: HttpRequest
) -> Vehicle:
    vehicle = _lock_vehicle(actor, vehicle)
    _ensure_editable_vehicle(vehicle)
    new_reason = _status_reason(status, VehicleStatus.ACTIVE, reason)
    if vehicle.status == status and vehicle.status_reason == new_reason:
        return vehicle
    changed = vehicle.status != status
    vehicle.status = status
    vehicle.status_reason = new_reason
    vehicle.full_clean(validate_unique=False, validate_constraints=False)
    vehicle.save()
    if changed:
        _audit(
            AuditAction.VEHICLE_STATUS_CHANGED,
            actor,
            vehicle,
            "vehicle",
            request,
            {"status": status},
        )
    return vehicle


@transaction.atomic
def archive_vehicle(*, actor: User, vehicle: Vehicle, request: HttpRequest) -> Vehicle:
    """Archive the vehicle. Archiving twice is a no-op."""
    vehicle = _lock_vehicle(actor, vehicle)
    if vehicle.is_archived:
        return vehicle
    vehicle.archived_at = timezone.now()
    vehicle.save(update_fields=["archived_at", "updated_at"])
    _audit(AuditAction.VEHICLE_ARCHIVED, actor, vehicle, "vehicle", request)
    return vehicle


# --- documents -----------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentFields:
    type: str
    upload_id: uuid.UUID
    number: str = ""
    issued_on: date | None = None
    expires_on: date | None = None
    categories: list[str] | None = None  # driver documents only


def _add_document(
    *,
    actor: User,
    document: DriverDocument | VehicleDocument,
    fields: DocumentFields,
    request: HttpRequest,
) -> None:
    """Validate, claim the upload and save, all in the caller's transaction, so a failure
    leaves the upload free to use again. The DB enforces the type rules as well."""
    document.type = fields.type
    document.number = fields.number.strip()
    document.issued_on = fields.issued_on
    document.expires_on = fields.expires_on
    if isinstance(document, DriverDocument):
        document.categories = list(fields.categories or [])
    document.full_clean(exclude=["driver", "vehicle", "upload"], validate_constraints=False)
    try:
        document.upload = uploads.attach_upload(
            actor=actor, upload_id=fields.upload_id, purpose=UploadPurpose.FLEET_DOCUMENT
        )
    except DomainError as error:
        if error.error_code == ErrorCode.VALIDATION_INVALID_REFERENCE:
            raise invalid_reference("upload_id", "Upload not found.") from None
        raise
    document.save()
    # IDs and enums only: the number is a personal identifier (rules.md §3).
    _audit(
        AuditAction.FLEET_DOCUMENT_ADDED,
        actor,
        document,
        "fleet_document",
        request,
        {"type": fields.type},
    )


@transaction.atomic
def add_driver_document(
    *, actor: User, driver: Driver, fields: DocumentFields, request: HttpRequest
) -> DriverDocument:
    driver = _lock_driver(actor, driver)
    _ensure_editable_driver(driver)
    document = DriverDocument(company=actor.company, driver=driver)
    _add_document(actor=actor, document=document, fields=fields, request=request)
    return document


@transaction.atomic
def add_vehicle_document(
    *, actor: User, vehicle: Vehicle, fields: DocumentFields, request: HttpRequest
) -> VehicleDocument:
    vehicle = _lock_vehicle(actor, vehicle)
    _ensure_editable_vehicle(vehicle)
    document = VehicleDocument(company=actor.company, vehicle=vehicle)
    _add_document(actor=actor, document=document, fields=fields, request=request)
    return document


def _scoped_document[D: DriverDocument | VehicleDocument](
    actor: User, document: D, *, lock: bool
) -> D:
    """Re-find `document` through the actor's company and branch scope, so a caller that
    skipped the selector still cannot reach another tenant's or branch's file."""
    documents: QuerySet[D]
    if isinstance(document, DriverDocument):
        documents = DriverDocument.objects.filter(  # type: ignore[assignment]
            driver__in=selectors.drivers_in_scope(actor)
        )
    else:
        documents = VehicleDocument.objects.filter(  # type: ignore[assignment]
            vehicle__in=selectors.vehicles_in_scope(actor)
        )
    if lock:
        documents = documents.select_for_update(of=("self",))
    found = documents.select_related("upload").filter(pk=document.pk).first()
    if found is None:
        raise DomainError(ErrorCode.NOT_FOUND)
    return found


@transaction.atomic
def archive_document[D: DriverDocument | VehicleDocument](
    *, actor: User, document: D, request: HttpRequest
) -> D:
    """Archive the document so it no longer counts. Repeating it is a no-op."""
    locked = _scoped_document(actor, document, lock=True)
    if locked.is_archived:
        return locked
    locked.archived_at = timezone.now()
    locked.save(update_fields=["archived_at", "updated_at"])
    _audit(AuditAction.FLEET_DOCUMENT_ARCHIVED, actor, locked, "fleet_document", request)
    return locked


def download_document(
    *, actor: User, document: DriverDocument | VehicleDocument, request: HttpRequest
) -> tuple[str, datetime]:
    """A 5-minute presigned GET for the document's file. The view has checked `fleet.manage`;
    each download is audited because the file is personal data."""
    locked = _scoped_document(actor, document, lock=False)
    url, expires_at = uploads.presign_download(locked.upload)
    _audit(AuditAction.FLEET_DOCUMENT_DOWNLOADED, actor, locked, "fleet_document", request)
    return url, expires_at
