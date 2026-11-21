from typing import Self

from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel


class CompanyStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"


class Company(PublicIdModel, TimeStampedModel):
    """A tenant. Every tenant-owned row references exactly one company."""

    name = models.CharField(max_length=200)
    status = models.CharField(
        max_length=20, choices=CompanyStatus.choices, default=CompanyStatus.ACTIVE
    )

    class Meta:
        verbose_name_plural = "companies"
        constraints = [
            models.CheckConstraint(condition=~models.Q(name=""), name="company_name_not_empty"),
            models.CheckConstraint(
                condition=models.Q(status__in=CompanyStatus.values), name="company_status_valid"
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

    class Meta:
        verbose_name_plural = "branches"
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="branch_unique_name"),
            models.CheckConstraint(condition=~models.Q(name=""), name="branch_name_not_empty"),
        ]

    def __str__(self) -> str:
        return self.name
