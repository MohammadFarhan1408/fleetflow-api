from typing import Any, NoReturn

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.tenancy.models import Company


class AuditAction(models.TextChoices):
    AUTH_LOGIN = "auth.login", "Login"
    AUTH_LOGOUT = "auth.logout", "Logout"
    AUTH_REFRESH_REUSE = "auth.refresh_reuse", "Refresh token reuse"
    AUTH_ACCOUNT_LOCKED = "auth.account_locked", "Account locked"
    AUTH_PASSWORD_RESET = "auth.password_reset", "Password reset"
    COMPANY_CREATED = "company.created", "Company created"
    USER_CREATED = "user.created", "User created"
    USER_ROLES_CHANGED = "user.roles_changed", "User roles changed"
    USER_BRANCHES_CHANGED = "user.branches_changed", "User branches changed"
    USER_DEACTIVATED = "user.deactivated", "User deactivated"
    USER_REACTIVATED = "user.reactivated", "User reactivated"


class AppendOnlyError(Exception):
    pass


class AuditLogQuerySet(models.QuerySet["AuditLog"]):
    def update(self, **kwargs: Any) -> NoReturn:
        raise AppendOnlyError("Audit log rows cannot be updated.")

    def delete(self) -> NoReturn:
        raise AppendOnlyError("Audit log rows cannot be deleted.")


class AuditLog(models.Model):
    """Append-only record of security-relevant operations (architecture §23).

    Written only through `apps.audit.services.record`.
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    # Null for actions with no human actor (system or management command).
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+"
    )
    action = models.CharField(max_length=64, choices=AuditAction.choices)
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    before = models.JSONField(null=True)
    after = models.JSONField(null=True)
    ip = models.GenericIPAddressField(null=True)
    user_agent = models.CharField(max_length=512, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    at = models.DateTimeField(default=timezone.now)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        indexes = [models.Index(fields=["company", "-at"], name="auditlog_company_at")]

    def __str__(self) -> str:
        return f"{self.action} {self.object_type}:{self.object_id} at {self.at.isoformat()}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AppendOnlyError("Audit log rows cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise AppendOnlyError("Audit log rows cannot be deleted.")
