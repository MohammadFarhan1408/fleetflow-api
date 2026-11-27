from typing import Any

from rest_framework import serializers

from apps.accounts.models import RoleCode, User
from apps.tenancy.models import Branch
from apps.tenancy.validators import BRANCH_CODE_PATTERN

# Generous but bounded: hashing arbitrarily long input is a cheap denial-of-service vector.
PASSWORD_MAX_LENGTH = 256


class LoginSerializer(serializers.Serializer[dict[str, str]]):
    email = serializers.EmailField()
    password = serializers.CharField(max_length=PASSWORD_MAX_LENGTH, trim_whitespace=False)


class RefreshTokenSerializer(serializers.Serializer[dict[str, str]]):
    refresh = serializers.CharField()


class TokenPairSerializer(serializers.Serializer[dict[str, str]]):
    access = serializers.CharField()
    refresh = serializers.CharField()


class PasswordResetRequestSerializer(serializers.Serializer[dict[str, str]]):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer[dict[str, Any]]):
    uid = serializers.UUIDField()
    token = serializers.CharField(max_length=128)
    new_password = serializers.CharField(max_length=PASSWORD_MAX_LENGTH, trim_whitespace=False)


class MeUserSerializer(serializers.Serializer[dict[str, Any]]):
    public_id = serializers.UUIDField()
    email = serializers.EmailField()
    full_name = serializers.CharField()


class MeCompanySerializer(serializers.Serializer[dict[str, Any]]):
    public_id = serializers.UUIDField()
    name = serializers.CharField()


class MeBranchSerializer(serializers.Serializer[dict[str, Any]]):
    public_id = serializers.UUIDField()
    name = serializers.CharField()


class MeSerializer(serializers.Serializer[dict[str, Any]]):
    """Session bootstrap for clients (admin `/me`, architecture §25)."""

    user = MeUserSerializer()
    company = MeCompanySerializer()
    branches = MeBranchSerializer(many=True)
    roles = serializers.ListField(child=serializers.CharField())
    permissions = serializers.ListField(child=serializers.CharField())


class BranchSerializer(serializers.ModelSerializer[Branch]):
    class Meta:
        model = Branch
        fields = ["public_id", "code", "name"]
        read_only_fields = fields


class BranchCreateSerializer(serializers.Serializer[dict[str, str]]):
    name = serializers.CharField(max_length=200)
    # Upper-cased before validation, so "ber1" is accepted as "BER1".
    code = serializers.RegexField(BRANCH_CODE_PATTERN, max_length=10)

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("code"), str):
            data = {**data, "code": data["code"].strip().upper()}
        return super().to_internal_value(data)


class UserBranchSerializer(serializers.Serializer[dict[str, Any]]):
    public_id = serializers.UUIDField()
    name = serializers.CharField()


class UserSerializer(serializers.ModelSerializer[User]):
    """A company member as returned by the users API. Explicit fields only (rules.md §2)."""

    roles = serializers.SerializerMethodField()
    branches = UserBranchSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = [
            "public_id",
            "email",
            "full_name",
            "is_active",
            "roles",
            "branches",
            "last_login",
            "created_at",
        ]
        read_only_fields = fields

    def get_roles(self, user: User) -> list[str]:
        # Reads the prefetched roles; sorted for a stable contract.
        return sorted(role.code for role in user.roles.all())


class UserInviteSerializer(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField()
    full_name = serializers.CharField(max_length=200)
    roles = serializers.ListField(
        child=serializers.ChoiceField(choices=RoleCode.choices), min_length=1, max_length=10
    )
    branches = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list, max_length=100
    )


class UserUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    full_name = serializers.CharField(max_length=200, required=False)
    roles = serializers.ListField(
        child=serializers.ChoiceField(choices=RoleCode.choices),
        min_length=1,
        max_length=10,
        required=False,
    )
    branches = serializers.ListField(child=serializers.UUIDField(), required=False, max_length=100)
    is_active = serializers.BooleanField(required=False)
