import uuid
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.accounts.tests.conftest import client_for
from apps.fleet.factories import (
    DriverDocumentFactory,
    DriverFactory,
    VehicleDocumentFactory,
)
from apps.fleet.models import Driver, Vehicle, VehicleDocumentType
from apps.tenancy.factories import BranchFactory, CompanyFactory
from apps.tenancy.models import Branch, Company
from apps.uploads.tests.conftest import confirm_url, make_jpeg, upload_file

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


def make_driver(branch: Branch, *, licensed: bool = True, **overrides: Any) -> Driver:
    """A driver with a valid licence, so they are available unless a test says otherwise."""
    driver = DriverFactory.create(company=branch.company, branch=branch, **overrides)
    if licensed:
        DriverDocumentFactory.create(driver=driver)
    return driver


def document_vehicle(vehicle: Vehicle | str) -> Vehicle:
    """Give a vehicle (or the public ID of one) valid registration and insurance."""
    if isinstance(vehicle, str):
        vehicle = Vehicle.objects.get(public_id=vehicle)
    VehicleDocumentFactory.create(vehicle=vehicle)
    VehicleDocumentFactory.create(vehicle=vehicle, type=VehicleDocumentType.INSURANCE)
    return vehicle


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


def confirmed_upload(client: APIClient, data: bytes | None = None) -> str:
    """A real upload for `client`'s user: presigned PUT to the bucket, then confirm. Returns
    its public ID, ready for a document to claim."""
    ticket = upload_file(client, "image/jpeg", data or make_jpeg())
    assert client.post(confirm_url(ticket["id"])).status_code == 200
    return str(ticket["id"])


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
