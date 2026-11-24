from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import AppendOnlyModel, AppendOnlyQuerySet
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
    CUSTOMER_CREATED = "customer.created", "Customer created"
    CUSTOMER_ARCHIVED = "customer.archived", "Customer archived"
    ADDRESS_CREATED = "address.created", "Address created"
    ADDRESS_ARCHIVED = "address.archived", "Address archived"
    SHIPMENT_CREATED = "shipment.created", "Shipment created"
    SHIPMENT_DRAFT_DELETED = "shipment.draft_deleted", "Draft shipment deleted"
    SHIPMENT_TRACKING_TOKEN_ROTATED = (
        "shipment.tracking_token_rotated",
        "Shipment tracking token rotated",
    )
    SHIPMENT_RECOVERED = "shipment.recovered", "Cancelled shipment recovered"
    SHIPMENT_STATUS_CORRECTED = "shipment.status_corrected", "Shipment status corrected"
    SHIPMENT_STATUS_MISMATCH = "shipment.status_mismatch", "Shipment status mismatch found"


class AuditLog(AppendOnlyModel):
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

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        indexes = [models.Index(fields=["company", "-at"], name="auditlog_company_at")]

    def __str__(self) -> str:
        return f"{self.action} {self.object_type}:{self.object_id} at {self.at.isoformat()}"
