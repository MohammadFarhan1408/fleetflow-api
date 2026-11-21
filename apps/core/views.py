import logging

import redis
from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from apps.core.errors import ErrorCode, error_payload

logger = logging.getLogger(__name__)

# Keeps the readiness probe fast when a dependency hangs instead of refusing.
_REDIS_TIMEOUT_SECONDS = 2


def _database_ok() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        logger.warning("Readiness check failed: database", exc_info=True)
        return False
    return True


def _redis_ok() -> bool:
    client = redis.Redis.from_url(
        settings.REDIS_URL,
        socket_timeout=_REDIS_TIMEOUT_SECONDS,
        socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
    )
    try:
        client.ping()
    except redis.RedisError:
        logger.warning("Readiness check failed: redis", exc_info=True)
        return False
    finally:
        client.close()
    return True


@require_GET
def health_live(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@require_GET
def health_ready(request: HttpRequest) -> JsonResponse:
    checks = {"database": _database_ok(), "redis": _redis_ok()}
    healthy = all(checks.values())
    # Only pass/fail per check: never error text, hosts or credentials (rules.md §3).
    return JsonResponse(
        {
            "status": "ok" if healthy else "unavailable",
            "checks": {name: "ok" if ok else "fail" for name, ok in checks.items()},
        },
        status=200 if healthy else 503,
    )


def bad_request(request: HttpRequest, exception: Exception) -> JsonResponse:
    """Django-level 400, e.g. a disallowed Host header (SuspiciousOperation)."""
    return JsonResponse(error_payload(ErrorCode.REQUEST_MALFORMED), status=400)


def permission_denied(request: HttpRequest, exception: Exception) -> JsonResponse:
    """Django-level 403 raised outside DRF views."""
    return JsonResponse(error_payload(ErrorCode.PERMISSION_DENIED), status=403)


def not_found(request: HttpRequest, exception: Exception) -> JsonResponse:
    """Django-level 404 (no URL matched), rendered as the API envelope instead of HTML."""
    return JsonResponse(error_payload(ErrorCode.NOT_FOUND), status=404)


def server_error(request: HttpRequest) -> JsonResponse:
    """Django-level 500 for exceptions outside DRF views; never includes internals."""
    return JsonResponse(error_payload(ErrorCode.SERVER_ERROR), status=500)
