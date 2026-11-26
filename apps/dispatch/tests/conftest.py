from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.accounts.tests.conftest import client_for
from apps.dispatch.factories import RouteFactory
from apps.dispatch.models import Route
from apps.shipments.factories import ShipmentFactory
from apps.shipments.models import Shipment, ShipmentStatus
from apps.tenancy.factories import BranchFactory, CompanyFactory
from apps.tenancy.models import Branch, Company

ROUTES_URL = "/api/v1/routes/"


def route_url(route_id: Any, suffix: str = "") -> str:
    public_id = getattr(route_id, "public_id", route_id)
    return f"{ROUTES_URL}{public_id}/{suffix}"


def post(client: APIClient, url: str, body: dict[str, Any] | None = None, **headers: str) -> Any:
    return client.post(url, body or {}, format="json", headers=headers)


def make_user(company: Company, role: RoleCode, *branches: Branch) -> User:
    user = UserFactory.create(company=company, roles=[role])
    user.branches.add(*branches)
    return user


def make_shipment(branch: Branch, status: ShipmentStatus = ShipmentStatus.CREATED) -> Shipment:
    return ShipmentFactory.create(branch=branch, status=status)


def version_of(route: Route) -> int:
    return Route.objects.get(pk=route.pk).version


def add_stop(
    client: APIClient,
    route: Route,
    shipment: Shipment,
    stop_type: str = "delivery",
    *,
    version: int | None = None,
    **extra: Any,
) -> Any:
    body = {
        "version": version if version is not None else version_of(route),
        "shipment_id": str(shipment.public_id),
        "type": stop_type,
        **extra,
    }
    return post(client, route_url(route, "stops/"), body)


def remove_stop(client: APIClient, route: Route, stop_id: Any, version: int | None = None) -> Any:
    v = version if version is not None else version_of(route)
    return client.delete(route_url(route, f"stops/{stop_id}/") + f"?version={v}")


def code(response: Any) -> str:
    return str(response.json()["code"])


@pytest.fixture
def company() -> Company:
    return CompanyFactory.create(timezone="Europe/Berlin")


@pytest.fixture
def branch(company: Company) -> Branch:
    return BranchFactory.create(company=company, code="BER1")


@pytest.fixture
def other_branch(company: Company) -> Branch:
    return BranchFactory.create(company=company, code="MUC1")


@pytest.fixture
def dispatcher(company: Company, branch: Branch) -> User:
    return make_user(company, RoleCode.DISPATCHER, branch)


@pytest.fixture
def client(dispatcher: User) -> APIClient:
    return client_for(dispatcher)


@pytest.fixture
def route(branch: Branch) -> Route:
    return RouteFactory.create(branch=branch)
