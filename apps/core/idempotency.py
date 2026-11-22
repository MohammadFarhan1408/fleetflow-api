"""Idempotent mutations via the `Idempotency-Key` header (architecture §20).

Applied per view method with `@idempotent`, not as Django middleware: the JWT user is only
known inside DRF, and replays are scoped to that user.
"""

import hashlib
import json
import re
from collections.abc import Callable
from datetime import timedelta
from functools import wraps
from typing import Any, Concatenate

from django.db import transaction
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import DomainError, ErrorCode
from apps.core.models import IdempotencyRecord

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
RETENTION = timedelta(hours=72)
# Clients send UUIDs; allow any short visible-ASCII token without spaces.
_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{1,255}$")

type ViewMethod[V: APIView] = Callable[Concatenate[V, Request, ...], Response]


def _request_hash(request: Request) -> str:
    body = json.dumps(request.data, sort_keys=True, separators=(",", ":"), default=str)
    fingerprint = f"{request.method}\n{request.path}\n{body}"
    return hashlib.sha256(fingerprint.encode()).hexdigest()


def idempotent[V: APIView](method: ViewMethod[V]) -> ViewMethod[V]:
    """Make an authenticated mutation safe to retry.

    Without the header the request runs normally. With it, the first successful response
    is stored and replayed for later requests from the same user with the same key; the
    same key with a different method, path or body is rejected with 422. The record is
    written in the same transaction as the mutation, so a failed request stores nothing
    and can be retried, and a concurrent duplicate waits on the unique index and then
    replays the winner's response.
    """

    @wraps(method)
    def wrapper(view: V, request: Request, *args: Any, **kwargs: Any) -> Response:
        key = request.headers.get(IDEMPOTENCY_KEY_HEADER)
        if key is None:
            return method(view, request, *args, **kwargs)
        if not _KEY_PATTERN.match(key):
            raise DomainError(ErrorCode.IDEMPOTENCY_INVALID_KEY)

        request_hash = _request_hash(request)
        with transaction.atomic():
            record, created = IdempotencyRecord.objects.get_or_create(
                user=request.user, key=key, defaults={"request_hash": request_hash}
            )
            if not created:
                if record.request_hash != request_hash:
                    raise DomainError(ErrorCode.IDEMPOTENCY_KEY_REUSE)
                return Response(
                    record.response_body,
                    status=record.status_code,
                    headers={REPLAYED_HEADER: "true"},
                )

            # Errors propagate as exceptions and roll back the record with the mutation.
            response = method(view, request, *args, **kwargs)
            record.status_code = response.status_code
            record.response_body = response.data
            record.save(update_fields=["status_code", "response_body"])
            return response

    return wrapper


def purge_expired_records() -> int:
    deleted, _ = IdempotencyRecord.objects.filter(
        created_at__lt=timezone.now() - RETENTION
    ).delete()
    return deleted
