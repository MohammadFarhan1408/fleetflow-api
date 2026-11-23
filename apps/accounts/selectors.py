import uuid

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404

from apps.accounts.models import User
from apps.tenancy.models import Company


def users_for_company(company: Company) -> QuerySet[User]:
    """Tenant-scoped users with everything the user serializer reads, in fixed queries."""
    return User.objects.for_company(company).prefetch_related("roles", "branches")


def get_company_user(company: Company, public_id: uuid.UUID) -> User:
    """A user of `company`, or 404: other tenants' users are indistinguishable from
    missing ones (architecture §9)."""
    return get_object_or_404(users_for_company(company), public_id=public_id)
