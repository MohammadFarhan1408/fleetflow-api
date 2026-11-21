from typing import Any

from rest_framework import serializers

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
