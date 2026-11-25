import uuid
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.accounts.tests.conftest import client_for
from apps.fleet.factories import DriverFactory
from apps.fleet.models import Driver
from apps.tenancy.factories import BranchFactory, CompanyFactory
from apps.tenancy.models import Branch, Company

DRIVERS_URL = "/api/v1/drivers/"
VEHICLES_URL = "/api/v1/vehicles/"


def driver_url(driver_id: Any, suffix: str = "") -> str:
    return f"{DRIVERS_URL}{driver_id}/{suffix}"


def vehicle_url(vehicle_id: Any, suffix: str = "") -> str:
    return f"{VEHICLES_URL}{vehicle_id}/{suffix}"


def post(client: APIClient, url: str, body: dict[str, Any] | None = None, **headers: str) -> Any:
    return client.post(url, body or {}, format="json", headers=headers)


def make_user(company: Company, role: RoleCode, *branches: Branch) -> User:
    user = UserFactory.create(company=company, roles=[role])
    user.branches.add(*branches)
    return user


def make_driver(branch: Branch, **overrides: Any) -> Driver:
    return DriverFactory.create(company=branch.company, branch=branch, **overrides)


def vehicle_body(branch: Branch, **overrides: Any) -> dict[str, Any]:
    return {
        "registration_number": "B FF 1234",
        "branch_id": str(branch.public_id),
        "required_license_category": "C",
        "capacity_weight_kg": "3500",
        "capacity_volume_m3": "20",
        "make": "MAN",
        "model": "TGL",
    } | overrides


def new_key() -> str:
    return str(uuid.uuid4())


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
def fleet_manager(company: Company, branch: Branch) -> User:
    return make_user(company, RoleCode.FLEET_MANAGER, branch)


@pytest.fixture
def client(fleet_manager: User) -> APIClient:
    return client_for(fleet_manager)


@pytest.fixture
def driver_user(company: Company) -> User:
    return UserFactory.create(company=company, roles=[RoleCode.DRIVER])
