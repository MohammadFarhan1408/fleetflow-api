import ipaddress
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser
from django.http import HttpRequest
from rest_framework.throttling import BaseThrottle

from apps.audit.models import AuditAction, AuditLog
from apps.core.request_id import get_request_id
from apps.tenancy.models import Company

_USER_AGENT_MAX = 512


def client_ip(request: HttpRequest) -> str | None:
    """Client IP honouring REST_FRAMEWORK["NUM_PROXIES"], the same rule DRF throttles use."""
    ident = BaseThrottle().get_ident(request)  # type: ignore[arg-type]  # only reads META
    try:
        return str(ipaddress.ip_address(ident))
    except ValueError:
        return None


def record(
    action: AuditAction,
    *,
    company: Company,
    actor: AbstractBaseUser | None = None,
    object_type: str = "",
    object_id: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request: HttpRequest | None = None,
) -> AuditLog:
    """Append one audit entry. Call inside the same transaction as the audited change."""
    return AuditLog.objects.create(
        company=company,
        actor_id=actor.pk if actor is not None else None,
        action=action,
        object_type=object_type,
        object_id=object_id,
        before=before,
        after=after,
        ip=client_ip(request) if request is not None else None,
        user_agent=(request.headers.get("User-Agent", "")[:_USER_AGENT_MAX] if request else ""),
        request_id=get_request_id() or "",
    )
