from typing import Self

from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel
from apps.tenancy.validators import (
    BRANCH_CODE_PATTERN,
    validate_branch_code,
    validate_currency,
    validate_timezone,
)


class CompanyStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"


class Company(PublicIdModel, TimeStampedModel):
    """A tenant. Every tenant-owned row references exactly one company."""

    name = models.CharField(max_length=200)
    status = models.CharField(
        max_length=20, choices=CompanyStatus.choices, default=CompanyStatus.ACTIVE
    )
    # Money amounts are always in this currency; business days follow this timezone.
    currency = models.CharField(max_length=3, validators=[validate_currency])
    timezone = models.CharField(max_length=64, validators=[validate_timezone])

    class Meta:
        verbose_name_plural = "companies"
        constraints = [
            models.CheckConstraint(condition=~models.Q(name=""), name="company_name_not_empty"),
            models.CheckConstraint(
                condition=models.Q(status__in=CompanyStatus.values), name="company_status_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(currency__regex=r"^[A-Z]{3}$"), name="company_currency_format"
            ),
            models.CheckConstraint(
                condition=~models.Q(timezone=""), name="company_timezone_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def is_active(self) -> bool:
        return self.status == CompanyStatus.ACTIVE


class TenantQuerySet[M: models.Model](models.QuerySet[M]):
    def for_company(self, company: Company) -> Self:
        return self.filter(company=company)


class TenantScopedModel(models.Model):
    """Base for tenant-owned data (architecture §9).

    Selectors must start from `Model.objects.for_company(request.user.company)`. The
    company always comes from the authenticated user, never from request input.
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT)

    objects = TenantQuerySet.as_manager()

    class Meta:
        abstract = True


class Branch(TenantScopedModel, PublicIdModel, TimeStampedModel):
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=10, validators=[validate_branch_code])

    class Meta:
        verbose_name_plural = "branches"
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="branch_unique_name"),
            models.CheckConstraint(condition=~models.Q(name=""), name="branch_name_not_empty"),
            models.UniqueConstraint(fields=["company", "code"], name="branch_unique_code"),
            models.CheckConstraint(
                condition=models.Q(code__regex=BRANCH_CODE_PATTERN), name="branch_code_format"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class ServiceType(TenantScopedModel, PublicIdModel, TimeStampedModel):
    """A delivery service a company offers. Limits apply to each parcel (ShipmentItem)."""

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=100)
    max_weight_kg = models.DecimalField(max_digits=9, decimal_places=3)
    max_length_cm = models.DecimalField(max_digits=7, decimal_places=1)
    max_width_cm = models.DecimalField(max_digits=7, decimal_places=1)
    max_height_cm = models.DecimalField(max_digits=7, decimal_places=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="service_type_unique_code"),
            models.CheckConstraint(
                condition=~models.Q(code=""), name="service_type_code_not_empty"
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""), name="service_type_name_not_empty"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    max_weight_kg__gt=0,
                    max_length_cm__gt=0,
                    max_width_cm__gt=0,
                    max_height_cm__gt=0,
                ),
                name="service_type_limits_positive",
            ),
        ]

    def __str__(self) -> str:
        return self.name
