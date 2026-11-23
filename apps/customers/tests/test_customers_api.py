from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.accounts.tests.conftest import client_for
from apps.audit.models import AuditAction, AuditLog
from apps.customers.factories import AddressFactory, CustomerFactory
from apps.customers.models import Customer, CustomerKind
from apps.tenancy.factories import CompanyFactory
from apps.tenancy.models import Company

pytestmark = pytest.mark.django_db

CUSTOMERS_URL = "/api/v1/customers/"
VALID = {
    "kind": "business",
    "name": "Acme GmbH",
    "email": "ops@acme.example",
    "phone": "+4930123456",
}


def detail_url(customer: Customer) -> str:
    return f"{CUSTOMERS_URL}{customer.public_id}/"


def archive_url(customer: Customer) -> str:
    return f"{detail_url(customer)}archive/"


@pytest.fixture
def company() -> Company:
    return CompanyFactory.create()


@pytest.fixture
def manager(company: Company) -> User:
    return UserFactory.create(company=company, roles=[RoleCode.CUSTOMER_SERVICE])


@pytest.fixture
def client(manager: User) -> APIClient:
    return client_for(manager)


# --- Role x action matrix (memory D-API-21) -----------------------------------------

CAN_VIEW = {
    RoleCode.OWNER,
    RoleCode.ADMIN,
    RoleCode.OPS_MANAGER,
    RoleCode.DISPATCHER,
    RoleCode.CUSTOMER_SERVICE,
    RoleCode.FINANCE,
    RoleCode.VIEWER,
}
CAN_MANAGE = {RoleCode.OWNER, RoleCode.ADMIN, RoleCode.OPS_MANAGER, RoleCode.CUSTOMER_SERVICE}


@pytest.mark.parametrize("role", list(RoleCode))
def test_role_matrix(role: RoleCode, company: Company) -> None:
    client = client_for(UserFactory.create(company=company, roles=[role]))
    customer = CustomerFactory.create(company=company)

    reads = [client.get(CUSTOMERS_URL), client.get(detail_url(customer))]
    writes = [
        client.post(CUSTOMERS_URL, VALID, format="json"),
        client.patch(detail_url(customer), {"name": "Renamed"}, format="json"),
        client.post(archive_url(customer)),
    ]

    assert {r.status_code for r in reads} == ({200} if role in CAN_VIEW else {403})
    expected_writes = [201, 200, 200] if role in CAN_MANAGE else [403, 403, 403]
    assert [r.status_code for r in writes] == expected_writes
    for denied in (r for r in reads + writes if r.status_code == 403):
        assert denied.json()["code"] == "permission.denied"


def test_anonymous_requests_are_401(company: Company) -> None:
    customer = CustomerFactory.create(company=company)
    client = APIClient()

    responses = [
        client.get(CUSTOMERS_URL),
        client.post(CUSTOMERS_URL, VALID, format="json"),
        client.get(detail_url(customer)),
        client.patch(detail_url(customer), {"name": "x"}, format="json"),
        client.post(archive_url(customer)),
    ]

    assert {r.status_code for r in responses} == {401}


# --- Create -----------------------------------------------------------------------------


def test_create_customer(client: APIClient, manager: User) -> None:
    response = client.post(CUSTOMERS_URL, VALID, format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    assert {k: body[k] for k in VALID} == VALID
    assert body["archived_at"] is None
    customer = Customer.objects.get(public_id=body["public_id"])
    assert customer.company == manager.company
    entry = AuditLog.objects.get(action=AuditAction.CUSTOMER_CREATED)
    assert entry.actor_id == manager.pk
    assert entry.object_id == body["public_id"]
    assert entry.after == {"kind": "business"}  # no name, email or phone in the log


def test_create_individual_with_only_required_fields(client: APIClient) -> None:
    response = client.post(CUSTOMERS_URL, {"kind": "individual", "name": "Ada"}, format="json")

    assert response.status_code == 201
    assert response.json()["email"] == response.json()["phone"] == ""


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"name": ""}, "name"),
        ({"name": "   "}, "name"),
        ({"kind": "government"}, "kind"),
        ({"email": "not-an-email"}, "email"),
        ({"phone": "030 123456"}, "phone"),
        ({"phone": "+0123456"}, "phone"),
        ({"phone": "+1234567890123456"}, "phone"),
    ],
)
def test_create_validation_errors(client: APIClient, override: dict[str, str], field: str) -> None:
    response = client.post(CUSTOMERS_URL, {**VALID, **override}, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "validation.invalid"
    assert field in response.json()["errors"]
    assert not Customer.objects.exists()


def test_company_in_body_is_ignored(client: APIClient, manager: User) -> None:
    other = CompanyFactory.create()

    response = client.post(CUSTOMERS_URL, {**VALID, "company": str(other.public_id)}, format="json")

    assert response.status_code == 201
    assert Customer.objects.get().company == manager.company


def test_create_is_idempotent_with_key(client: APIClient) -> None:
    headers = {"Idempotency-Key": "create-acme-1"}

    first = client.post(CUSTOMERS_URL, VALID, format="json", headers=headers)
    replay = client.post(CUSTOMERS_URL, VALID, format="json", headers=headers)

    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert Customer.objects.count() == 1


# --- List -------------------------------------------------------------------------------


def test_list_hides_archived_unless_asked(client: APIClient, company: Company) -> None:
    active = CustomerFactory.create(company=company)
    archived = CustomerFactory.create(company=company)
    client.post(archive_url(archived))

    default = {r["public_id"] for r in client.get(CUSTOMERS_URL).json()["results"]}
    everything = client.get(CUSTOMERS_URL, {"include_archived": "true"}).json()["results"]

    assert default == {str(active.public_id)}
    assert {r["public_id"] for r in everything} == {str(active.public_id), str(archived.public_id)}


def test_list_search_matches_name_case_insensitively(client: APIClient, company: Company) -> None:
    match = CustomerFactory.create(company=company, name="Northwind Traders")
    CustomerFactory.create(company=company, name="Contoso")

    results = client.get(CUSTOMERS_URL, {"search": "northWIND"}).json()["results"]

    assert [r["public_id"] for r in results] == [str(match.public_id)]


def test_list_rejects_bad_query(client: APIClient) -> None:
    response = client.get(CUSTOMERS_URL, {"include_archived": "maybe"})

    assert response.status_code == 400


def test_list_is_paginated_newest_first(client: APIClient, company: Company) -> None:
    created = [CustomerFactory.create(company=company) for _ in range(3)]

    body = client.get(CUSTOMERS_URL, {"page_size": 2}).json()

    assert [r["public_id"] for r in body["results"]] == [
        str(c.public_id) for c in reversed(created[1:])
    ]
    assert body["next"]


def test_list_never_shows_other_company_customers(client: APIClient) -> None:
    CustomerFactory.create(name="Someone Else's Customer")

    body = client.get(CUSTOMERS_URL).content.decode()

    assert "Someone Else's Customer" not in body


@pytest.mark.parametrize("rows", [1, 10])
def test_list_query_count_is_constant(
    client: APIClient, company: Company, rows: int, django_assert_num_queries: Any
) -> None:
    CustomerFactory.create_batch(rows, company=company)

    # auth (family + user + company), permissions, page
    with django_assert_num_queries(3):
        assert client.get(CUSTOMERS_URL).status_code == 200


# --- Detail and update --------------------------------------------------------------


def test_detail(client: APIClient, company: Company) -> None:
    customer = CustomerFactory.create(company=company)

    response = client.get(detail_url(customer))

    assert response.status_code == 200
    assert response.json()["name"] == customer.name


def test_partial_update_keeps_other_fields(client: APIClient, company: Company) -> None:
    customer = CustomerFactory.create(company=company, kind=CustomerKind.BUSINESS)

    response = client.patch(detail_url(customer), {"phone": "+447700900123"}, format="json")

    assert response.status_code == 200
    customer.refresh_from_db()
    assert customer.phone == "+447700900123"
    assert customer.kind == CustomerKind.BUSINESS
    assert response.json()["name"] == customer.name


def test_update_validation_error(client: APIClient, company: Company) -> None:
    customer = CustomerFactory.create(company=company)

    response = client.patch(detail_url(customer), {"name": ""}, format="json")

    assert response.status_code == 400
    assert "name" in response.json()["errors"]


def test_update_archived_customer_is_409(client: APIClient, company: Company) -> None:
    customer = CustomerFactory.create(company=company)
    client.post(archive_url(customer))

    response = client.patch(detail_url(customer), {"name": "New"}, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "customer.archived"


@pytest.mark.parametrize("method", ["get", "patch"])
def test_other_company_customer_is_404(client: APIClient, method: str) -> None:
    outsider = CustomerFactory.create(name="Hidden Co")

    response = getattr(client, method)(detail_url(outsider), {"name": "x"}, format="json")

    assert response.status_code == 404
    assert "Hidden Co" not in response.content.decode()


# --- Archive ----------------------------------------------------------------------------


def test_archive_archives_addresses_and_is_idempotent(
    client: APIClient, company: Company, manager: User
) -> None:
    customer = CustomerFactory.create(company=company)
    AddressFactory.create_batch(2, customer=customer)

    first = client.post(archive_url(customer))
    second = client.post(archive_url(customer))

    assert first.status_code == second.status_code == 200
    assert first.json()["archived_at"] == second.json()["archived_at"] is not None
    assert not customer.addresses.filter(archived_at__isnull=True).exists()
    assert AuditLog.objects.filter(action=AuditAction.CUSTOMER_ARCHIVED).count() == 1


def test_archive_other_company_customer_is_404(client: APIClient) -> None:
    outsider = CustomerFactory.create()

    assert client.post(archive_url(outsider)).status_code == 404
    outsider.refresh_from_db()
    assert outsider.archived_at is None
