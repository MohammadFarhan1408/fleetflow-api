from django.core.exceptions import ValidationError
from rest_framework_simplejwt import authentication
from rest_framework_simplejwt.exceptions import ExpiredTokenError, TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken, Token

from apps.accounts.models import TokenFamily, User
from apps.accounts.tokens import FAMILY_CLAIM, token_error
from apps.core.errors import DomainError, ErrorCode


def ensure_account_usable(user: User) -> None:
    """Shared gate for login, refresh and every authenticated request."""
    if not user.is_active:
        raise token_error(ErrorCode.AUTH_USER_DISABLED)
    if not user.company.is_active:
        raise DomainError(ErrorCode.AUTH_COMPANY_SUSPENDED)


class JWTAuthentication(authentication.JWTAuthentication):
    """Bearer access-token authentication with FleetFlow's error codes and account gates.

    The user is loaded through the token's session (family), in one query, and only if
    that session is not revoked. So logout, refresh-reuse detection, password reset and
    deactivation end access immediately, not when the 15-minute access token expires.
    Disabled users and suspended companies are also rejected on every request.
    """

    def get_validated_token(self, raw_token: bytes) -> Token:
        try:
            return AccessToken(raw_token)  # type: ignore[arg-type]  # accepts str or bytes
        except ExpiredTokenError as exc:
            raise token_error(ErrorCode.AUTH_TOKEN_EXPIRED) from exc
        except TokenError as exc:
            raise token_error(ErrorCode.AUTH_TOKEN_INVALID) from exc

    # simplejwt annotates this with an unbound TypeVar, which no concrete override matches.
    def get_user(self, validated_token: Token) -> User:  # type: ignore[override]
        try:
            family = (
                TokenFamily.objects.select_related("user__company")
                .filter(id=validated_token[FAMILY_CLAIM], revoked_at__isnull=True)
                .first()
            )
        except (KeyError, ValidationError) as exc:
            raise token_error(ErrorCode.AUTH_TOKEN_INVALID) from exc

        if family is None or str(family.user.public_id) != validated_token.get(
            api_settings.USER_ID_CLAIM
        ):
            raise token_error(ErrorCode.AUTH_TOKEN_INVALID)

        ensure_account_usable(family.user)
        return family.user
