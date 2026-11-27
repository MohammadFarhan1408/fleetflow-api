"""Error code registry and the single builder of the API error envelope (architecture §6).

Every error the API returns has a registered code. Clients branch on `code`, so codes are
part of the API contract (rules.md §6): never rename or remove one without a changelog entry.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from rest_framework.exceptions import APIException

from apps.core.request_id import get_request_id


class ErrorType(StrEnum):
    VALIDATION = "validation_error"
    AUTHENTICATION = "authentication_error"
    PERMISSION = "permission_error"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    CLIENT = "client_error"
    SERVER = "server_error"
    UPSTREAM = "upstream_error"


class ErrorCode(StrEnum):
    VALIDATION_INVALID = "validation.invalid"
    VALIDATION_INVALID_REFERENCE = "validation.invalid_reference"
    REQUEST_MALFORMED = "request.malformed"
    AUTH_NOT_AUTHENTICATED = "auth.not_authenticated"
    AUTH_AUTHENTICATION_FAILED = "auth.authentication_failed"
    AUTH_INVALID_CREDENTIALS = "auth.invalid_credentials"
    AUTH_USER_DISABLED = "auth.user_disabled"
    AUTH_COMPANY_SUSPENDED = "auth.company_suspended"
    AUTH_TOKEN_EXPIRED = "auth.token_expired"
    AUTH_TOKEN_INVALID = "auth.token_invalid"
    AUTH_ACCOUNT_LOCKED = "auth.account_locked"
    AUTH_INVALID_RESET_TOKEN = "auth.invalid_reset_token"
    PERMISSION_DENIED = "permission.denied"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    NOT_ACCEPTABLE = "not_acceptable"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    THROTTLED = "throttled"
    APP_UPDATE_REQUIRED = "app.update_required"
    APP_INVALID_VERSION = "app.invalid_version"
    IDEMPOTENCY_INVALID_KEY = "idempotency.invalid_key"
    IDEMPOTENCY_KEY_REUSE = "idempotency.key_reuse"
    IDEMPOTENCY_KEY_REQUIRED = "idempotency.key_required"
    USER_EMAIL_TAKEN = "user.email_taken"
    USER_CANNOT_MODIFY_SELF = "user.cannot_modify_self"
    USER_OWNER_REQUIRED = "user.owner_required"
    USER_LAST_OWNER = "user.last_owner"
    CUSTOMER_ARCHIVED = "customer.archived"
    ADDRESS_ARCHIVED = "address.archived"
    CUSTOMER_INACTIVE = "customer.inactive"
    SHIPMENT_INVALID_WEIGHT = "shipment.invalid_weight"
    SHIPMENT_UNSUPPORTED_TYPE = "shipment.unsupported_type"
    SHIPMENT_PAST_SCHEDULE = "shipment.past_schedule"
    SHIPMENT_DUPLICATE_REFERENCE = "shipment.duplicate_reference"
    SHIPMENT_NOT_DRAFT = "shipment.not_draft"
    SHIPMENT_INVALID_TRANSITION = "shipment.invalid_transition"
    SHIPMENT_STATUS_CONFLICT = "shipment.status_conflict"
    DRIVER_ALREADY_EXISTS = "driver.already_exists"
    DRIVER_ARCHIVED = "driver.archived"
    DRIVER_LEAVE_OVERLAP = "driver.leave_overlap"
    DRIVER_LEAVE_STARTED = "driver.leave_started"
    DRIVER_PROFILE_INACTIVE = "driver.profile_inactive"
    DRIVER_SUSPENDED = "driver.suspended"
    SHIFT_ALREADY_OPEN = "shift.already_open"
    SHIFT_NOT_OPEN = "shift.not_open"
    ROUTE_VERSION_CONFLICT = "route.version_conflict"
    ROUTE_INVALID_STATUS = "route.invalid_status"
    ROUTE_SHIPMENT_UNAVAILABLE = "route.shipment_unavailable"
    ROUTE_STOPS_STARTED = "route.stops_started"
    ROUTE_DUPLICATE_STOP = "route.duplicate_stop"
    ROUTE_INVALID_SEQUENCE = "route.invalid_sequence"
    ROUTE_PICKUP_DEPENDENCY = "route.pickup_dependency"
    ROUTE_TOO_MANY_STOPS = "route.too_many_stops"
    VEHICLE_ARCHIVED = "vehicle.archived"
    VEHICLE_DUPLICATE_REGISTRATION = "vehicle.duplicate_registration"
    BRANCH_DUPLICATE = "branch.duplicate"
    UPLOAD_TOO_LARGE = "upload.too_large"
    UPLOAD_SIZE_MISMATCH = "upload.size_mismatch"
    UPLOAD_TYPE_MISMATCH = "upload.type_mismatch"
    UPLOAD_NOT_FOUND = "upload.not_found"
    UPLOAD_NOT_CONFIRMED = "upload.not_confirmed"
    UPLOAD_IN_USE = "upload.in_use"
    STORAGE_UNAVAILABLE = "storage.unavailable"
    SERVER_ERROR = "server_error"


@dataclass(frozen=True)
class ErrorSpec:
    status: int
    type: ErrorType
    title: str


ERROR_SPECS: dict[ErrorCode, ErrorSpec] = {
    ErrorCode.VALIDATION_INVALID: ErrorSpec(400, ErrorType.VALIDATION, "Invalid input"),
    ErrorCode.VALIDATION_INVALID_REFERENCE: ErrorSpec(
        400, ErrorType.VALIDATION, "Referenced object not found"
    ),
    ErrorCode.REQUEST_MALFORMED: ErrorSpec(400, ErrorType.CLIENT, "Malformed request"),
    ErrorCode.AUTH_NOT_AUTHENTICATED: ErrorSpec(
        401, ErrorType.AUTHENTICATION, "Authentication required"
    ),
    ErrorCode.AUTH_AUTHENTICATION_FAILED: ErrorSpec(
        401, ErrorType.AUTHENTICATION, "Authentication failed"
    ),
    ErrorCode.AUTH_INVALID_CREDENTIALS: ErrorSpec(
        401, ErrorType.AUTHENTICATION, "Invalid email or password"
    ),
    ErrorCode.AUTH_USER_DISABLED: ErrorSpec(401, ErrorType.AUTHENTICATION, "Account disabled"),
    ErrorCode.AUTH_COMPANY_SUSPENDED: ErrorSpec(403, ErrorType.PERMISSION, "Company suspended"),
    # Clients refresh on token_expired only; token_invalid means log in again.
    ErrorCode.AUTH_TOKEN_EXPIRED: ErrorSpec(401, ErrorType.AUTHENTICATION, "Token expired"),
    ErrorCode.AUTH_TOKEN_INVALID: ErrorSpec(401, ErrorType.AUTHENTICATION, "Token invalid"),
    ErrorCode.AUTH_ACCOUNT_LOCKED: ErrorSpec(
        429, ErrorType.RATE_LIMITED, "Too many failed login attempts"
    ),
    ErrorCode.AUTH_INVALID_RESET_TOKEN: ErrorSpec(
        400, ErrorType.VALIDATION, "Invalid or expired password reset link"
    ),
    ErrorCode.PERMISSION_DENIED: ErrorSpec(403, ErrorType.PERMISSION, "Permission denied"),
    ErrorCode.NOT_FOUND: ErrorSpec(404, ErrorType.NOT_FOUND, "Not found"),
    ErrorCode.METHOD_NOT_ALLOWED: ErrorSpec(405, ErrorType.CLIENT, "Method not allowed"),
    ErrorCode.NOT_ACCEPTABLE: ErrorSpec(406, ErrorType.CLIENT, "Not acceptable"),
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: ErrorSpec(415, ErrorType.CLIENT, "Unsupported media type"),
    ErrorCode.THROTTLED: ErrorSpec(429, ErrorType.RATE_LIMITED, "Too many requests"),
    ErrorCode.APP_UPDATE_REQUIRED: ErrorSpec(426, ErrorType.CLIENT, "App update required"),
    ErrorCode.APP_INVALID_VERSION: ErrorSpec(400, ErrorType.CLIENT, "Invalid app version"),
    ErrorCode.IDEMPOTENCY_INVALID_KEY: ErrorSpec(
        400, ErrorType.CLIENT, "Invalid Idempotency-Key header"
    ),
    ErrorCode.IDEMPOTENCY_KEY_REUSE: ErrorSpec(
        422, ErrorType.CONFLICT, "Idempotency-Key reused with a different request"
    ),
    ErrorCode.IDEMPOTENCY_KEY_REQUIRED: ErrorSpec(
        400, ErrorType.CLIENT, "Idempotency-Key header required"
    ),
    ErrorCode.USER_EMAIL_TAKEN: ErrorSpec(409, ErrorType.CONFLICT, "Email already in use"),
    ErrorCode.USER_CANNOT_MODIFY_SELF: ErrorSpec(
        409, ErrorType.CONFLICT, "You cannot change your own roles, branches or status"
    ),
    ErrorCode.USER_OWNER_REQUIRED: ErrorSpec(
        403, ErrorType.PERMISSION, "Only an owner can grant, revoke or modify owners"
    ),
    ErrorCode.USER_LAST_OWNER: ErrorSpec(
        409, ErrorType.CONFLICT, "The company must keep at least one active owner"
    ),
    ErrorCode.CUSTOMER_ARCHIVED: ErrorSpec(409, ErrorType.CONFLICT, "Customer is archived"),
    ErrorCode.ADDRESS_ARCHIVED: ErrorSpec(409, ErrorType.CONFLICT, "Address is archived"),
    ErrorCode.CUSTOMER_INACTIVE: ErrorSpec(400, ErrorType.VALIDATION, "Customer is archived"),
    ErrorCode.SHIPMENT_INVALID_WEIGHT: ErrorSpec(
        400, ErrorType.VALIDATION, "Invalid shipment weight"
    ),
    ErrorCode.SHIPMENT_UNSUPPORTED_TYPE: ErrorSpec(
        400, ErrorType.VALIDATION, "Unsupported service type"
    ),
    ErrorCode.SHIPMENT_PAST_SCHEDULE: ErrorSpec(
        400, ErrorType.VALIDATION, "Scheduled date is in the past"
    ),
    ErrorCode.SHIPMENT_DUPLICATE_REFERENCE: ErrorSpec(
        409, ErrorType.CONFLICT, "External reference already used"
    ),
    ErrorCode.SHIPMENT_NOT_DRAFT: ErrorSpec(
        409, ErrorType.CONFLICT, "Only draft shipments can be changed"
    ),
    ErrorCode.SHIPMENT_INVALID_TRANSITION: ErrorSpec(
        409, ErrorType.CONFLICT, "This status change is not allowed"
    ),
    ErrorCode.SHIPMENT_STATUS_CONFLICT: ErrorSpec(
        409, ErrorType.CONFLICT, "The shipment status has changed; reload and retry"
    ),
    ErrorCode.DRIVER_ALREADY_EXISTS: ErrorSpec(
        409, ErrorType.CONFLICT, "This user already has a driver profile"
    ),
    ErrorCode.DRIVER_ARCHIVED: ErrorSpec(409, ErrorType.CONFLICT, "Driver is archived"),
    ErrorCode.DRIVER_LEAVE_OVERLAP: ErrorSpec(
        409, ErrorType.CONFLICT, "The driver already has leave in this period"
    ),
    ErrorCode.DRIVER_LEAVE_STARTED: ErrorSpec(
        409, ErrorType.CONFLICT, "Leave that has started cannot be deleted"
    ),
    ErrorCode.DRIVER_PROFILE_INACTIVE: ErrorSpec(
        403, ErrorType.PERMISSION, "You have no active driver profile"
    ),
    ErrorCode.DRIVER_SUSPENDED: ErrorSpec(409, ErrorType.CONFLICT, "The driver is suspended"),
    ErrorCode.SHIFT_ALREADY_OPEN: ErrorSpec(409, ErrorType.CONFLICT, "A shift is already open"),
    ErrorCode.SHIFT_NOT_OPEN: ErrorSpec(409, ErrorType.CONFLICT, "There is no open shift"),
    ErrorCode.ROUTE_VERSION_CONFLICT: ErrorSpec(
        409, ErrorType.CONFLICT, "The route has changed; reload and retry"
    ),
    ErrorCode.ROUTE_INVALID_STATUS: ErrorSpec(
        409, ErrorType.CONFLICT, "The route's status does not allow this"
    ),
    ErrorCode.ROUTE_SHIPMENT_UNAVAILABLE: ErrorSpec(
        409, ErrorType.CONFLICT, "The shipment cannot be added to a route"
    ),
    ErrorCode.ROUTE_STOPS_STARTED: ErrorSpec(
        409, ErrorType.CONFLICT, "The route has stops that were already worked"
    ),
    ErrorCode.ROUTE_DUPLICATE_STOP: ErrorSpec(
        400, ErrorType.VALIDATION, "The shipment already has this stop on the route"
    ),
    ErrorCode.ROUTE_INVALID_SEQUENCE: ErrorSpec(
        400, ErrorType.VALIDATION, "The stop order must list every stop exactly once"
    ),
    ErrorCode.ROUTE_PICKUP_DEPENDENCY: ErrorSpec(
        400, ErrorType.VALIDATION, "A delivery must come after its pickup"
    ),
    ErrorCode.ROUTE_TOO_MANY_STOPS: ErrorSpec(
        400, ErrorType.VALIDATION, "The route has the most stops it can hold"
    ),
    ErrorCode.VEHICLE_ARCHIVED: ErrorSpec(409, ErrorType.CONFLICT, "Vehicle is archived"),
    ErrorCode.VEHICLE_DUPLICATE_REGISTRATION: ErrorSpec(
        409, ErrorType.CONFLICT, "Registration number already in use"
    ),
    ErrorCode.BRANCH_DUPLICATE: ErrorSpec(
        409, ErrorType.CONFLICT, "Branch name or code already in use"
    ),
    ErrorCode.UPLOAD_TOO_LARGE: ErrorSpec(400, ErrorType.VALIDATION, "File is too large"),
    ErrorCode.UPLOAD_SIZE_MISMATCH: ErrorSpec(
        400, ErrorType.VALIDATION, "Uploaded file size does not match the request"
    ),
    ErrorCode.UPLOAD_TYPE_MISMATCH: ErrorSpec(
        400, ErrorType.VALIDATION, "File content does not match its declared type"
    ),
    ErrorCode.UPLOAD_NOT_FOUND: ErrorSpec(
        400, ErrorType.VALIDATION, "No uploaded file found; upload it and confirm again"
    ),
    ErrorCode.UPLOAD_NOT_CONFIRMED: ErrorSpec(
        409, ErrorType.CONFLICT, "The upload is not confirmed or was changed after confirmation"
    ),
    ErrorCode.UPLOAD_IN_USE: ErrorSpec(409, ErrorType.CONFLICT, "The upload is already in use"),
    ErrorCode.STORAGE_UNAVAILABLE: ErrorSpec(
        503, ErrorType.UPSTREAM, "File storage is temporarily unavailable"
    ),
    ErrorCode.SERVER_ERROR: ErrorSpec(500, ErrorType.SERVER, "Internal server error"),
}


def error_payload(
    code: ErrorCode,
    detail: str | None = None,
    errors: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = ERROR_SPECS[code]
    payload: dict[str, Any] = {
        "type": spec.type.value,
        "title": spec.title,
        "status": spec.status,
        "code": code.value,
        "detail": detail or spec.title,
        "request_id": get_request_id(),
    }
    if errors:
        payload["errors"] = errors
    return payload


def invalid_reference(key: str, message: str, *, field: str | None = None) -> DomainError:
    """400 for an ID in the request body that does not resolve inside the caller's scope.
    Same answer for "missing" and "another tenant's", so IDs are never confirmed."""
    errors: dict[str, Any] = {key: {field: [message]} if field else [message]}
    return DomainError(ErrorCode.VALIDATION_INVALID_REFERENCE, errors=errors)


class DomainError(APIException):
    """Raise from services and views for any expected failure with a registered code.

    Subclasses APIException so DRF's own handling (transaction rollback, auth headers)
    applies before our exception handler renders the envelope.
    """

    def __init__(
        self,
        code: ErrorCode,
        detail: str | None = None,
        errors: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.error_code = code
        self.status_code = ERROR_SPECS[code].status
        self.errors = errors
        self.headers = headers or {}
        super().__init__(detail=detail or ERROR_SPECS[code].title)
