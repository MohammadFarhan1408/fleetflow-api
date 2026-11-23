from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.accounts.tests.conftest import client_for
from apps.audit.models import AuditAction, AuditLog
from apps.customers.factories import AddressFactory, CustomerFactory
from apps.customers.models import Address, Customer, GeocodeConfidence
from apps.tenancy.factories import CompanyFactory
from apps.tenancy.models import Company

pytestmark = pytest.mark.django_db

VALID = {
    "label": "Warehouse",
    "contact_name": "Max Muster",
    "phone": "+4930123456",
    "line1": "Hauptstraße 1",
    "city": "Berlin",
    "postal_code": "10115",
    "country": "de",
}


def list_url(customer: Customer) -> str:
    return f"/api/v1/customers/{customer.public_id}/addresses/"


def detail_url(address: Address) -> str:
    return f"{list_url(address.customer)}{address.public_id}/"


def archive_url(address: Address) -> str:
    return f"{detail_url(address)}archive/"


@pytest.fixture
def company() -> Company:
    return CompanyFactory.create()


@pytest.fixture
def manager(company: Company) -> User:
    return UserFactory.create(company=company, roles=[RoleCode.CUSTOMER_SERVICE])


@pytest.fixture
def client(manager: User) -> APIClient:
    return client_for(manager)


@pytest.fixture
def customer(company: Company) -> Customer:
    return CustomerFactory.create(company=company)


# --- Access -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "read", "write"),
    [
        (RoleCode.CUSTOMER_SERVICE, 200, 201),
        (RoleCode.DISPATCHER, 200, 403),
        (RoleCode.WAREHOUSE_STAFF, 403, 403),
        (RoleCode.DRIVER, 403, 403),
    ],
)
def test_permissions(
    role: RoleCode, read: int, write: int, company: Company, customer: Customer
) -> None:
    client = client_for(UserFactory.create(company=company, roles=[role]))

    assert client.get(list_url(customer)).status_code == read
    assert client.post(list_url(customer), VALID, format="json").status_code == write


def test_anonymous_requests_are_401(customer: Customer) -> None:
    address = AddressFactory.create(customer=customer)
    client = APIClient()

    responses = [
        client.get(list_url(customer)),
        client.post(list_url(customer), VALID, format="json"),
        client.get(detail_url(address)),
        client.patch(detail_url(address), {"city": "x"}, format="json"),
        client.post(archive_url(address)),
    ]

    assert {r.status_code for r in responses} == {401}


# --- Create -----------------------------------------------------------------------------


def test_create_without_coordinates(client: APIClient, customer: Customer) -> None:
    response = client.post(list_url(customer), VALID, format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["country"] == "DE"
    assert body["latitude"] is None and body["longitude"] is None
    assert body["geocode_confidence"] == GeocodeConfidence.NONE
    address = Address.objects.get(public_id=body["public_id"])
    assert address.customer == customer and address.company == customer.company
    entry = AuditLog.objects.get(action=AuditAction.ADDRESS_CREATED)
    assert entry.after is None  # address text never reaches the audit log


def test_create_with_coordinates_is_manual(client: APIClient, customer: Customer) -> None:
    response = client.post(
        list_url(customer), {**VALID, "latitude": 52.52, "longitude": 13.405}, format="json"
    )

    assert response.status_code == 201
    body = response.json()
    assert (body["latitude"], body["longitude"]) == (52.52, 13.405)
    assert body["geocode_confidence"] == GeocodeConfidence.MANUAL
    location = Address.objects.get(public_id=body["public_id"]).location
    assert location is not None
    assert (location.x, location.y, location.srid) == (13.405, 52.52, 4326)


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"line1": ""}, "line1"),
        ({"city": ""}, "city"),
        ({"country": "XX"}, "country"),
        ({"country": "DEU"}, "country"),
        ({"phone": "0301234"}, "phone"),
        ({"latitude": 52.5}, "latitude"),
        ({"latitude": 52.5, "longitude": None}, "latitude"),
        ({"latitude": 90.1, "longitude": 0}, "latitude"),
        ({"latitude": 0, "longitude": -180.5}, "longitude"),
    ],
)
def test_create_validation_errors(
    client: APIClient, customer: Customer, override: dict[str, Any], field: str
) -> None:
    response = client.post(list_url(customer), {**VALID, **override}, format="json")

    assert response.status_code == 400
    assert field in response.json()["errors"]
    assert not Address.objects.exists()


def test_create_for_archived_customer_is_409(client: APIClient, customer: Customer) -> None:
    client.post(f"/api/v1/customers/{customer.public_id}/archive/")

    response = client.post(list_url(customer), VALID, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "customer.archived"


def test_create_for_other_company_customer_is_404(client: APIClient) -> None:
    outsider = CustomerFactory.create()

    response = client.post(list_url(outsider), VALID, format="json")

    assert response.status_code == 404
    assert not Address.objects.exists()


# --- List -------------------------------------------------------------------------------


def test_list_shows_only_this_customers_active_addresses(
    client: APIClient, company: Company, customer: Customer
) -> None:
    mine = AddressFactory.create(customer=customer)
    archived = AddressFactory.create(customer=customer)
    AddressFactory.create(customer=CustomerFactory.create(company=company))  # sibling customer
    client.post(archive_url(archived))

    default = client.get(list_url(customer)).json()["results"]
    everything = client.get(list_url(customer), {"include_archived": "true"}).json()["results"]

    assert [r["public_id"] for r in default] == [str(mine.public_id)]
    assert {r["public_id"] for r in everything} == {str(mine.public_id), str(archived.public_id)}


@pytest.mark.parametrize("rows", [1, 10])
def test_list_query_count_is_constant(
    client: APIClient, customer: Customer, rows: int, django_assert_num_queries: Any
) -> None:
    AddressFactory.create_batch(rows, customer=customer)

    # auth (family + user + company), permissions, customer, page
    with django_assert_num_queries(4):
        assert client.get(list_url(customer)).status_code == 200


# --- Detail, update and archive ---------------------------------------------------------


def test_detail(client: APIClient, customer: Customer) -> None:
    address = AddressFactory.create(customer=customer)

    response = client.get(detail_url(address))

    assert response.status_code == 200
    assert response.json()["line1"] == address.line1


def test_address_under_wrong_customer_is_404(
    client: APIClient, company: Company, customer: Customer
) -> None:
    address = AddressFactory.create(customer=CustomerFactory.create(company=company))

    response = client.get(f"{list_url(customer)}{address.public_id}/")

    assert response.status_code == 404


def test_patch_sets_then_clears_coordinates(client: APIClient, customer: Customer) -> None:
    address = AddressFactory.create(customer=customer)

    set_response = client.patch(
        detail_url(address), {"latitude": 40.7, "longitude": -74.0}, format="json"
    )
    keep_response = client.patch(detail_url(address), {"city": "New York"}, format="json")
    clear_response = client.patch(
        detail_url(address), {"latitude": None, "longitude": None}, format="json"
    )

    assert set_response.json()["geocode_confidence"] == GeocodeConfidence.MANUAL
    assert keep_response.json()["latitude"] == 40.7  # untouched by an unrelated PATCH
    assert keep_response.json()["city"] == "New York"
    assert clear_response.status_code == 200
    assert clear_response.json()["geocode_confidence"] == GeocodeConfidence.NONE
    address.refresh_from_db()
    assert address.location is None


def test_patch_validation_error(client: APIClient, customer: Customer) -> None:
    address = AddressFactory.create(customer=customer)

    response = client.patch(detail_url(address), {"country": "ZZ"}, format="json")

    assert response.status_code == 400
    assert "country" in response.json()["errors"]


def test_patch_archived_address_is_409(client: APIClient, customer: Customer) -> None:
    address = AddressFactory.create(customer=customer)
    client.post(archive_url(address))

    response = client.patch(detail_url(address), {"city": "x"}, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "address.archived"


def test_archive_is_idempotent_and_audited(client: APIClient, customer: Customer) -> None:
    address = AddressFactory.create(customer=customer)

    first = client.post(archive_url(address))
    second = client.post(archive_url(address))

    assert first.status_code == second.status_code == 200
    assert first.json()["archived_at"] is not None
    assert AuditLog.objects.filter(action=AuditAction.ADDRESS_ARCHIVED).count() == 1


def test_other_company_address_is_404(client: APIClient) -> None:
    outsider = AddressFactory.create(line1="1 Secret Road")

    for response in (
        client.get(detail_url(outsider)),
        client.patch(detail_url(outsider), {"city": "x"}, format="json"),
        client.post(archive_url(outsider)),
    ):
        assert response.status_code == 404
        assert "Secret Road" not in response.content.decode()
