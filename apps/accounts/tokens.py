"""JWT issue and decode helpers on top of simplejwt (architecture §7)."""

from dataclasses import dataclass
from typing import Any

from rest_framework_simplejwt.exceptions import ExpiredTokenError, TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.utils import datetime_from_epoch

from apps.accounts.models import RefreshTokenRecord, TokenFamily, User
from apps.core.errors import DomainError, ErrorCode

FAMILY_CLAIM = "fam"
# RFC 6750 challenge returned with every 401 from bearer-token authentication.
BEARER_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="api"'}


@dataclass(frozen=True)
class TokenPair:
    access: str
    refresh: str


def issue_token_pair(user: User, family: TokenFamily) -> TokenPair:
    refresh = RefreshToken.for_user(user)
    refresh[FAMILY_CLAIM] = str(family.id)
    RefreshTokenRecord.objects.create(
        jti=refresh[api_settings.JTI_CLAIM],
        family=family,
        expires_at=datetime_from_epoch(refresh["exp"]),
    )
    # The access token copies custom claims (including `fam`) from the refresh token.
    return TokenPair(access=str(refresh.access_token), refresh=str(refresh))


def token_error(code: ErrorCode) -> DomainError:
    return DomainError(code, headers=BEARER_CHALLENGE)


def decode_refresh_token(raw: str) -> dict[str, Any]:
    """Verify signature, expiry and token type. Raises token_expired or token_invalid."""
    try:
        return RefreshToken(raw).payload  # type: ignore[arg-type]  # accepts str or bytes
    except ExpiredTokenError as exc:
        raise token_error(ErrorCode.AUTH_TOKEN_EXPIRED) from exc
    except TokenError as exc:
        raise token_error(ErrorCode.AUTH_TOKEN_INVALID) from exc
