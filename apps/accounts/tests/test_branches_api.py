import pytest
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.tests.conftest import client_for
from apps.audit.models import AuditAction, AuditLog
from apps.tenancy.factories import BranchFactory, CompanyFactory

URL = "/api/v1/branches/"


def codes(response) -> set[str]:  # type: ignore[no-untyped-def]
    return {row["code"] for row in response.json()["results"]}


@pytest.mark.django_db
def test_member_sees_only_own_branches() -> None:
    company = CompanyFactory.create()
    mine = BranchFactory.create(company=company, code="MINE")
    BranchFactory.create(company=company, code="OTHER")
    user = UserFactory.create(company=company, roles=["dispatcher"])
    user.branches.add(mine)
    response = client_for(user).get(URL)
    assert response.status_code == 200
    assert codes(response) == {"MINE"}
    assert set(response.json()["results"][0]) == {"public_id", "code", "name"}


@pytest.mark.django_db
def test_all_branches_holder_sees_company_branches_not_other_tenants() -> None:
    company = CompanyFactory.create()
    BranchFactory.create(company=company, code="ONE")
    BranchFactory.create(company=company, code="TWO")
    BranchFactory.create(company=CompanyFactory.create(), code="FOREIGN")
    admin = UserFactory.create(company=company, roles=["admin"])
    response = client_for(admin).get(URL)
    assert codes(response) == {"ONE", "TWO"}


@pytest.mark.django_db
def test_user_without_branches_gets_empty_list() -> None:
    user = UserFactory.create(roles=["viewer"])
    assert client_for(user).get(URL).json()["results"] == []


@pytest.mark.django_db
def test_anonymous_is_401(api_client: APIClient) -> None:
    assert api_client.get(URL).status_code == 401


@pytest.mark.django_db
def test_owner_creates_branch_normalizes_code_and_audits() -> None:
    owner = UserFactory.create(roles=["owner"])
    client = client_for(owner)
    response = client.post(URL, {"name": "Berlin Hub", "code": " ber1 "}, format="json")
    assert response.status_code == 201, response.content
    assert response.json()["code"] == "BER1"
    assert codes(client.get(URL)) == {"BER1"}  # all_branches holder sees it, not a member
    assert AuditLog.objects.filter(action=AuditAction.BRANCH_CREATED, actor=owner).count() == 1


@pytest.mark.django_db
def test_duplicate_name_or_code_is_409_and_other_company_may_reuse() -> None:
    company = CompanyFactory.create()
    BranchFactory.create(company=company, name="Hub", code="HUB1")
    admin = UserFactory.create(company=company, roles=["admin"])
    client = client_for(admin)
    for body in ({"name": "Hub", "code": "NEW1"}, {"name": "New", "code": "hub1"}):
        response = client.post(URL, body, format="json")
        assert response.status_code == 409
        assert response.json()["code"] == "branch.duplicate"
    other = client_for(UserFactory.create(roles=["owner"]))
    assert other.post(URL, {"name": "Hub", "code": "HUB1"}, format="json").status_code == 201


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        {"name": "X", "code": "A"},
        {"name": "X", "code": "a b"},
        {"name": "", "code": "OK1"},
        {"code": "OK1"},
    ],
)
def test_invalid_branch_is_400(body: dict[str, str]) -> None:
    client = client_for(UserFactory.create(roles=["owner"]))
    assert client.post(URL, body, format="json").status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["ops_manager", "dispatcher", "fleet_manager", "viewer", "driver"])
def test_only_owner_and_admin_can_create(role: str) -> None:
    client = client_for(UserFactory.create(roles=[role]))
    response = client.post(URL, {"name": "Nope", "code": "NOPE"}, format="json")
    assert response.status_code == 403
    assert client.get(URL).status_code == 200
