from typing import Any

import pytest
from django.core import mail
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, TokenFamily, User
from apps.accounts.tests.conftest import ME_URL, login
from apps.audit.models import AuditAction, AuditLog
from apps.tenancy.factories import BranchFactory, CompanyFactory
from apps.tenancy.models import Company

pytestmark = pytest.mark.django_db

USERS_URL = "/api/v1/users/"


def detail_url(user: User) -> str:
    return f"{USERS_URL}{user.public_id}/"


def client_for(user: User) -> APIClient:
    """An API client authenticated as `user` through the real login flow."""
    client = APIClient()
    access = login(client, user.email).json()["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


@pytest.fixture
def company() -> Company:
    return CompanyFactory.create()


@pytest.fixture
def owner(company: Company) -> User:
    return UserFactory.create(company=company, roles=[RoleCode.OWNER])


@pytest.fixture
def admin(company: Company) -> User:
    return UserFactory.create(company=company, roles=[RoleCode.ADMIN])


def member(company: Company, *roles: RoleCode, **kwargs: Any) -> User:
    return UserFactory.create(company=company, roles=list(roles), **kwargs)


# --- Role x action matrix (architecture §26.4) --------------------------------------

ROLE_CAN = {
    RoleCode.OWNER: {"view", "manage"},
    RoleCode.ADMIN: {"view", "manage"},
    RoleCode.OPS_MANAGER: {"view"},
    RoleCode.DISPATCHER: set(),
    RoleCode.FLEET_MANAGER: set(),
    RoleCode.WAREHOUSE_STAFF: set(),
    RoleCode.FINANCE: set(),
    RoleCode.CUSTOMER_SERVICE: set(),
    RoleCode.DRIVER: set(),
    RoleCode.VIEWER: set(),
}


@pytest.mark.parametrize("role", list(RoleCode))
def test_role_matrix(role: RoleCode, company: Company, owner: User) -> None:
    client = client_for(member(company, role))
    other = member(company, RoleCode.VIEWER)
    allowed = ROLE_CAN[role]

    list_status = client.get(USERS_URL).status_code
    detail_status = client.get(detail_url(other)).status_code
    patch_status = client.patch(
        detail_url(other), {"full_name": "Renamed"}, format="json"
    ).status_code
    invite_status = client.post(
        USERS_URL,
        {"email": f"new-{role}@example.com", "full_name": "New", "roles": ["viewer"]},
        format="json",
    ).status_code

    assert (list_status, detail_status) == ((200, 200) if "view" in allowed else (403, 403))
    assert (patch_status, invite_status) == ((200, 201) if "manage" in allowed else (403, 403))


def test_forbidden_response_uses_permission_code(company: Company) -> None:
    response = client_for(member(company, RoleCode.DISPATCHER)).get(USERS_URL)

    assert response.json()["code"] == "permission.denied"


@pytest.mark.parametrize("method", ["put", "delete"])
def test_unimplemented_methods_are_405_not_403(owner: User, method: str) -> None:
    response = getattr(client_for(owner), method)(detail_url(owner))

    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"


def test_unmapped_implemented_method_is_denied(owner: User) -> None:
    # OPTIONS is implemented by every APIView but mapped to no permission.
    assert client_for(owner).options(USERS_URL).status_code == 403


def test_anonymous_gets_401(api_client: APIClient) -> None:
    assert api_client.get(USERS_URL).status_code == 401


# --- List and detail ----------------------------------------------------------------


def test_list_shows_only_own_company_with_roles_and_branches(company: Company, owner: User) -> None:
    branch = BranchFactory.create(company=company, name="North")
    dispatcher = member(company, RoleCode.DISPATCHER)
    dispatcher.branches.add(branch)
    outsider = UserFactory.create()

    body = client_for(owner).get(USERS_URL).json()

    ids = {row["public_id"] for row in body["results"]}
    assert ids == {str(owner.public_id), str(dispatcher.public_id)}
    assert str(outsider.public_id) not in str(body)
    row = next(r for r in body["results"] if r["public_id"] == str(dispatcher.public_id))
    assert row["roles"] == ["dispatcher"]
    assert row["branches"] == [{"public_id": str(branch.public_id), "name": "North"}]
    assert "id" not in row and "password" not in row


def test_list_is_cursor_paginated(company: Company, owner: User) -> None:
    for _ in range(3):
        member(company, RoleCode.VIEWER)

    first = client_for(owner).get(USERS_URL, {"page_size": 2}).json()

    assert len(first["results"]) == 2
    assert first["next"] is not None and "cursor=" in first["next"]


def test_list_query_count_does_not_grow_with_users(
    company: Company, owner: User, django_assert_max_num_queries: Any
) -> None:
    client = client_for(owner)
    for _ in range(10):
        user = member(company, RoleCode.DISPATCHER, RoleCode.VIEWER)
        user.branches.add(BranchFactory.create(company=company))

    # auth (family+user+company), permissions, page, roles prefetch, branches prefetch
    with django_assert_max_num_queries(5):
        assert client.get(USERS_URL).status_code == 200


def test_detail_of_other_company_user_is_404(owner: User) -> None:
    outsider = UserFactory.create()

    response = client_for(owner).get(detail_url(outsider))

    assert response.status_code == 404
    assert outsider.email not in response.content.decode()


# --- Invite ---------------------------------------------------------------------------


def test_invite_creates_user_sends_email_and_audits(
    company: Company, admin: User, django_capture_on_commit_callbacks: Any
) -> None:
    branch = BranchFactory.create(company=company)

    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(admin).post(
            USERS_URL,
            {
                "email": "New.Hire@Example.com",
                "full_name": "New Hire",
                "roles": ["dispatcher"],
                "branches": [str(branch.public_id)],
            },
            format="json",
        )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new.hire@example.com"
    assert body["roles"] == ["dispatcher"]
    created = User.objects.get(email="new.hire@example.com")
    assert created.company == company
    assert created.has_usable_password() is False
    assert [m.to for m in mail.outbox] == [["new.hire@example.com"]]
    entry = AuditLog.objects.get(action=AuditAction.USER_CREATED, actor=admin)
    assert entry.after is not None and entry.after["roles"] == ["dispatcher"]


def test_invite_with_existing_email_is_409(owner: User) -> None:
    taken = UserFactory.create()  # any company: emails are global

    response = client_for(owner).post(
        USERS_URL, {"email": taken.email, "full_name": "X", "roles": ["viewer"]}, format="json"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "user.email_taken"


def test_invite_with_other_company_branch_is_invalid_reference(owner: User) -> None:
    foreign_branch = BranchFactory.create()

    response = client_for(owner).post(
        USERS_URL,
        {
            "email": "x@example.com",
            "full_name": "X",
            "roles": ["viewer"],
            "branches": [str(foreign_branch.public_id)],
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["code"] == "validation.invalid_reference"
    assert foreign_branch.name not in response.content.decode()
    assert not User.objects.filter(email="x@example.com").exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "bad", "full_name": "X", "roles": ["viewer"]},
        {"email": "x@example.com", "full_name": "X", "roles": []},
        {"email": "x@example.com", "full_name": "X", "roles": ["superuser"]},
        {"email": "x@example.com", "roles": ["viewer"]},
    ],
)
def test_invite_validation(owner: User, payload: dict[str, Any]) -> None:
    response = client_for(owner).post(USERS_URL, payload, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "validation.invalid"


def test_admin_cannot_invite_an_owner(admin: User) -> None:
    response = client_for(admin).post(
        USERS_URL, {"email": "o2@example.com", "full_name": "O", "roles": ["owner"]}, format="json"
    )

    assert response.status_code == 403
    assert response.json()["code"] == "user.owner_required"


def test_owner_can_invite_an_owner(owner: User) -> None:
    response = client_for(owner).post(
        USERS_URL, {"email": "o2@example.com", "full_name": "O", "roles": ["owner"]}, format="json"
    )

    assert response.status_code == 201


# --- Update and guards ---------------------------------------------------------------


def test_change_roles_and_branches_audits_before_and_after(company: Company, admin: User) -> None:
    target = member(company, RoleCode.VIEWER)
    branch = BranchFactory.create(company=company)

    response = client_for(admin).patch(
        detail_url(target),
        {"roles": ["dispatcher", "viewer"], "branches": [str(branch.public_id)]},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["roles"] == ["dispatcher", "viewer"]
    roles_entry = AuditLog.objects.get(action=AuditAction.USER_ROLES_CHANGED)
    assert roles_entry.before == {"roles": ["viewer"]}
    assert roles_entry.after == {"roles": ["dispatcher", "viewer"]}
    assert AuditLog.objects.filter(action=AuditAction.USER_BRANCHES_CHANGED).exists()


def test_role_change_takes_effect_on_next_request(company: Company, owner: User) -> None:
    target = member(company, RoleCode.DISPATCHER)
    target_client = client_for(target)
    assert target_client.get(USERS_URL).status_code == 403

    client_for(owner).patch(detail_url(target), {"roles": ["ops_manager"]}, format="json")

    assert target_client.get(USERS_URL).status_code == 200


def test_unchanged_values_write_no_audit(company: Company, admin: User) -> None:
    target = member(company, RoleCode.VIEWER)

    client_for(admin).patch(detail_url(target), {"roles": ["viewer"]}, format="json")

    assert not AuditLog.objects.filter(action=AuditAction.USER_ROLES_CHANGED).exists()


def test_deactivation_ends_sessions_immediately(company: Company, admin: User) -> None:
    target = member(company, RoleCode.DISPATCHER)
    target_client = client_for(target)

    response = client_for(admin).patch(detail_url(target), {"is_active": False}, format="json")

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert target_client.get(ME_URL).status_code == 401
    assert not TokenFamily.objects.filter(user=target, revoked_at__isnull=True).exists()
    assert AuditLog.objects.filter(action=AuditAction.USER_DEACTIVATED).exists()


def test_reactivation_is_audited(company: Company, admin: User) -> None:
    target = member(company, RoleCode.DISPATCHER, is_active=False)

    client_for(admin).patch(detail_url(target), {"is_active": True}, format="json")

    assert AuditLog.objects.filter(action=AuditAction.USER_REACTIVATED).exists()


@pytest.mark.parametrize("payload", [{"roles": ["viewer"]}, {"is_active": False}, {"branches": []}])
def test_cannot_change_own_access(owner: User, payload: dict[str, Any]) -> None:
    response = client_for(owner).patch(detail_url(owner), payload, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "user.cannot_modify_self"


def test_can_change_own_name(owner: User) -> None:
    response = client_for(owner).patch(detail_url(owner), {"full_name": "New Name"}, format="json")

    assert response.status_code == 200
    assert response.json()["full_name"] == "New Name"


@pytest.mark.parametrize("payload", [{"roles": ["viewer"]}, {"is_active": False}, {"branches": []}])
def test_admin_cannot_change_an_owners_access(
    owner: User, admin: User, payload: dict[str, Any]
) -> None:
    response = client_for(admin).patch(detail_url(owner), payload, format="json")

    assert response.status_code == 403
    assert response.json()["code"] == "user.owner_required"


def test_admin_cannot_grant_owner(company: Company, admin: User) -> None:
    target = member(company, RoleCode.VIEWER)

    response = client_for(admin).patch(detail_url(target), {"roles": ["owner"]}, format="json")

    assert response.json()["code"] == "user.owner_required"


def test_admin_can_rename_an_owner(owner: User, admin: User) -> None:
    response = client_for(admin).patch(detail_url(owner), {"full_name": "Olivia"}, format="json")

    assert response.status_code == 200


def test_last_owner_guard_in_service(company: Company, owner: User) -> None:
    # Unreachable through the API (the actor is always another active owner), so this
    # backstop is exercised directly: an inactive owner acting on the only active one.
    from django.test import RequestFactory

    from apps.accounts import services
    from apps.core.errors import DomainError

    inactive_owner = member(company, RoleCode.OWNER, is_active=False)

    with pytest.raises(DomainError) as exc_info:
        services.update_user(
            actor=inactive_owner,
            target=owner,
            changes=services.UserChanges(is_active=False),
            request=RequestFactory().patch("/"),
        )

    assert exc_info.value.error_code == "user.last_owner"
    owner.refresh_from_db()
    assert owner.is_active


def test_patch_other_company_user_is_404(owner: User) -> None:
    outsider = UserFactory.create()

    response = client_for(owner).patch(detail_url(outsider), {"full_name": "x"}, format="json")

    assert response.status_code == 404
    outsider.refresh_from_db()
    assert outsider.full_name != "x"


def test_service_rejects_target_from_another_company(owner: User) -> None:
    from django.test import RequestFactory

    from apps.accounts import services
    from apps.core.errors import DomainError

    outsider = UserFactory.create(roles=[RoleCode.VIEWER])

    with pytest.raises(DomainError) as exc_info:
        services.update_user(
            actor=owner,
            target=outsider,
            changes=services.UserChanges(is_active=False),
            request=RequestFactory().patch("/"),
        )

    assert exc_info.value.error_code == "not_found"
    outsider.refresh_from_db()
    assert outsider.is_active


def test_patch_with_foreign_branch_is_invalid_reference(company: Company, admin: User) -> None:
    target = member(company, RoleCode.VIEWER)

    response = client_for(admin).patch(
        detail_url(target), {"branches": [str(BranchFactory.create().public_id)]}, format="json"
    )

    assert response.json()["code"] == "validation.invalid_reference"
    assert target.branches.count() == 0
