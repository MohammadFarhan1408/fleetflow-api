import uuid
from decimal import Decimal
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.accounts.tests.conftest import client_for
from apps.customers.factories import AddressFactory, CustomerFactory
from apps.customers.models import Address, Customer
from apps.tenancy.factories import BranchFactory, CompanyFactory, ServiceTypeFactory
from apps.tenancy.models import Branch, Company, ServiceType

SHIPMENTS_URL = "/api/v1/shipments/"


def detail_url(public_id: Any) -> str:
    return f"{SHIPMENTS_URL}{public_id}/"


def token_url(public_id: Any) -> str:
    return f"{detail_url(public_id)}tracking-token/"


def item(**overrides: Any) -> dict[str, Any]:
    return {"weight_kg": "2.5", "length_cm": "40", "width_cm": "30", "height_cm": "20"} | overrides


def inline_address(**overrides: Any) -> dict[str, Any]:
    return {
        "contact_name": "Rita Recipient",
        "phone": "+4930123456",
        "line1": "Unter den Linden 1",
        "city": "Berlin",
        "postal_code": "10117",
        "country": "de",
        "latitude": 52.517,
        "longitude": 13.389,
    } | overrides


def create(client: APIClient, body: dict[str, Any], key: str | None = None) -> Any:
    """POST a shipment with a fresh Idempotency-Key unless one is given."""
    return client.post(
        SHIPMENTS_URL, body, format="json", headers={"Idempotency-Key": key or str(uuid.uuid4())}
    )


def make_user(company: Company, role: RoleCode, *branches: Branch) -> User:
    user = UserFactory.create(company=company, roles=[role])
    user.branches.add(*branches)
    return user


@pytest.fixture
def company() -> Company:
    return CompanyFactory.create()


@pytest.fixture
def branch(company: Company) -> Branch:
    return BranchFactory.create(company=company, code="NY1")


@pytest.fixture
def other_branch(company: Company) -> Branch:
    return BranchFactory.create(company=company, code="LA1")


@pytest.fixture
def standard(company: Company) -> ServiceType:
    return ServiceTypeFactory.create(company=company, code="standard")


@pytest.fixture
def freight(company: Company) -> ServiceType:
    return ServiceTypeFactory.create(
        company=company,
        code="freight",
        max_weight_kg=Decimal(1000),
        max_length_cm=Decimal(240),
        max_width_cm=Decimal(120),
        max_height_cm=Decimal(220),
    )


@pytest.fixture
def customer(company: Company) -> Customer:
    return CustomerFactory.create(company=company, name="Acme GmbH")


@pytest.fixture
def pickup_address(customer: Customer) -> Address:
    return AddressFactory.create(customer=customer, contact_name="")


@pytest.fixture
def dispatcher(company: Company, branch: Branch) -> User:
    return make_user(company, RoleCode.DISPATCHER, branch)


@pytest.fixture
def client(dispatcher: User) -> APIClient:
    return client_for(dispatcher)


@pytest.fixture
def valid(
    branch: Branch, customer: Customer, pickup_address: Address, standard: ServiceType
) -> dict[str, Any]:
    """A valid create body: pickup from the address book, delivery typed in, 2 parcels."""
    return {
        "branch_id": str(branch.public_id),
        "customer_id": str(customer.public_id),
        "service_type": "standard",
        "pickup": {"address_id": str(pickup_address.public_id)},
        "delivery": inline_address(),
        "items": [item(description="Books"), item(weight_kg="4")],
    }
