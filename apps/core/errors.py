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
    PERMISSION_DENIED = "permission.denied"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    NOT_ACCEPTABLE = "not_acceptable"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    THROTTLED = "throttled"
    APP_UPDATE_REQUIRED = "app.update_required"
    APP_INVALID_VERSION = "app.invalid_version"
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
    ErrorCode.PERMISSION_DENIED: ErrorSpec(403, ErrorType.PERMISSION, "Permission denied"),
    ErrorCode.NOT_FOUND: ErrorSpec(404, ErrorType.NOT_FOUND, "Not found"),
    ErrorCode.METHOD_NOT_ALLOWED: ErrorSpec(405, ErrorType.CLIENT, "Method not allowed"),
    ErrorCode.NOT_ACCEPTABLE: ErrorSpec(406, ErrorType.CLIENT, "Not acceptable"),
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: ErrorSpec(415, ErrorType.CLIENT, "Unsupported media type"),
    ErrorCode.THROTTLED: ErrorSpec(429, ErrorType.RATE_LIMITED, "Too many requests"),
    ErrorCode.APP_UPDATE_REQUIRED: ErrorSpec(426, ErrorType.CLIENT, "App update required"),
    ErrorCode.APP_INVALID_VERSION: ErrorSpec(400, ErrorType.CLIENT, "Invalid app version"),
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
