"""Assign and unassign a driver and vehicle (architecture §13, §26.4 assignment table)."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RoleCode
from apps.accounts.tests.conftest import client_for
from apps.audit.models import AuditAction, AuditLog
from apps.dispatch.factories import RouteFactory, RouteStopFactory
from apps.dispatch.models import Route, RouteStatus
from apps.dispatch.tests.conftest import (
    ROUTES_URL,
    add_stop,
    code,
    make_shipment,
    make_user,
    post,
    route_url,
    version_of,
)
from apps.fleet.factories import (
    DriverDocumentFactory,
    DriverFactory,
    DriverLeaveFactory,
    VehicleDocumentFactory,
    VehicleFactory,
)
from apps.fleet.models import (
    Driver,
    DriverStatus,
    LicenseCategory,
    Vehicle,
    VehicleDocumentType,
    VehicleStatus,
)
from apps.shipments.models import ShipmentStatus
from apps.tenancy.models import Branch, Company

pytestmark = pytest.mark.django_db

ROUTE_DATE = date(2099, 6, 15)  # RouteFactory's default


def make_driver(branch: Branch, **fields: Any) -> Driver:
    """An available driver with a valid B and C licence."""
    driver = DriverFactory.create(company=branch.company, branch=branch, **fields)
    DriverDocumentFactory.create(driver=driver)
    return driver


def make_vehicle(branch: Branch, **fields: Any) -> Vehicle:
    """An available C-category vehicle with valid registration and insurance."""
    vehicle = VehicleFactory.create(company=branch.company, branch=branch, **fields)
    VehicleDocumentFactory.create(vehicle=vehicle)
    VehicleDocumentFactory.create(vehicle=vehicle, type=VehicleDocumentType.INSURANCE)
    return vehicle


def assign(
    client: APIClient,
    route: Route,
    driver: Driver,
    vehicle: Vehicle,
    *,
    version: int | None = None,
    **extra: Any,
) -> Any:
    body = {
        "version": version if version is not None else version_of(route),
        "driver_id": str(driver.public_id),
        "vehicle_id": str(vehicle.public_id),
        **extra,
    }
    return post(client, route_url(route, "assign/"), body)


def unassign(client: APIClient, route: Route, version: int | None = None) -> Any:
    v = version if version is not None else version_of(route)
    return post(client, route_url(route, "unassign/"), {"version": v})


def assigned_route(branch: Branch, driver: Driver, vehicle: Vehicle, **fields: Any) -> Route:
    fields.setdefault("status", RouteStatus.ASSIGNED)
    return RouteFactory.create(branch=branch, driver=driver, vehicle=vehicle, **fields)


@pytest.fixture
def driver(branch: Branch) -> Driver:
    return make_driver(branch)


@pytest.fixture
def vehicle(branch: Branch) -> Vehicle:
    return make_vehicle(branch)


# --- happy path and idempotency --------------------------------------------------------------


def test_assign_sets_driver_vehicle_status_and_version(
    client: APIClient, route: Route, driver: Driver, vehicle: Vehicle
) -> None:
    response = assign(client, route, driver, vehicle)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "assigned"
    assert body["version"] == 2
    assert body["driver"] == {
        "public_id": str(driver.public_id),
        "full_name": driver.user.full_name,
    }
    assert body["vehicle"] == {
        "public_id": str(vehicle.public_id),
        "registration_number": vehicle.registration_number,
    }
    assert body["capacity_override_reason"] == ""
    entry = AuditLog.objects.get(action=AuditAction.ROUTE_ASSIGNED)
    assert entry.after == {
        "status": "assigned",
        "driver": str(driver.public_id),
        "vehicle": str(vehicle.public_id),
        "capacity_override": "false",
    }


def test_repeating_the_same_assignment_is_a_no_op_even_with_a_stale_version(
    client: APIClient, route: Route, driver: Driver, vehicle: Vehicle
) -> None:
    assert assign(client, route, driver, vehicle).status_code == 200

    again = assign(client, route, driver, vehicle, version=1)  # a retry after a lost response

    assert again.status_code == 200
    assert again.json()["version"] == 2
    assert AuditLog.objects.filter(action=AuditAction.ROUTE_ASSIGNED).count() == 1


def test_reassigning_replaces_driver_and_vehicle(
    client: APIClient, branch: Branch, route: Route, driver: Driver, vehicle: Vehicle
) -> None:
    assign(client, route, driver, vehicle)
    other_driver, other_vehicle = make_driver(branch), make_vehicle(branch)

    response = assign(client, route, other_driver, other_vehicle)

    assert response.status_code == 200
    assert response.json()["driver"]["public_id"] == str(other_driver.public_id)
    assert response.json()["version"] == 3
    # the first pair is free again
    second = RouteFactory.create(branch=branch)
    assert assign(client, second, driver, vehicle).status_code == 200


def test_a_stale_version_is_409(
    client: APIClient, route: Route, driver: Driver, vehicle: Vehicle
) -> None:
    response = assign(client, route, driver, vehicle, version=7)

    assert (response.status_code, code(response)) == (409, "route.version_conflict")


@pytest.mark.parametrize(
    "status", [RouteStatus.DISPATCHED, RouteStatus.IN_PROGRESS, RouteStatus.COMPLETED]
)
def test_only_planned_or_assigned_routes_can_be_assigned(
    client: APIClient, branch: Branch, driver: Driver, vehicle: Vehicle, status: RouteStatus
) -> None:
    other_driver, other_vehicle = make_driver(branch), make_vehicle(branch)
    route = assigned_route(branch, other_driver, other_vehicle, status=status)

    response = assign(client, route, driver, vehicle)

    assert (response.status_code, code(response)) == (409, "route.invalid_status")


def test_a_cancelled_route_cannot_be_assigned(
    client: APIClient, branch: Branch, driver: Driver, vehicle: Vehicle
) -> None:
    route = RouteFactory.create(
        branch=branch, status=RouteStatus.CANCELLED, cancelled_at="2099-01-01T00:00:00Z"
    )

    response = assign(client, route, driver, vehicle)

    assert (response.status_code, code(response)) == (409, "route.invalid_status")


def test_a_route_in_the_past_cannot_be_assigned(
    client: APIClient, branch: Branch, driver: Driver, vehicle: Vehicle
) -> None:
    route = RouteFactory.create(branch=branch, route_date=date(2020, 1, 1))

    response = assign(client, route, driver, vehicle)

    assert (response.status_code, code(response)) == (400, "validation.invalid")


# --- driver rules ----------------------------------------------------------------------------


def reasons(response: Any) -> list[str]:
    return list(response.json()["errors"]["reasons"])


def test_suspended_driver_is_409_with_reason(
    client: APIClient, branch: Branch, route: Route, vehicle: Vehicle
) -> None:
    driver = make_driver(branch, status=DriverStatus.SUSPENDED, status_reason="Hearing")

    response = assign(client, route, driver, vehicle)

    assert (response.status_code, code(response)) == (409, "assignment.driver_unavailable")
    assert reasons(response) == ["suspended"]
    assert Route.objects.get(pk=route.pk).status == RouteStatus.PLANNED


def test_driver_on_leave_that_day_is_409_but_leave_on_another_day_is_fine(
    client: APIClient, branch: Branch, route: Route, vehicle: Vehicle
) -> None:
    on_leave = make_driver(branch)
    DriverLeaveFactory.create(driver=on_leave, starts_on=ROUTE_DATE, ends_on=ROUTE_DATE)
    other_day = make_driver(branch)
    DriverLeaveFactory.create(
        driver=other_day,
        starts_on=ROUTE_DATE + timedelta(days=1),
        ends_on=ROUTE_DATE + timedelta(days=2),
    )

    blocked = assign(client, route, on_leave, vehicle)
    allowed = assign(client, route, other_day, vehicle)

    assert (blocked.status_code, code(blocked)) == (409, "assignment.driver_unavailable")
    assert reasons(blocked) == ["on_leave"]
    assert allowed.status_code == 200


def test_archived_driver_and_disabled_login_are_409(
    client: APIClient, branch: Branch, route: Route, vehicle: Vehicle
) -> None:
    archived = make_driver(branch, archived_at="2099-01-01T00:00:00Z")
    disabled = make_driver(branch)
    disabled.user.is_active = False
    disabled.user.save(update_fields=["is_active"])

    assert reasons(assign(client, route, archived, vehicle)) == ["archived"]
    assert reasons(assign(client, route, disabled, vehicle)) == ["login_disabled"]


def test_driver_without_a_licence_is_409(
    client: APIClient, branch: Branch, route: Route, vehicle: Vehicle
) -> None:
    driver = DriverFactory.create(company=branch.company, branch=branch)

    response = assign(client, route, driver, vehicle)

    assert (response.status_code, code(response)) == (409, "assignment.driver_unavailable")
    assert reasons(response) == ["license_missing"]


def test_licence_valid_through_the_route_date_is_allowed_and_the_day_before_is_not(
    client: APIClient, branch: Branch, route: Route, vehicle: Vehicle
) -> None:
    expires_today = DriverFactory.create(company=branch.company, branch=branch)
    DriverDocumentFactory.create(driver=expires_today, expires_on=ROUTE_DATE)
    expired_yesterday = DriverFactory.create(company=branch.company, branch=branch)
    DriverDocumentFactory.create(
        driver=expired_yesterday, expires_on=ROUTE_DATE - timedelta(days=1)
    )

    blocked = assign(client, route, expired_yesterday, vehicle)
    allowed = assign(client, route, expires_today, vehicle)

    assert (blocked.status_code, code(blocked)) == (409, "assignment.driver_unavailable")
    assert reasons(blocked) == ["license_expired"]
    assert allowed.status_code == 200


def test_licence_that_does_not_cover_the_vehicle_category_is_409(
    client: APIClient, branch: Branch, route: Route
) -> None:
    driver = DriverFactory.create(company=branch.company, branch=branch)
    DriverDocumentFactory.create(driver=driver, categories=[LicenseCategory.B])
    vehicle = make_vehicle(branch, required_license_category=LicenseCategory.C)

    response = assign(client, route, driver, vehicle)

    assert (response.status_code, code(response)) == (409, "assignment.license_category")


# --- vehicle rules ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [VehicleStatus.IN_MAINTENANCE, VehicleStatus.OUT_OF_SERVICE])
def test_vehicle_in_maintenance_or_out_of_service_is_409(
    client: APIClient, branch: Branch, route: Route, driver: Driver, status: VehicleStatus
) -> None:
    vehicle = make_vehicle(branch, status=status, status_reason="Brakes")

    response = assign(client, route, driver, vehicle)

    assert (response.status_code, code(response)) == (409, "assignment.vehicle_unavailable")
    assert reasons(response) == [status.value]


@pytest.mark.parametrize(
    ("document_type", "expected"),
    [
        (VehicleDocumentType.REGISTRATION, "registration_expired"),
        (VehicleDocumentType.INSURANCE, "insurance_expired"),
    ],
)
def test_vehicle_with_an_expired_document_is_409(
    client: APIClient,
    branch: Branch,
    route: Route,
    driver: Driver,
    document_type: VehicleDocumentType,
    expected: str,
) -> None:
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)
    for kind in (VehicleDocumentType.REGISTRATION, VehicleDocumentType.INSURANCE):
        VehicleDocumentFactory.create(
            vehicle=vehicle,
            type=kind,
            expires_on=ROUTE_DATE - timedelta(days=1)
            if kind == document_type
            else date(2099, 12, 31),
        )

    response = assign(client, route, driver, vehicle)

    assert (response.status_code, code(response)) == (409, "assignment.vehicle_unavailable")
    assert reasons(response) == [expected]


# --- conflicts -------------------------------------------------------------------------------


def test_driver_on_another_active_route_that_date_is_409(
    client: APIClient, branch: Branch, route: Route, driver: Driver, vehicle: Vehicle
) -> None:
    assigned_route(branch, driver, make_vehicle(branch))

    response = assign(client, route, driver, vehicle)

    assert (response.status_code, code(response)) == (409, "assignment.driver_conflict")


def test_vehicle_on_another_active_route_that_date_is_409(
    client: APIClient, branch: Branch, route: Route, driver: Driver, vehicle: Vehicle
) -> None:
    assigned_route(branch, make_driver(branch), vehicle)

    response = assign(client, route, driver, vehicle)

    assert (response.status_code, code(response)) == (409, "assignment.vehicle_conflict")


@pytest.mark.parametrize(
    ("status", "other_date"),
    [
        (RouteStatus.COMPLETED, ROUTE_DATE),
        (RouteStatus.CANCELLED, ROUTE_DATE),
        (RouteStatus.ASSIGNED, ROUTE_DATE + timedelta(days=1)),
    ],
)
def test_routes_that_are_finished_or_on_another_date_do_not_conflict(
    client: APIClient,
    branch: Branch,
    route: Route,
    driver: Driver,
    vehicle: Vehicle,
    status: RouteStatus,
    other_date: date,
) -> None:
    fields: dict[str, Any] = {"route_date": other_date, "status": status}
    if status == RouteStatus.CANCELLED:
        fields["cancelled_at"] = "2099-01-01T00:00:00Z"
    RouteFactory.create(branch=branch, driver=driver, vehicle=vehicle, **fields)

    assert assign(client, route, driver, vehicle).status_code == 200


# --- capacity --------------------------------------------------------------------------------


def load(route: Route, count: int = 1) -> None:
    """`count` shipments on the route: 2.5 kg and 0.024 m³ each."""
    for _ in range(count):
        RouteStopFactory.create(route=route)


def test_load_that_fits_is_allowed(
    client: APIClient, branch: Branch, route: Route, driver: Driver
) -> None:
    load(route, 2)  # 5 kg, 0.048 m³
    vehicle = make_vehicle(
        branch, capacity_weight_kg=Decimal("5"), capacity_volume_m3=Decimal("0.048")
    )

    assert assign(client, route, driver, vehicle).status_code == 200


@pytest.mark.parametrize(
    "capacity",
    [
        {"capacity_weight_kg": Decimal("2.4"), "capacity_volume_m3": Decimal(20)},
        {"capacity_weight_kg": Decimal(3500), "capacity_volume_m3": Decimal("0.023")},
    ],
    ids=["weight", "volume"],
)
def test_over_capacity_without_a_reason_is_409(
    client: APIClient, branch: Branch, route: Route, driver: Driver, capacity: dict[str, Decimal]
) -> None:
    load(route)
    vehicle = make_vehicle(branch, **capacity)

    response = assign(client, route, driver, vehicle)

    assert (response.status_code, code(response)) == (409, "assignment.over_capacity")
    assert Route.objects.get(pk=route.pk).status == RouteStatus.PLANNED


def test_over_capacity_with_a_reason_and_the_permission_is_allowed_and_audited(
    client: APIClient, branch: Branch, route: Route, driver: Driver
) -> None:
    load(route)
    vehicle = make_vehicle(branch, capacity_weight_kg=Decimal(1))

    response = assign(client, route, driver, vehicle, override_reason="Customer collects the rest")

    assert response.status_code == 200
    assert response.json()["capacity_override_reason"] == "Customer collects the rest"
    entry = AuditLog.objects.get(action=AuditAction.ROUTE_ASSIGNED)
    assert entry.after is not None
    assert entry.after["capacity_override"] == "true"
    assert "Customer collects" not in str(entry.after)


def test_over_capacity_with_a_reason_but_without_the_permission_is_403(
    client: APIClient, branch: Branch, route: Route, driver: Driver
) -> None:
    Permission.objects.get(codename="dispatch.override_capacity").roles.remove(
        Role.objects.get(code=RoleCode.DISPATCHER)
    )
    load(route)
    vehicle = make_vehicle(branch, capacity_weight_kg=Decimal(1))

    response = assign(client, route, driver, vehicle, override_reason="Please")

    assert (response.status_code, code(response)) == (403, "permission.denied")
    assert Route.objects.get(pk=route.pk).status == RouteStatus.PLANNED


def test_a_reason_is_not_kept_when_the_load_fits(
    client: APIClient, route: Route, driver: Driver, vehicle: Vehicle
) -> None:
    response = assign(client, route, driver, vehicle, override_reason="Not needed")

    assert response.status_code == 200
    assert response.json()["capacity_override_reason"] == ""


def test_a_shipment_with_two_stops_counts_once(
    client: APIClient, branch: Branch, route: Route, driver: Driver
) -> None:
    stop = RouteStopFactory.create(route=route)
    RouteStopFactory.create(route=route, shipment=stop.shipment, type="pickup", sequence=99)
    vehicle = make_vehicle(
        branch, capacity_weight_kg=Decimal("2.5"), capacity_volume_m3=Decimal("0.024")
    )

    assert assign(client, route, driver, vehicle).status_code == 200


def test_adding_a_stop_over_capacity_to_an_assigned_route_is_409_and_rolls_back(
    client: APIClient, branch: Branch, route: Route, driver: Driver
) -> None:
    load(route)
    vehicle = make_vehicle(branch, capacity_weight_kg=Decimal("3"))
    assert assign(client, route, driver, vehicle).status_code == 200
    shipment = make_shipment(branch, ShipmentStatus.SORTED)

    response = add_stop(client, route, shipment)

    assert (response.status_code, code(response)) == (409, "assignment.over_capacity")
    shipment.refresh_from_db()
    assert shipment.status == ShipmentStatus.SORTED
    assert Route.objects.get(pk=route.pk).stops.count() == 1


def test_adding_a_stop_over_capacity_to_a_planned_route_is_allowed(
    client: APIClient, branch: Branch, route: Route
) -> None:
    load(route)

    assert add_stop(client, route, make_shipment(branch, ShipmentStatus.SORTED)).status_code == 201


# --- scope -----------------------------------------------------------------------------------


def test_driver_or_vehicle_of_a_branch_the_caller_cannot_see_is_400_invalid_reference(
    client: APIClient, other_branch: Branch, route: Route, driver: Driver, vehicle: Vehicle
) -> None:
    foreign_driver, foreign_vehicle = make_driver(other_branch), make_vehicle(other_branch)

    for response in (
        assign(client, route, foreign_driver, vehicle),
        assign(client, route, driver, foreign_vehicle),
    ):
        assert (response.status_code, code(response)) == (400, "validation.invalid_reference")


def test_an_all_branch_user_still_needs_the_route_branch_for_driver_and_vehicle(
    company: Company, branch: Branch, other_branch: Branch, route: Route
) -> None:
    admin = client_for(make_user(company, RoleCode.ADMIN))
    home_driver, home_vehicle = make_driver(branch), make_vehicle(branch)
    foreign_driver, foreign_vehicle = make_driver(other_branch), make_vehicle(other_branch)

    for response in (
        assign(admin, route, foreign_driver, home_vehicle),
        assign(admin, route, home_driver, foreign_vehicle),
    ):
        assert (response.status_code, code(response)) == (400, "validation.invalid")


def test_driver_or_vehicle_of_another_company_or_unknown_is_400(
    client: APIClient, route: Route, driver: Driver, vehicle: Vehicle
) -> None:
    foreign = Route.objects.get(pk=RouteFactory.create().pk)  # another company's branch
    foreign_driver = make_driver(foreign.branch)
    foreign_vehicle = make_vehicle(foreign.branch)

    for response in (
        assign(client, route, foreign_driver, vehicle),
        assign(client, route, driver, foreign_vehicle),
    ):
        assert (response.status_code, code(response)) == (400, "validation.invalid_reference")


def test_route_of_another_branch_is_404(
    client: APIClient, other_branch: Branch, driver: Driver, vehicle: Vehicle
) -> None:
    route = RouteFactory.create(branch=other_branch)

    assert assign(client, route, driver, vehicle).status_code == 404
    assert unassign(client, route, 1).status_code == 404


def test_all_branch_users_can_assign_across_branches(
    company: Company, other_branch: Branch
) -> None:
    admin = client_for(make_user(company, RoleCode.ADMIN))
    route = RouteFactory.create(branch=other_branch)

    response = assign(admin, route, make_driver(other_branch), make_vehicle(other_branch))

    assert response.status_code == 200


# --- unassign --------------------------------------------------------------------------------


def test_unassign_returns_the_route_to_planned_and_keeps_stops_and_shipment_status(
    client: APIClient, route: Route, driver: Driver, vehicle: Vehicle
) -> None:
    stop = RouteStopFactory.create(route=route)
    assign(client, route, driver, vehicle)

    response = unassign(client, route)

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["driver"], body["vehicle"], body["version"]) == (
        "planned",
        None,
        None,
        3,
    )
    assert len(body["stops"]) == 1
    stop.shipment.refresh_from_db()
    assert stop.shipment.status == ShipmentStatus.ASSIGNED
    assert AuditLog.objects.filter(action=AuditAction.ROUTE_UNASSIGNED).count() == 1


def test_unassign_clears_a_capacity_override(
    client: APIClient, branch: Branch, route: Route, driver: Driver
) -> None:
    load(route)
    assign(
        client,
        route,
        driver,
        make_vehicle(branch, capacity_weight_kg=Decimal(1)),
        override_reason="Ok",
    )

    assert unassign(client, route).json()["capacity_override_reason"] == ""


def test_unassign_on_a_planned_route_is_a_no_op(client: APIClient, route: Route) -> None:
    response = unassign(client, route, version=99)

    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert not AuditLog.objects.filter(action=AuditAction.ROUTE_UNASSIGNED).exists()


def test_unassign_with_a_stale_version_is_409(
    client: APIClient, route: Route, driver: Driver, vehicle: Vehicle
) -> None:
    assign(client, route, driver, vehicle)

    response = unassign(client, route, version=1)

    assert (response.status_code, code(response)) == (409, "route.version_conflict")


def test_unassign_after_dispatch_is_409(
    client: APIClient, branch: Branch, driver: Driver, vehicle: Vehicle
) -> None:
    route = assigned_route(branch, driver, vehicle, status=RouteStatus.DISPATCHED)

    response = unassign(client, route)

    assert (response.status_code, code(response)) == (409, "route.invalid_status")


def test_a_cancelled_route_keeps_its_driver_and_frees_them(
    client: APIClient, branch: Branch, route: Route, driver: Driver, vehicle: Vehicle
) -> None:
    assign(client, route, driver, vehicle)
    assert post(client, route_url(route, "cancel/"), {"version": 2}).status_code == 200

    second = RouteFactory.create(branch=branch)

    assert assign(client, second, driver, vehicle).status_code == 200


# --- validation, access, reads ---------------------------------------------------------------


def test_missing_or_malformed_fields_are_400(client: APIClient, route: Route) -> None:
    for body in ({}, {"version": 1}, {"version": 1, "driver_id": "x", "vehicle_id": "y"}):
        assert post(client, route_url(route, "assign/"), body).status_code == 400
    assert post(client, route_url(route, "unassign/"), {}).status_code == 400


@pytest.mark.parametrize("role", list(RoleCode))
def test_role_matrix(role: RoleCode, company: Company, branch: Branch) -> None:
    client = client_for(make_user(company, role, branch))
    route = RouteFactory.create(branch=branch)
    driver, vehicle = make_driver(branch), make_vehicle(branch)

    assigned = assign(client, route, driver, vehicle)
    unassigned = unassign(client, route)

    can_write = role in {RoleCode.OWNER, RoleCode.ADMIN, RoleCode.OPS_MANAGER, RoleCode.DISPATCHER}
    assert assigned.status_code == (200 if can_write else 403)
    assert unassigned.status_code == (200 if can_write else 403)


def test_unauthenticated_is_401(route: Route) -> None:
    anonymous = APIClient()

    assert anonymous.post(route_url(route, "assign/"), {}, format="json").status_code == 401
    assert anonymous.post(route_url(route, "unassign/"), {}, format="json").status_code == 401


def test_idempotency_key_replays_the_first_response(
    client: APIClient, route: Route, driver: Driver, vehicle: Vehicle
) -> None:
    body = {
        "version": 1,
        "driver_id": str(driver.public_id),
        "vehicle_id": str(vehicle.public_id),
    }

    first = post(client, route_url(route, "assign/"), body, **{"Idempotency-Key": "k-1"})
    second = post(client, route_url(route, "assign/"), body, **{"Idempotency-Key": "k-1"})

    assert (first.status_code, second.status_code) == (200, 200)
    assert second["Idempotent-Replayed"] == "true"
    assert AuditLog.objects.filter(action=AuditAction.ROUTE_ASSIGNED).count() == 1


def test_route_list_query_count_does_not_grow_with_assigned_routes(
    client: APIClient, branch: Branch
) -> None:
    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            assert client.get(ROUTES_URL).status_code == 200
        return len(queries)

    assigned_route(branch, make_driver(branch), make_vehicle(branch))
    baseline = count()
    for _ in range(3):
        assigned_route(branch, make_driver(branch), make_vehicle(branch))

    assert count() == baseline
