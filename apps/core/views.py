import logging

import redis
from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

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
