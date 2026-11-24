"""Mandatory tenant-isolation sweep (architecture §26.2).

Every `/api/v1/` route that takes an object ID is called, with every method it serves, by
an owner of company B against company A's object. Each must answer 404 without echoing
A's data. A new parametrized route with no entry in OBJECT_FACTORIES fails the sweep on
purpose: register how to create a company-A object for it.
"""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode
from apps.accounts.tests.conftest import login
from apps.customers.factories import AddressFactory, CustomerFactory
from apps.shipments.factories import ShipmentFactory
from apps.tenancy.factories import CompanyFactory
from apps.tenancy.models import Company
from apps.uploads.factories import UploadFactory

type ObjectFactory = Callable[[Company], tuple[dict[str, Any], list[str]]]


def _user_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    user = UserFactory.create(company=company, roles=[RoleCode.VIEWER])
    return {"public_id": user.public_id}, [str(user.public_id), user.email]


def _customer_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    customer = CustomerFactory.create(company=company, name="Company A Customer")
    return {"public_id": customer.public_id}, [str(customer.public_id), customer.name]


def _customer_addresses_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    kwargs, secrets = _address_in(company)
    return {"customer_id": kwargs["customer_id"]}, secrets


def _address_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    address = AddressFactory.create(customer__company=company, line1="1 Company A Street")
    kwargs = {"customer_id": address.customer.public_id, "public_id": address.public_id}
    return kwargs, [str(address.public_id), address.line1, address.customer.name]


def _shipment_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    shipment = ShipmentFactory.create(branch__company=company, external_reference="A-REF-1")
    secrets = [
        str(shipment.public_id),
        shipment.tracking_number,
        shipment.tracking_token,
        shipment.external_reference,
    ]
    return {"public_id": shipment.public_id}, secrets


def _upload_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    upload = UploadFactory.create(company=company)
    return {"public_id": upload.public_id}, [str(upload.public_id), upload.object_key]


# URL name → creates company A's object; returns (URL kwargs, strings that must not leak).
OBJECT_FACTORIES: dict[str, ObjectFactory] = {
    "user-detail": _user_in,
    "customer-detail": _customer_in,
    "customer-archive": _customer_in,
    "customer-address-list": _customer_addresses_in,
    "customer-address-detail": _address_in,
    "customer-address-archive": _address_in,
    "shipment-detail": _shipment_in,
    "shipment-tracking-token": _shipment_in,
    "shipment-transition": _shipment_in,
    "shipment-correction": _shipment_in,
    "shipment-events": _shipment_in,
    "upload-confirm": _upload_in,
}

# Routes that are public by design: the path segment is a bearer credential, not a
# tenant object ID, so "another company's user gets 404" does not apply. Each has its own
# tests proving a token opens exactly one shipment.
PUBLIC_ROUTES = {"public-tracking"}

# URL name → strings in company A that must never appear in company B's list response.
LIST_ROUTES: dict[str, ObjectFactory] = {
    "user-list": _user_in,
    "customer-list": _customer_in,
    "shipment-list": _shipment_in,
}

# Valid for users, customers and addresses alike, so each route reaches its object lookup.
# Shipment views look the object up before validating the body.
MUTATING_BODY = {
    "full_name": "pwned",
    "roles": ["owner"],
    "is_active": False,
    "name": "pwned",
    "kind": "individual",
    "line1": "pwned",
    "city": "pwned",
    "country": "US",
}


def _api_routes() -> Iterator[tuple[str, URLPattern]]:
    def walk(
        patterns: list[URLPattern | URLResolver], prefix: str
    ) -> Iterator[tuple[str, URLPattern]]:
        for pattern in patterns:
            route = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                yield from walk(pattern.url_patterns, route)
            elif route.startswith("api/v1/"):
                yield route, pattern

    yield from walk(get_resolver().url_patterns, "")


def _served_methods(pattern: URLPattern) -> list[str]:
    view_class = pattern.callback.view_class  # type: ignore[attr-defined]  # class-based views
    return [m.upper() for m in ("get", "put", "patch", "delete", "post") if hasattr(view_class, m)]


PARAMETRIZED = [
    (route, p) for route, p in _api_routes() if "<" in route and p.name not in PUBLIC_ROUTES
]


def _client_for_other_company() -> APIClient:
    outsider = UserFactory.create(roles=[RoleCode.OWNER])  # highest privilege, company B
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {login(client, outsider.email).json()['access']}"
    )
    return client


@pytest.mark.django_db
def test_every_object_route_is_registered() -> None:
    names = {str(pattern.name) for _, pattern in PARAMETRIZED}

    assert names, "sweep found no object routes; is the URL walk broken?"
    assert names <= OBJECT_FACTORIES.keys(), (
        f"Register these routes in OBJECT_FACTORIES: {sorted(names - OBJECT_FACTORIES.keys())}"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(("route", "pattern"), PARAMETRIZED, ids=[r for r, _ in PARAMETRIZED])
def test_other_company_object_is_404_for_every_method(route: str, pattern: URLPattern) -> None:
    factory = OBJECT_FACTORIES[str(pattern.name)]
    kwargs, secrets = factory(CompanyFactory.create())
    url = "/" + route
    for name, value in kwargs.items():
        url = url.replace(f"<uuid:{name}>", str(value))
    client = _client_for_other_company()

    for method in _served_methods(pattern):
        send = getattr(client, method.lower())
        response = send(url, MUTATING_BODY, format="json")
        body = response.content.decode()
        assert response.status_code == 404, (method, url, response.status_code)
        assert not any(secret in body for secret in secrets), (method, url)


@pytest.mark.django_db
@pytest.mark.parametrize("name", sorted(LIST_ROUTES))
def test_list_routes_never_return_other_company_rows(name: str) -> None:
    _, secrets = LIST_ROUTES[name](CompanyFactory.create())
    route = next(r for r, p in _api_routes() if p.name == name)

    body = _client_for_other_company().get("/" + route).content.decode()

    assert not any(secret in body for secret in secrets)
