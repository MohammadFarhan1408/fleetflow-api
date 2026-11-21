import re
import uuid
from collections.abc import Callable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse, JsonResponse

from apps.core.errors import ERROR_SPECS, ErrorCode, error_payload
from apps.core.request_id import REQUEST_ID_HEADER, set_request_id

APP_VERSION_HEADER = "X-App-Version"
_VERSION_PATTERN = re.compile(r"^(\d{1,4})\.(\d{1,4})\.(\d{1,6})$")

type Version = tuple[int, int, int]


def parse_version(value: str) -> Version | None:
    match = _VERSION_PATTERN.match(value.strip())
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


class RequestIdMiddleware:
    """Assigns a request ID, echoes it in the response, and exposes it to the envelope.

    A client-supplied ID is kept only if it is a UUID, so arbitrary text never reaches
    logs or audit rows through this header.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _is_uuid(incoming) else str(uuid.uuid4())
        set_request_id(request_id)
        try:
            response = self.get_response(request)
        finally:
            set_request_id(None)
        response[REQUEST_ID_HEADER] = request_id
        return response


class AppVersionMiddleware:
    """Rejects driver-app builds older than DRIVER_APP_MIN_VERSION with 426.

    Only requests that send X-App-Version are checked: the admin BFF and integrations
    don't send it. An empty DRIVER_APP_MIN_VERSION disables the check.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        configured: str = settings.DRIVER_APP_MIN_VERSION
        self.min_version: Version | None = None
        if configured:
            self.min_version = parse_version(configured)
            if self.min_version is None:
                raise ImproperlyConfigured(
                    f"DRIVER_APP_MIN_VERSION must look like 1.2.3, got {configured!r}"
                )

    def __call__(self, request: HttpRequest) -> HttpResponse:
        header = request.headers.get(APP_VERSION_HEADER)
        if self.min_version is None or header is None or not request.path.startswith("/api/"):
            return self.get_response(request)

        version = parse_version(header)
        if version is None:
            return _json_error(ErrorCode.APP_INVALID_VERSION)
        if version < self.min_version:
            return _json_error(ErrorCode.APP_UPDATE_REQUIRED)
        return self.get_response(request)


def _json_error(code: ErrorCode) -> JsonResponse:
    return JsonResponse(error_payload(code), status=ERROR_SPECS[code].status)
