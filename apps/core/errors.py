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
