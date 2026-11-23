import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.accounts.factories import UserFactory
from apps.accounts.models import Role, RoleCode, User
from apps.tenancy.factories import CompanyFactory

pytestmark = pytest.mark.django_db


def test_create_user_normalizes_email() -> None:
    user = User.objects.create_user(
        email="  Ops.Lead@Example.COM ", full_name="Ops Lead", company=CompanyFactory.create()
    )

    assert user.email == "ops.lead@example.com"


def test_create_user_without_password_has_unusable_password() -> None:
    user = User.objects.create_user(
        email="a@example.com", full_name="A", company=CompanyFactory.create()
    )

    assert user.has_usable_password() is False


def test_create_user_rejects_duplicate_email_in_any_case() -> None:
    UserFactory.create(email="driver@example.com")

    with pytest.raises(ValidationError):
        User.objects.create_user(
            email="DRIVER@example.com", full_name="Dup", company=CompanyFactory.create()
        )


def test_db_enforces_email_uniqueness() -> None:
    existing = UserFactory.create(email="driver@example.com")

    with pytest.raises(IntegrityError):
        User.objects.create(email="driver@example.com", full_name="Dup", company=existing.company)


def test_create_user_rejects_invalid_email() -> None:
    with pytest.raises(ValidationError):
        User.objects.create_user(
            email="not-an-email", full_name="X", company=CompanyFactory.create()
        )


def test_db_rejects_mixed_case_email() -> None:
    user = UserFactory.create()

    with pytest.raises(IntegrityError):
        User.objects.filter(pk=user.pk).update(email="Mixed@Example.com")


def test_natural_key_lookup_is_case_insensitive() -> None:
    user = UserFactory.create(email="dispatch@example.com")

    assert User.objects.get_by_natural_key("Dispatch@Example.com") == user


def test_user_for_company_is_tenant_scoped() -> None:
    mine = UserFactory.create()
    UserFactory.create()

    assert list(User.objects.for_company(mine.company)) == [mine]


def test_all_builtin_roles_are_seeded() -> None:
    assert set(Role.objects.values_list("code", flat=True)) == set(RoleCode.values)


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        # Phase 1 users permissions; customers permissions per memory D-API-21.
        (RoleCode.OWNER, {"users.view", "users.manage", "customers.view", "customers.manage"}),
        (RoleCode.ADMIN, {"users.view", "users.manage", "customers.view", "customers.manage"}),
        (RoleCode.OPS_MANAGER, {"users.view", "customers.view", "customers.manage"}),
        (RoleCode.DISPATCHER, {"customers.view"}),
        (RoleCode.CUSTOMER_SERVICE, {"customers.view", "customers.manage"}),
        (RoleCode.FINANCE, {"customers.view"}),
        (RoleCode.VIEWER, {"customers.view"}),
        (RoleCode.WAREHOUSE_STAFF, set()),
        (RoleCode.FLEET_MANAGER, set()),
        (RoleCode.DRIVER, set()),
    ],
)
def test_seeded_role_permissions(role: RoleCode, expected: set[str]) -> None:
    codenames = set(Role.objects.get(code=role).permissions.values_list("codename", flat=True))

    assert codenames == expected
