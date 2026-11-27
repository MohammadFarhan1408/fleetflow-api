import uuid

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404

from apps.accounts.models import User
from apps.accounts.permissions import get_user_permissions
from apps.tenancy.models import Branch, Company


def users_for_company(company: Company) -> QuerySet[User]:
    """Tenant-scoped users with everything the user serializer reads, in fixed queries."""
    return User.objects.for_company(company).prefetch_related("roles", "branches")


def branches_in_scope(user: User, all_branches_permission: str) -> QuerySet[Branch]:
    """Branches whose data `user` may see and write (architecture §8): every branch of the
    company when they hold `all_branches_permission` (e.g. `shipments.all_branches`),
    otherwise only their own branches. Shared by every branch-scoped module."""
    branches = Branch.objects.for_company(user.company)
    if all_branches_permission in get_user_permissions(user):
        return branches
    return branches.filter(users=user)


def branches_for_picker(user: User) -> QuerySet[Branch]:
    """Branches `user` can choose when creating branch-scoped records: their own, or the
    whole company when any module's `*.all_branches` permission is held. Each create
    endpoint still validates the branch against its own scope."""
    if any(code.endswith(".all_branches") for code in get_user_permissions(user)):
        return Branch.objects.for_company(user.company)
    return Branch.objects.for_company(user.company).filter(users=user)


def get_company_user(company: Company, public_id: uuid.UUID) -> User:
    """A user of `company`, or 404: other tenants' users are indistinguishable from
    missing ones (architecture §9)."""
    return get_object_or_404(users_for_company(company), public_id=public_id)
