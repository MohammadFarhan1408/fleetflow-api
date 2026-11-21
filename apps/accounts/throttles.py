"""Scoped throttles for the unauthenticated auth endpoints (rates in settings, §20)."""

import hashlib

from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import normalize_email


def _email_digest(request: Request) -> str:
    email = request.data.get("email") if isinstance(request.data, dict) else None
    normalized = normalize_email(email) if isinstance(email, str) else ""
    # Hashed so raw emails never become cache keys.
    return hashlib.sha256(normalized.encode()).hexdigest()


class LoginThrottle(SimpleRateThrottle):
    """Per IP + email: slows guessing one account from one client."""

    scope = "login"

    def get_cache_key(self, request: Request, view: APIView) -> str:
        ident = f"{self.get_ident(request)}:{_email_digest(request)}"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class RefreshThrottle(SimpleRateThrottle):
    scope = "refresh"

    def get_cache_key(self, request: Request, view: APIView) -> str:
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class PasswordResetEmailThrottle(SimpleRateThrottle):
    """Per email: stops flooding one person's inbox."""

    scope = "password_reset_email"

    def get_cache_key(self, request: Request, view: APIView) -> str:
        return self.cache_format % {"scope": self.scope, "ident": _email_digest(request)}


class PasswordResetIPThrottle(SimpleRateThrottle):
    """Per IP: stops one client probing many emails."""

    scope = "password_reset_ip"

    def get_cache_key(self, request: Request, view: APIView) -> str:
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
