import logging
import math
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import set_rollback

from apps.core.errors import ERROR_SPECS, DomainError, ErrorCode, error_payload

logger = logging.getLogger(__name__)

# DRF exceptions that map one-to-one onto a registered code. DomainError, ValidationError
# and Throttled carry extra data and are handled before this table is consulted.
_DRF_EXCEPTION_CODES: tuple[tuple[type[exceptions.APIException], ErrorCode], ...] = (
    (exceptions.ParseError, ErrorCode.REQUEST_MALFORMED),
    (exceptions.NotAuthenticated, ErrorCode.AUTH_NOT_AUTHENTICATED),
    (exceptions.AuthenticationFailed, ErrorCode.AUTH_AUTHENTICATION_FAILED),
    (exceptions.PermissionDenied, ErrorCode.PERMISSION_DENIED),
    (exceptions.NotFound, ErrorCode.NOT_FOUND),
    (exceptions.MethodNotAllowed, ErrorCode.METHOD_NOT_ALLOWED),
    (exceptions.NotAcceptable, ErrorCode.NOT_ACCEPTABLE),
    (exceptions.UnsupportedMediaType, ErrorCode.UNSUPPORTED_MEDIA_TYPE),
)


def _validation_errors(detail: Any) -> dict[str, Any]:
    if isinstance(detail, dict):
        return detail
    # Serializer-level errors arrive as a list; give them the conventional key.
    return {"non_field_errors": detail}


def _error_response(
    code: ErrorCode,
    detail: str | None = None,
    errors: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    return Response(
        error_payload(code, detail, errors),
        status=ERROR_SPECS[code].status,
        headers=headers,
    )


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response:
    """Render every exception raised inside a DRF view as the error envelope."""
    # DRF's default handler would otherwise roll back; we replace it, so do it here.
    set_rollback()

    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    if isinstance(exc, DomainError):
        return _error_response(exc.error_code, str(exc.detail), exc.errors, exc.headers)

    if isinstance(exc, exceptions.ValidationError):
        return _error_response(ErrorCode.VALIDATION_INVALID, errors=_validation_errors(exc.detail))

    if isinstance(exc, exceptions.Throttled):
        # `wait` is set by Throttled.__init__ but missing from the DRF type stubs.
        wait: float | None = getattr(exc, "wait", None)
        headers = {"Retry-After": str(math.ceil(wait))} if wait is not None else None
        return _error_response(ErrorCode.THROTTLED, headers=headers)

    for exc_class, code in _DRF_EXCEPTION_CODES:
        if isinstance(exc, exc_class):
            headers = {}
            auth_header = getattr(exc, "auth_header", None)
            if auth_header:
                headers["WWW-Authenticate"] = auth_header
            return _error_response(code, headers=headers or None)

    # Unregistered exceptions are bugs: log with context, return nothing internal.
    logger.exception(
        "Unhandled exception in API view",
        extra={"view": type(context.get("view")).__name__},
    )
    return _error_response(ErrorCode.SERVER_ERROR)
