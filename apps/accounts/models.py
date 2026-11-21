import uuid
from typing import Any, ClassVar

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower

from apps.core.models import PublicIdModel, TimeStampedModel
from apps.tenancy.models import Branch, Company, TenantQuerySet


class RoleCode(models.TextChoices):
    """Built-in roles (architecture §8). Part of the API contract via `/me`."""

    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    OPS_MANAGER = "ops_manager", "Operations manager"
    DISPATCHER = "dispatcher", "Dispatcher"
    FLEET_MANAGER = "fleet_manager", "Fleet manager"
    WAREHOUSE_STAFF = "warehouse_staff", "Warehouse staff"
    FINANCE = "finance", "Finance"
    CUSTOMER_SERVICE = "customer_service", "Customer service"
    DRIVER = "driver", "Driver"
    VIEWER = "viewer", "Viewer"


class Permission(models.Model):
    """A permission codename such as `users.manage`. Seeded by data migrations."""

    codename = models.CharField(max_length=100, unique=True)

    def __str__(self) -> str:
        return self.codename


class Role(models.Model):
    code = models.CharField(max_length=32, choices=RoleCode.choices, unique=True)
    permissions = models.ManyToManyField(Permission, related_name="roles", blank=True)

    def __str__(self) -> str:
        return self.code


def normalize_email(email: str) -> str:
    # Lowercase the whole address: emails are the login identifier and must match
    # case-insensitively. The DB check constraint below enforces this.
    return email.strip().lower()


class UserQuerySet(TenantQuerySet["User"]):
    pass


class UserManager(BaseUserManager["User"]):
    def get_queryset(self) -> UserQuerySet:
        return UserQuerySet(self.model, using=self._db)

    def for_company(self, company: Company) -> UserQuerySet:
        return self.get_queryset().for_company(company)

    def get_by_natural_key(self, username: str | None) -> User:
        return self.get(email=normalize_email(username or ""))

    def create_user(
        self,
        *,
        email: str,
        full_name: str,
        company: Company,
        password: str | None = None,
        **extra: Any,
    ) -> User:
        user = self.model(
            email=normalize_email(email), full_name=full_name.strip(), company=company, **extra
        )
        # None → unusable password: the user sets one through the emailed link.
        user.set_password(password)
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user


class User(AbstractBaseUser, PublicIdModel, TimeStampedModel):
    """A person who logs in. Belongs to exactly one company (decision D-API-11).

    No Django superuser/staff concept: authorization is FleetFlow RBAC only.
    """

    email = models.EmailField(max_length=254, unique=True)
    full_name = models.CharField(max_length=200)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="users")
    is_active = models.BooleanField(default=True)
    roles = models.ManyToManyField(Role, related_name="users", blank=True)
    branches = models.ManyToManyField(Branch, related_name="users", blank=True)

    objects: ClassVar[UserManager] = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = ["full_name"]

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(email=Lower("email")), name="user_email_lowercase"
            ),
            models.CheckConstraint(condition=~models.Q(email=""), name="user_email_not_empty"),
        ]

    def __str__(self) -> str:
        return self.email


class TokenFamilyRevokedReason(models.TextChoices):
    LOGOUT = "logout", "Logout"
    REUSE = "reuse", "Refresh token reuse"
    PASSWORD_RESET = "password_reset", "Password reset"
    DEACTIVATED = "deactivated", "User deactivated"


class TokenFamily(models.Model):
    """One login session (one device). Every refresh token rotated from that login shares
    the family ID in its `fam` claim, so the whole session can be revoked at once."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="token_families")
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(
        max_length=20, choices=TokenFamilyRevokedReason.choices, blank=True
    )

    class Meta:
        verbose_name_plural = "token families"

    def __str__(self) -> str:
        return str(self.id)


class RefreshTokenRecord(models.Model):
    """Tracks each issued refresh token by `jti` only.

    The token itself is never stored, so a database read cannot be replayed as a session.
    `used_at` drives rotation and reuse detection.
    """

    jti = models.CharField(max_length=64, primary_key=True)
    family = models.ForeignKey(TokenFamily, on_delete=models.CASCADE, related_name="tokens")
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.jti
