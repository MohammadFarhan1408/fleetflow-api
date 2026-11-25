from decimal import Decimal
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from apps.accounts.models import RoleCode
from apps.accounts.tests.conftest import client_for
from apps.audit.models import AuditAction, AuditLog
from apps.fleet.factories import VehicleFactory
from apps.fleet.models import Vehicle, VehicleStatus
from apps.fleet.tests.conftest import (
    VEHICLES_URL,
    make_user,
    new_key,
    post,
    vehicle_body,
    vehicle_url,
)
from apps.tenancy.factories import BranchFactory, CompanyFactory
from apps.tenancy.models import Branch, Company

pytestmark = pytest.mark.django_db


def create(client: APIClient, branch: Branch, **overrides: Any) -> Any:
    return post(
        client, VEHICLES_URL, vehicle_body(branch, **overrides), **{"Idempotency-Key": new_key()}
    )


def code(response: Any) -> str:
    return str(response.json()["code"])


def test_create_vehicle(client: APIClient, branch: Branch) -> None:
    response = create(client, branch)

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["registration_number"] == "B FF 1234"
    assert body["branch"]["code"] == "BER1"
    assert body["required_license_category"] == "C"
    assert (body["capacity_weight_kg"], body["capacity_volume_m3"]) == ("3500.000", "20.000")
    assert body["status"] == "active"
    assert body["availability"] == {"available": True, "reasons": []}
    entry = AuditLog.objects.get(action=AuditAction.VEHICLE_CREATED)
    assert entry.after is None  # IDs only; the plate stays out of the log


def test_create_is_idempotent_with_the_same_key(client: APIClient, branch: Branch) -> None:
    key = new_key()
    body = vehicle_body(branch)

    first = post(client, VEHICLES_URL, body, **{"Idempotency-Key": key})
    second = post(client, VEHICLES_URL, body, **{"Idempotency-Key": key})

    assert (first.status_code, second.status_code) == (201, 201)
    assert Vehicle.objects.count() == 1


@pytest.mark.parametrize(
    ("typed", "stored"),
    [(" ab  12 cd ", "AB 12 CD"), ("b-ff 99", "B-FF 99"), ("x1", "X1"), ("A" * 20, "A" * 20)],
)
def test_registration_is_normalized(
    client: APIClient, branch: Branch, typed: str, stored: str
) -> None:
    response = create(client, branch, registration_number=typed)

    assert response.status_code == 201, response.content
    assert response.json()["registration_number"] == stored


@pytest.mark.parametrize("plate", ["", "   ", "A", "AB!", "AB_12", "-AB 12", "A" * 21, "ÄÖ 12"])
def test_invalid_registration_is_400(client: APIClient, branch: Branch, plate: str) -> None:
    response = create(client, branch, registration_number=plate)

    assert response.status_code == 400
    assert "registration_number" in response.json()["errors"]


def test_duplicate_registration_is_409_in_the_same_company_only(
    client: APIClient, branch: Branch
) -> None:
    create(client, branch, registration_number="AB 12 CD")
    foreign_branch = BranchFactory.create(company=CompanyFactory.create())
    VehicleFactory.create(
        company=foreign_branch.company, branch=foreign_branch, registration_number="XY 99"
    )

    same = create(client, branch, registration_number=" ab 12  cd")
    other_company = create(client, branch, registration_number="XY 99")

    assert (same.status_code, code(same)) == (409, "vehicle.duplicate_registration")
    assert other_company.status_code == 201
    assert Vehicle.objects.filter(registration_number="AB 12 CD").count() == 1


def test_the_database_enforces_unique_registrations(branch: Branch) -> None:
    VehicleFactory.create(company=branch.company, branch=branch, registration_number="AB 12")

    with pytest.raises(IntegrityError), transaction.atomic():
        VehicleFactory.create(company=branch.company, branch=branch, registration_number="AB 12")


@pytest.mark.parametrize(
    ("field", "value", "ok"),
    [
        ("capacity_weight_kg", "0", False),
        ("capacity_weight_kg", "0.001", True),
        ("capacity_weight_kg", "60000", True),
        ("capacity_weight_kg", "60000.001", False),
        ("capacity_weight_kg", "-5", False),
        ("capacity_weight_kg", "abc", False),
        ("capacity_volume_m3", "0", False),
        ("capacity_volume_m3", "200", True),
        ("capacity_volume_m3", "200.001", False),
    ],
)
def test_capacity_bounds(
    client: APIClient, branch: Branch, field: str, value: str, ok: bool
) -> None:
    response = create(client, branch, **{field: value})

    assert response.status_code == (201 if ok else 400), response.content
    if not ok:
        assert field in response.json()["errors"]


@pytest.mark.parametrize("category", ["", "Z", "c", "B1", "AM"])
def test_invalid_license_category_is_400(client: APIClient, branch: Branch, category: str) -> None:
    response = create(client, branch, required_license_category=category)

    assert response.status_code == 400
    assert "required_license_category" in response.json()["errors"]


def test_all_vienna_categories_are_accepted(client: APIClient, branch: Branch) -> None:
    for index, category in enumerate(
        ["A", "B", "BE", "C1", "C1E", "C", "CE", "D1", "D1E", "D", "DE"]
    ):
        response = create(
            client, branch, required_license_category=category, registration_number=f"V {index}"
        )
        assert response.status_code == 201, (category, response.content)


def test_create_rejects_a_branch_outside_the_scope(client: APIClient, other_branch: Branch) -> None:
    response = create(client, other_branch)

    assert (response.status_code, code(response)) == (400, "validation.invalid_reference")
    foreign = BranchFactory.create(company=CompanyFactory.create())
    assert code(create(client, foreign)) == "validation.invalid_reference"
    assert str(foreign.public_id) not in create(client, foreign).content.decode()


def test_patch_updates_fields_and_keeps_the_rest(client: APIClient, branch: Branch) -> None:
    created = create(client, branch).json()

    response = client.patch(
        vehicle_url(created["public_id"]),
        {"capacity_weight_kg": "7500", "make": "Volvo"},
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["capacity_weight_kg"], body["make"]) == ("7500.000", "Volvo")
    assert body["registration_number"] == "B FF 1234"
    assert body["model"] == "TGL"


def test_patch_to_a_taken_registration_is_409_but_to_its_own_is_fine(
    client: APIClient, branch: Branch
) -> None:
    first = create(client, branch, registration_number="AA 1").json()
    create(client, branch, registration_number="BB 2")

    taken = client.patch(
        vehicle_url(first["public_id"]), {"registration_number": "bb 2"}, format="json"
    )
    same = client.patch(
        vehicle_url(first["public_id"]), {"registration_number": "aa 1"}, format="json"
    )

    assert (taken.status_code, code(taken)) == (409, "vehicle.duplicate_registration")
    assert same.status_code == 200


def test_status_requires_a_reason_unless_active(client: APIClient, branch: Branch) -> None:
    vehicle = create(client, branch).json()
    url = vehicle_url(vehicle["public_id"], "status/")

    missing = post(client, url, {"status": "in_maintenance"})
    maintenance = post(client, url, {"status": "in_maintenance", "reason": "Brake service"})
    broken = post(client, url, {"status": "out_of_service", "reason": "Accident"})
    back = post(client, url, {"status": "active"})

    assert missing.status_code == 400
    assert maintenance.json()["availability"] == {"available": False, "reasons": ["in_maintenance"]}
    assert broken.json()["availability"]["reasons"] == ["out_of_service"]
    assert back.json()["availability"]["available"] is True
    assert back.json()["status_reason"] == ""
    audited = AuditLog.objects.filter(action=AuditAction.VEHICLE_STATUS_CHANGED)
    assert audited.count() == 3
    assert "Brake service" not in str(list(audited.values_list("after", flat=True)))


def test_repeating_a_vehicle_status_is_a_no_op(client: APIClient, branch: Branch) -> None:
    vehicle = create(client, branch).json()
    url = vehicle_url(vehicle["public_id"], "status/")
    body = {"status": "out_of_service", "reason": "Accident"}

    post(client, url, body)
    post(client, url, body)

    assert AuditLog.objects.filter(action=AuditAction.VEHICLE_STATUS_CHANGED).count() == 1


def test_the_database_requires_a_reason_for_a_non_active_vehicle(branch: Branch) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        VehicleFactory.create(
            company=branch.company, branch=branch, status=VehicleStatus.OUT_OF_SERVICE
        )


def test_archive_is_idempotent_and_blocks_changes(client: APIClient, branch: Branch) -> None:
    vehicle = create(client, branch).json()
    url = vehicle_url(vehicle["public_id"], "archive/")

    first, second = post(client, url), post(client, url)
    patch = client.patch(vehicle_url(vehicle["public_id"]), {"make": "X"}, format="json")
    status = post(client, vehicle_url(vehicle["public_id"], "status/"), {"status": "active"})

    assert first.json()["archived_at"] == second.json()["archived_at"]
    assert first.json()["availability"]["reasons"] == ["archived"]
    assert (patch.status_code, code(patch)) == (409, "vehicle.archived")
    assert (status.status_code, code(status)) == (409, "vehicle.archived")
    assert AuditLog.objects.filter(action=AuditAction.VEHICLE_ARCHIVED).count() == 1


def test_list_filters_and_scope(
    client: APIClient, company: Company, branch: Branch, other_branch: Branch
) -> None:
    truck = create(client, branch, registration_number="TRUCK 1").json()
    van = create(client, branch, registration_number="VAN 2", required_license_category="B").json()
    post(
        client,
        vehicle_url(van["public_id"], "status/"),
        {"status": "in_maintenance", "reason": "x"},
    )
    old = create(client, branch, registration_number="OLD 3").json()
    post(client, vehicle_url(old["public_id"], "archive/"))
    elsewhere = VehicleFactory.create(
        company=company, branch=other_branch, registration_number="FAR 4"
    )

    def ids(query: str, who: APIClient = client) -> set[str]:
        response = who.get(f"{VEHICLES_URL}?{query}")
        assert response.status_code == 200, response.content
        return {v["public_id"] for v in response.json()["results"]}

    assert ids("") == {truck["public_id"], van["public_id"]}
    assert ids("include_archived=true") == {truck["public_id"], van["public_id"], old["public_id"]}
    assert ids("status=in_maintenance") == {van["public_id"]}
    assert ids("license_category=B") == {van["public_id"]}
    assert ids("search=tr") == {truck["public_id"]}
    assert ids("available=true") == {truck["public_id"]}
    assert ids("available=false") == {van["public_id"]}
    assert ids(f"branch={branch.public_id}") == {truck["public_id"], van["public_id"]}
    admin = client_for(make_user(company, RoleCode.ADMIN))
    assert str(elsewhere.public_id) in ids("", admin)
    assert str(elsewhere.public_id) not in ids("")
    assert client.get(vehicle_url(elsewhere.public_id)).status_code == 404


@pytest.mark.parametrize(
    "query", ["status=broken", "license_category=Z", "branch=x", "available=maybe"]
)
def test_list_rejects_invalid_filters(client: APIClient, query: str) -> None:
    assert client.get(f"{VEHICLES_URL}?{query}").status_code == 400


def test_list_query_count_does_not_grow_with_rows(
    client: APIClient, branch: Branch, django_assert_max_num_queries: Any
) -> None:
    VehicleFactory.create(company=branch.company, branch=branch)
    with django_assert_max_num_queries(6):
        assert client.get(VEHICLES_URL).status_code == 200
    VehicleFactory.create_batch(15, company=branch.company, branch=branch)

    with django_assert_max_num_queries(6):
        response = client.get(VEHICLES_URL)

    assert len(response.json()["results"]) == 16


READ_ROLES = {
    RoleCode.OWNER,
    RoleCode.ADMIN,
    RoleCode.OPS_MANAGER,
    RoleCode.DISPATCHER,
    RoleCode.FLEET_MANAGER,
    RoleCode.FINANCE,
    RoleCode.VIEWER,
}
WRITE_ROLES = {RoleCode.OWNER, RoleCode.ADMIN, RoleCode.OPS_MANAGER, RoleCode.FLEET_MANAGER}


@pytest.mark.parametrize("role", list(RoleCode))
def test_role_matrix(role: RoleCode, company: Company, branch: Branch) -> None:
    client = client_for(make_user(company, role, branch))
    existing = VehicleFactory.create(company=company, branch=branch)

    read = client.get(VEHICLES_URL)
    write = create(client, branch, registration_number="R 1")
    patch = client.patch(vehicle_url(existing.public_id), {"make": "X"}, format="json")
    archive = post(client, vehicle_url(existing.public_id, "archive/"))

    assert read.status_code == (200 if role in READ_ROLES else 403)
    assert write.status_code == (201 if role in WRITE_ROLES else 403)
    assert patch.status_code == (200 if role in WRITE_ROLES else 403)
    assert archive.status_code == (200 if role in WRITE_ROLES else 403)


def test_unauthenticated_is_401(branch: Branch) -> None:
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)

    assert APIClient().get(VEHICLES_URL).status_code == 401
    assert APIClient().get(vehicle_url(vehicle.public_id)).status_code == 401


def test_decimal_capacity_round_trips_as_strings(client: APIClient, branch: Branch) -> None:
    created = create(client, branch, capacity_weight_kg="1234.567", capacity_volume_m3="9.5")

    body = client.get(vehicle_url(created.json()["public_id"])).json()

    assert (body["capacity_weight_kg"], body["capacity_volume_m3"]) == ("1234.567", "9.500")
    assert Vehicle.objects.get().capacity_weight_kg == Decimal("1234.567")
