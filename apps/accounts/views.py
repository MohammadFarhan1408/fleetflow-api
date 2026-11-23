import uuid
from dataclasses import asdict
from typing import Any

from django.db.models import QuerySet
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import selectors, services
from apps.accounts.authentication import current_user
from apps.accounts.models import RoleCode, User
from apps.accounts.permissions import (
    USERS_MANAGE,
    USERS_VIEW,
    HasRequiredPermission,
    get_user_permissions,
)
from apps.accounts.serializers import (
    LoginSerializer,
    MeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RefreshTokenSerializer,
    TokenPairSerializer,
    UserInviteSerializer,
    UserSerializer,
    UserUpdateSerializer,
)
from apps.accounts.throttles import (
    LoginThrottle,
    PasswordResetEmailThrottle,
    PasswordResetIPThrottle,
    RefreshThrottle,
)
from apps.core.idempotency import idempotent
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


class UserListCreateView(ListAPIView[User]):
    """List the company's users (`users.view`) or invite a new one (`users.manage`)."""

    permission_classes = [IsAuthenticated, HasRequiredPermission]
    required_permissions = {"GET": USERS_VIEW, "POST": USERS_MANAGE}
    serializer_class = UserSerializer

    def get_queryset(self) -> QuerySet[User]:
        return selectors.users_for_company(current_user(self.request).company)

    @extend_schema(
        request=UserInviteSerializer,
        responses={201: UserSerializer, 400: ERROR, 401: ERROR, 403: ERROR, 409: ERROR, 422: ERROR},
    )
    @idempotent
    def post(self, request: Request) -> Response:
        serializer = UserInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = services.invite_user(
            actor=current_user(request),
            email=data["email"],
            full_name=data["full_name"],
            roles=[RoleCode(code) for code in data["roles"]],
            branch_ids=data["branches"],
            request=request,
        )
        created = selectors.get_company_user(user.company, user.public_id)
        return Response(UserSerializer(created).data, status=status.HTTP_201_CREATED)


class UserDetailView(APIView):
    """Read (`users.view`) or update (`users.manage`) one user of the company."""

    permission_classes = [IsAuthenticated, HasRequiredPermission]
    required_permissions = {"GET": USERS_VIEW, "PATCH": USERS_MANAGE}

    @extend_schema(responses={200: UserSerializer, 401: ERROR, 403: ERROR, 404: ERROR})
    def get(self, request: Request, public_id: uuid.UUID) -> Response:
        user = selectors.get_company_user(current_user(request).company, public_id)
        return Response(UserSerializer(user).data)

    @extend_schema(
        request=UserUpdateSerializer,
        responses={
            200: UserSerializer,
            400: ERROR,
            401: ERROR,
            403: ERROR,
            404: ERROR,
            409: ERROR,
            422: ERROR,
        },
    )
    @idempotent
    def patch(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        target = selectors.get_company_user(actor.company, public_id)
        serializer = UserUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        services.update_user(
            actor=actor,
            target=target,
            changes=services.UserChanges(
                full_name=data.get("full_name"),
                roles=[RoleCode(code) for code in data["roles"]] if "roles" in data else None,
                branch_ids=data.get("branches"),
                is_active=data.get("is_active"),
            ),
            request=request,
        )
        updated = selectors.get_company_user(actor.company, public_id)
        return Response(UserSerializer(updated).data)
