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
from apps.dispatch.factories import RouteFactory, RouteStopFactory
from apps.dispatch.models import RouteStatus
from apps.fleet.factories import (
    DriverDocumentFactory,
    DriverFactory,
    DriverLeaveFactory,
    VehicleDocumentFactory,
    VehicleFactory,
)
from apps.shipments.factories import ShipmentFactory
from apps.tenancy.factories import BranchFactory, CompanyFactory
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


def _driver_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    driver = DriverFactory.create(company=company, user__full_name="Company A Driver")
    return {"public_id": driver.public_id}, [
        str(driver.public_id),
        driver.user.full_name,
        driver.user.email,
    ]


def _driver_leaves_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    kwargs, secrets = _leave_in(company)
    return {"driver_id": kwargs["driver_id"]}, secrets


def _leave_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    leave = DriverLeaveFactory.create(driver__company=company, reason="Company A secret leave")
    kwargs = {"driver_id": leave.driver.public_id, "public_id": leave.public_id}
    return kwargs, [str(leave.public_id), leave.reason, leave.driver.user.email]


def _branch_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    branch = BranchFactory.create(company=company, name="Secret Hub", code="SECRETX")
    return {"public_id": branch.public_id}, [str(branch.public_id), branch.name, branch.code]


def _vehicle_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    vehicle = VehicleFactory.create(company=company, registration_number="AAA 111")
    return {"public_id": vehicle.public_id}, [str(vehicle.public_id), vehicle.registration_number]


def _driver_documents_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    kwargs, secrets = _driver_document_in(company)
    return {"driver_id": kwargs["driver_id"]}, secrets


def _driver_document_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    document = DriverDocumentFactory.create(driver__company=company, number="A-SECRET-LIC-77")
    kwargs = {"driver_id": document.driver.public_id, "public_id": document.public_id}
    return kwargs, [str(document.public_id), document.number, document.upload.object_key]


def _vehicle_documents_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    kwargs, secrets = _vehicle_document_in(company)
    return {"vehicle_id": kwargs["vehicle_id"]}, secrets


def _vehicle_document_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    document = VehicleDocumentFactory.create(vehicle__company=company, number="A-SECRET-REG-77")
    kwargs = {"vehicle_id": document.vehicle.public_id, "public_id": document.public_id}
    return kwargs, [str(document.public_id), document.number, document.upload.object_key]


def _route_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    route = RouteFactory.create(branch__company=company)
    return {"public_id": route.public_id}, [str(route.public_id)]


def _route_stop_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    stop = RouteStopFactory.create(route__branch__company=company)
    kwargs = {"route_id": stop.route.public_id, "public_id": stop.public_id}
    secrets = [str(stop.public_id), stop.shipment.tracking_number, str(stop.shipment.public_id)]
    return kwargs, secrets


def _my_route_in(company: Company) -> tuple[dict[str, Any], list[str]]:
    route = RouteFactory.create(branch__company=company, status=RouteStatus.DISPATCHED)
    return {"public_id": route.public_id}, [str(route.public_id)]


# Routes only the `driver` role can call, on their own routes: the sweep calls them as an
# active driver of company B (an owner would get 403, which proves nothing about tenancy).
DRIVER_ROUTES = {"me-route-detail", "me-route-start"}

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
    "driver-detail": _driver_in,
    "driver-status": _driver_in,
    "driver-archive": _driver_in,
    "driver-unarchive": _driver_in,
    "driver-leave-list": _driver_leaves_in,
    "driver-leave-detail": _leave_in,
    "vehicle-detail": _vehicle_in,
    "vehicle-status": _vehicle_in,
    "vehicle-archive": _vehicle_in,
    "driver-document-list": _driver_documents_in,
    "driver-document-detail": _driver_document_in,
    "driver-document-archive": _driver_document_in,
    "driver-document-download": _driver_document_in,
    "vehicle-document-list": _vehicle_documents_in,
    "vehicle-document-detail": _vehicle_document_in,
    "vehicle-document-archive": _vehicle_document_in,
    "vehicle-document-download": _vehicle_document_in,
    "route-detail": _route_in,
    "route-stops": _route_in,
    "route-assign": _route_in,
    "route-dispatch": _route_in,
    "route-reassign": _route_in,
    "me-route-detail": _my_route_in,
    "me-route-start": _my_route_in,
    "route-unassign": _route_in,
    "route-reorder": _route_in,
    "route-suggest-order": _route_in,
    "route-cancel": _route_in,
    "route-stop-detail": _route_stop_in,
}

# Routes that are public by design: the path segment is a bearer credential, not a
# tenant object ID, so "another company's user gets 404" does not apply. Each has its own
# tests proving a token opens exactly one shipment.
PUBLIC_ROUTES = {"public-tracking"}

# URL name → strings in company A that must never appear in company B's list response.
LIST_ROUTES: dict[str, ObjectFactory] = {
    "user-list": _user_in,
    "branch-list": _branch_in,
    "customer-list": _customer_in,
    "shipment-list": _shipment_in,
    "driver-list": _driver_in,
    "vehicle-list": _vehicle_in,
    "route-list": _route_stop_in,
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


def _client_for_other_company(*, as_driver: bool = False) -> APIClient:
    # Highest privilege, company B; or an active driver of company B for driver-only routes.
    outsider = (
        DriverFactory.create().user if as_driver else UserFactory.create(roles=[RoleCode.OWNER])
    )
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
    client = _client_for_other_company(as_driver=str(pattern.name) in DRIVER_ROUTES)

    for method in _served_methods(pattern):
        send = getattr(client, method.lower())
        # driver-app mutations require the key (architecture §20) before the lookup
        headers = {"Idempotency-Key": "sweep"} if str(pattern.name) in DRIVER_ROUTES else {}
        response = send(url, MUTATING_BODY, format="json", headers=headers)
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
