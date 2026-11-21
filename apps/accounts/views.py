from dataclasses import asdict
from typing import Any

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import services
from apps.accounts.models import User
from apps.accounts.permissions import get_user_permissions
from apps.accounts.serializers import (
    LoginSerializer,
    MeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RefreshTokenSerializer,
    TokenPairSerializer,
)
from apps.accounts.throttles import (
    LoginThrottle,
    PasswordResetEmailThrottle,
    PasswordResetIPThrottle,
    RefreshThrottle,
)
from apps.core.serializers import ErrorSerializer

ERROR = OpenApiResponse(ErrorSerializer)


class PublicAuthView(APIView):
    # Reachable without a session. No authentication classes: a stale or expired
    # Authorization header must not stop a client from logging in, refreshing or logging
    # out. (A comment, not a docstring, so it does not leak into the OpenAPI schema.)
    authentication_classes = []
    permission_classes = [AllowAny]


class LoginView(PublicAuthView):
    """Log in with email and password; returns an access and refresh token pair."""

    throttle_classes = [LoginThrottle]

    @extend_schema(
        request=LoginSerializer,
        responses={200: TokenPairSerializer, 400: ERROR, 401: ERROR, 403: ERROR, 429: ERROR},
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pair = services.login(**serializer.validated_data, request=request)
        return Response(TokenPairSerializer(asdict(pair)).data)


class RefreshView(PublicAuthView):
    """Rotate a refresh token. Retrying the same token within 30 s returns a fresh pair;
    reusing it later ends the session (401 `auth.token_invalid`)."""

    throttle_classes = [RefreshThrottle]

    @extend_schema(
        request=RefreshTokenSerializer,
        responses={200: TokenPairSerializer, 400: ERROR, 401: ERROR, 403: ERROR, 429: ERROR},
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = RefreshTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pair = services.refresh_session(
            raw_refresh=serializer.validated_data["refresh"], request=request
        )
        return Response(TokenPairSerializer(asdict(pair)).data)


class LogoutView(PublicAuthView):
    """End this device's session, including its access token. Idempotent."""

    @extend_schema(
        request=RefreshTokenSerializer,
        responses={204: None, 400: ERROR, 401: ERROR},
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = RefreshTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.logout(raw_refresh=serializer.validated_data["refresh"], request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetRequestView(PublicAuthView):
    """Email a password reset link. Always 202, whether or not the account exists."""

    throttle_classes = [PasswordResetEmailThrottle, PasswordResetIPThrottle]

    @extend_schema(
        request=PasswordResetRequestSerializer,
        responses={202: None, 400: ERROR, 429: ERROR},
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.request_password_reset(email=serializer.validated_data["email"])
        # Same answer whether or not the account exists.
        return Response(status=status.HTTP_202_ACCEPTED)


class PasswordResetConfirmView(PublicAuthView):
    """Set a new password from an emailed reset or invite link; ends every session."""

    @extend_schema(
        request=PasswordResetConfirmSerializer,
        responses={204: None, 400: ERROR},
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.confirm_password_reset(**serializer.validated_data, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """Session bootstrap: the user, company, branches, roles and permissions."""

    @extend_schema(responses={200: MeSerializer, 401: ERROR, 403: ERROR})
    def get(self, request: Request) -> Response:
        user: User = request.user  # type: ignore[assignment]  # IsAuthenticated guarantees User
        payload: dict[str, Any] = {
            "user": user,
            "company": user.company,
            "branches": user.branches.order_by("name"),
            "roles": sorted(user.roles.values_list("code", flat=True)),
            "permissions": sorted(get_user_permissions(user)),
        }
        return Response(MeSerializer(payload).data)
