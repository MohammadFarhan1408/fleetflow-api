"""Dispatch, in-progress reassignment and the driver's own routes (architecture §13, §26.4)."""

import threading
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import RoleCode, User
from apps.accounts.tests.conftest import client_for
from apps.audit.models import AuditAction, AuditLog
from apps.dispatch.factories import RouteFactory, RouteStopFactory
from apps.dispatch.models import ManifestItem, Route, RouteStatus, StopStatus
from apps.dispatch.tests.conftest import code, make_user, post, route_url
from apps.dispatch.tests.test_assignment_api import assigned_route, make_driver, make_vehicle
from apps.fleet.factories import DriverDocumentFactory, DriverFactory, ShiftFactory
from apps.fleet.models import Driver, DriverStatus, Vehicle, VehicleStatus
from apps.shipments.models import Shipment, ShipmentStatus, TrackingEvent
from apps.tenancy.models import Branch, Company

pytestmark = pytest.mark.django_db

ME_ROUTES = "/api/v1/me/routes/"
S = ShipmentStatus


def reasons(response: Any) -> list[str]:
    return list(response.json()["errors"]["reasons"])


def version_of(route: Route) -> int:
    return Route.objects.get(pk=route.pk).version


def dispatch(client: APIClient, route: Route, version: int | None = None) -> Any:
    v = version if version is not None else version_of(route)
    return post(client, route_url(route, "dispatch/"), {"version": v})


def reassign(
    client: APIClient, route: Route, driver: Driver, vehicle: Vehicle, version: int | None = None
) -> Any:
    body = {
        "version": version if version is not None else version_of(route),
        "driver_id": str(driver.public_id),
        "vehicle_id": str(vehicle.public_id),
    }
    return post(client, route_url(route, "reassign/"), body)


def start(client: APIClient, route: Route, key: str = "start-1") -> Any:
    return post(client, f"{ME_ROUTES}{route.public_id}/start/", {}, **{"Idempotency-Key": key})


@pytest.fixture
def today(company: Company) -> Any:
    return company.local_today()


@pytest.fixture
def ready(branch: Branch, today: Any) -> Route:
    """An assigned route for today with one delivery stop (a sorted shipment)."""
    route = assigned_route(branch, make_driver(branch), make_vehicle(branch), route_date=today)
    RouteStopFactory.create(route=route)
    return route


def driver_client(route: Route, *, open_shift: bool = True) -> APIClient:
    assert route.driver is not None
    if open_shift:
        ShiftFactory.create(driver=route.driver)
    return client_for(route.driver.user)


# --- dispatch --------------------------------------------------------------------------------


def test_dispatch_sends_the_route_out_and_records_the_manifest(
    client: APIClient, ready: Route
) -> None:
    stop = ready.stops.get()
    RouteStopFactory.create(route=ready, shipment=stop.shipment, type="pickup", sequence=9)
    RouteStopFactory.create(route=ready)

    response = dispatch(client, ready)

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["version"]) == ("dispatched", 2)
    assert body["dispatched_at"] is not None and body["started_at"] is None
    assert ManifestItem.objects.filter(route=ready).count() == 2  # two shipments, three stops
    assert AuditLog.objects.filter(action=AuditAction.ROUTE_DISPATCHED).count() == 1
    # shipments change status when the driver starts, not now
    assert set(Shipment.objects.values_list("status", flat=True)) == {S.ASSIGNED}


def test_dispatching_an_empty_route_is_409(client: APIClient, branch: Branch, today: Any) -> None:
    route = assigned_route(branch, make_driver(branch), make_vehicle(branch), route_date=today)

    response = dispatch(client, route)

    assert (response.status_code, code(response)) == (409, "route.empty")


def test_only_an_assigned_route_can_be_dispatched(
    client: APIClient, branch: Branch, today: Any
) -> None:
    planned = RouteFactory.create(branch=branch, route_date=today)
    RouteStopFactory.create(route=planned)

    response = dispatch(client, planned)

    assert (response.status_code, code(response)) == (409, "route.invalid_status")


def test_repeating_dispatch_is_a_no_op_even_with_a_stale_version(
    client: APIClient, ready: Route
) -> None:
    dispatch(client, ready)

    again = dispatch(client, ready, version=1)

    assert again.status_code == 200
    assert again.json()["version"] == 2
    assert ManifestItem.objects.filter(route=ready).count() == 1
    assert AuditLog.objects.filter(action=AuditAction.ROUTE_DISPATCHED).count() == 1


def test_dispatch_with_a_stale_version_is_409(client: APIClient, ready: Route) -> None:
    response = dispatch(client, ready, version=9)

    assert (response.status_code, code(response)) == (409, "route.version_conflict")


def test_a_route_in_the_past_cannot_be_dispatched(
    client: APIClient, branch: Branch, today: Any
) -> None:
    route = assigned_route(
        branch, make_driver(branch), make_vehicle(branch), route_date=today - timedelta(days=1)
    )
    RouteStopFactory.create(route=route)

    assert dispatch(client, route).status_code == 400


def test_dispatch_checks_the_driver_again(client: APIClient, ready: Route) -> None:
    assert ready.driver is not None
    ready.driver.status = DriverStatus.SUSPENDED
    ready.driver.status_reason = "Hearing"
    ready.driver.save()

    response = dispatch(client, ready)

    assert (response.status_code, code(response)) == (409, "assignment.driver_unavailable")
    assert reasons(response) == ["suspended"]
    assert Route.objects.get(pk=ready.pk).status == RouteStatus.ASSIGNED
    assert not ManifestItem.objects.exists()


def test_dispatch_checks_the_vehicle_again(client: APIClient, ready: Route) -> None:
    assert ready.vehicle is not None
    ready.vehicle.status = VehicleStatus.OUT_OF_SERVICE
    ready.vehicle.status_reason = "Accident"
    ready.vehicle.save()

    response = dispatch(client, ready)

    assert (response.status_code, code(response)) == (409, "assignment.vehicle_unavailable")


def test_dispatch_checks_the_licence_again(client: APIClient, ready: Route) -> None:
    assert ready.driver is not None
    ready.driver.documents.update(archived_at=timezone.now())

    response = dispatch(client, ready)

    assert (response.status_code, code(response)) == (409, "assignment.driver_unavailable")
    assert reasons(response) == ["license_missing"]


def test_dispatch_checks_capacity_again_and_honours_a_stored_override(
    client: APIClient, ready: Route
) -> None:
    assert ready.vehicle is not None
    ready.vehicle.capacity_weight_kg = Decimal(1)  # the 2.5 kg load no longer fits
    ready.vehicle.save()

    blocked = dispatch(client, ready)
    Route.objects.filter(pk=ready.pk).update(capacity_override_reason="Agreed with the customer")
    allowed = dispatch(client, ready)

    assert (blocked.status_code, code(blocked)) == (409, "assignment.over_capacity")
    assert allowed.status_code == 200


def test_a_dispatched_route_cannot_be_unassigned_or_have_stops_changed(
    client: APIClient, ready: Route
) -> None:
    dispatch(client, ready)

    unassigned = post(client, route_url(ready, "unassign/"), {"version": 2})

    assert (unassigned.status_code, code(unassigned)) == (409, "route.invalid_status")


def test_cancelling_a_dispatched_route_keeps_its_manifest(client: APIClient, ready: Route) -> None:
    dispatch(client, ready)

    cancelled = post(client, route_url(ready, "cancel/"), {"version": 2})

    assert cancelled.status_code == 200
    assert ManifestItem.objects.filter(route=ready).count() == 1
    assert Shipment.objects.get().status == S.SORTED


# --- reassign --------------------------------------------------------------------------------


@pytest.fixture
def manager(company: Company) -> APIClient:
    return client_for(make_user(company, RoleCode.OPS_MANAGER))


def test_reassigning_a_dispatched_route_moves_it_to_the_new_pair(
    manager: APIClient, branch: Branch, ready: Route
) -> None:
    old_driver = ready.driver
    assert old_driver is not None
    dispatch(manager, ready)
    new_driver, new_vehicle = make_driver(branch), make_vehicle(branch)

    response = reassign(manager, ready, new_driver, new_vehicle)

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["version"]) == ("dispatched", 3)
    assert body["driver"]["public_id"] == str(new_driver.public_id)
    entry = AuditLog.objects.get(action=AuditAction.ROUTE_REASSIGNED)
    assert entry.after is not None
    assert entry.after["previous_driver"] == str(old_driver.public_id)
    # the old driver is free again
    other = assigned_route(branch, old_driver, make_vehicle(branch), route_date=ready.route_date)
    assert other.driver == old_driver


def test_reassigning_an_in_progress_route_sends_it_back_to_dispatched(
    manager: APIClient, branch: Branch, ready: Route
) -> None:
    dispatch(manager, ready)
    assert start(driver_client(ready), ready).status_code == 200

    response = reassign(manager, ready, make_driver(branch), make_vehicle(branch))

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["started_at"]) == ("dispatched", None)
    # shipments already out for delivery stay so
    assert Shipment.objects.get().status == S.OUT_FOR_DELIVERY


def test_reassigning_the_same_pair_is_a_no_op(manager: APIClient, ready: Route) -> None:
    assert ready.driver is not None and ready.vehicle is not None
    dispatch(manager, ready)

    again = reassign(manager, ready, ready.driver, ready.vehicle, version=1)

    assert again.status_code == 200
    assert again.json()["version"] == 2
    assert not AuditLog.objects.filter(action=AuditAction.ROUTE_REASSIGNED).exists()


def test_reassign_needs_the_reassign_active_permission(
    client: APIClient, branch: Branch, ready: Route
) -> None:  # `client` is a dispatcher: dispatch.manage but not dispatch.reassign_active
    dispatch(client, ready)

    response = reassign(client, ready, make_driver(branch), make_vehicle(branch))

    assert (response.status_code, code(response)) == (403, "permission.denied")


def test_reassign_refuses_a_route_that_has_worked_stops(
    manager: APIClient, branch: Branch, ready: Route
) -> None:
    dispatch(manager, ready)
    RouteStopFactory.create(route=ready, status=StopStatus.COMPLETED, sequence=9)

    response = reassign(manager, ready, make_driver(branch), make_vehicle(branch))

    assert (response.status_code, code(response)) == (409, "route.stops_started")


@pytest.mark.parametrize("status", [RouteStatus.PLANNED, RouteStatus.ASSIGNED])
def test_reassign_is_only_for_dispatched_or_in_progress_routes(
    manager: APIClient, branch: Branch, status: RouteStatus
) -> None:
    route = (
        RouteFactory.create(branch=branch)
        if status == RouteStatus.PLANNED
        else assigned_route(branch, make_driver(branch), make_vehicle(branch))
    )

    response = reassign(manager, route, make_driver(branch), make_vehicle(branch))

    assert (response.status_code, code(response)) == (409, "route.invalid_status")


def test_reassign_checks_the_new_pair_and_capacity_has_no_override(
    manager: APIClient, branch: Branch, ready: Route
) -> None:
    dispatch(manager, ready)
    suspended = make_driver(branch, status=DriverStatus.SUSPENDED, status_reason="Hearing")
    small = make_vehicle(branch, capacity_weight_kg=Decimal(1))

    unavailable = reassign(manager, ready, suspended, make_vehicle(branch))
    too_small = reassign(manager, ready, make_driver(branch), small)

    assert (unavailable.status_code, code(unavailable)) == (409, "assignment.driver_unavailable")
    assert (too_small.status_code, code(too_small)) == (409, "assignment.over_capacity")
    assert Route.objects.get(pk=ready.pk).driver == ready.driver


def test_reassign_to_a_driver_on_another_active_route_is_409(
    manager: APIClient, branch: Branch, ready: Route
) -> None:
    dispatch(manager, ready)
    busy = make_driver(branch)
    assigned_route(branch, busy, make_vehicle(branch), route_date=ready.route_date)

    response = reassign(manager, ready, busy, make_vehicle(branch))

    assert (response.status_code, code(response)) == (409, "assignment.driver_conflict")


def test_reassign_with_a_stale_version_is_409(
    manager: APIClient, branch: Branch, ready: Route
) -> None:
    dispatch(manager, ready)

    response = reassign(manager, ready, make_driver(branch), make_vehicle(branch), version=1)

    assert (response.status_code, code(response)) == (409, "route.version_conflict")


# --- the driver: start -----------------------------------------------------------------------


def test_start_moves_the_route_and_the_warehouse_shipments(client: APIClient, ready: Route) -> None:
    pickup_and_deliver = RouteStopFactory.create(
        route=ready,
        shipment__status=S.ASSIGNED,
        shipment_status_before=S.CREATED,
        type="pickup",
        sequence=5,
    )
    dispatch(client, ready)
    driver = driver_client(ready)

    response = start(driver, ready)

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["version"]) == ("in_progress", 3)
    assert body["started_at"] is not None
    warehouse_shipment = ready.stops.get(shipment_status_before=S.SORTED).shipment
    warehouse_shipment.refresh_from_db()
    assert warehouse_shipment.status == S.OUT_FOR_DELIVERY
    event = TrackingEvent.objects.filter(shipment=warehouse_shipment).latest("recorded_at")
    assert (event.status_after, event.source, event.reason) == (
        "out_for_delivery",
        "driver_app",
        "Route started",
    )
    assert event.actor is not None and event.actor.pk == ready.driver.user.pk  # type: ignore[union-attr]
    # still to be picked up: stays assigned until the delivery workflow (Phase 5)
    pickup_and_deliver.shipment.refresh_from_db()
    assert pickup_and_deliver.shipment.status == S.ASSIGNED


def test_repeating_start_is_a_no_op(client: APIClient, ready: Route) -> None:
    dispatch(client, ready)
    driver = driver_client(ready)
    start(driver, ready)

    again = start(driver, ready, key="start-2")

    assert again.status_code == 200
    assert again.json()["version"] == 3
    assert TrackingEvent.objects.filter(status_after="out_for_delivery").count() == 1


def test_start_replays_the_same_idempotency_key(client: APIClient, ready: Route) -> None:
    dispatch(client, ready)
    driver = driver_client(ready)

    first, second = start(driver, ready), start(driver, ready)

    assert (first.status_code, second.status_code) == (200, 200)
    assert second["Idempotent-Replayed"] == "true"


def test_start_needs_an_idempotency_key(client: APIClient, ready: Route) -> None:
    dispatch(client, ready)

    response = post(driver_client(ready), f"{ME_ROUTES}{ready.public_id}/start/")

    assert (response.status_code, code(response)) == (400, "idempotency.key_required")


def test_start_before_dispatch_is_409(ready: Route) -> None:
    response = start(driver_client(ready), ready)

    assert (response.status_code, code(response)) == (409, "route.not_dispatched")


def test_start_without_an_open_shift_is_409(client: APIClient, ready: Route) -> None:
    dispatch(client, ready)

    response = start(driver_client(ready, open_shift=False), ready)

    assert (response.status_code, code(response)) == (409, "shift.not_open")
    assert Route.objects.get(pk=ready.pk).status == RouteStatus.DISPATCHED


def test_start_before_the_route_date_is_409(client: APIClient, branch: Branch, today: Any) -> None:
    route = assigned_route(
        branch,
        make_driver(branch),
        make_vehicle(branch),
        route_date=today + timedelta(days=1),
    )
    RouteStopFactory.create(route=route)
    dispatch(client, route)

    response = start(driver_client(route), route)

    assert (response.status_code, code(response)) == (409, "route.not_due")


def test_a_driver_cannot_start_another_drivers_route(
    client: APIClient, branch: Branch, ready: Route
) -> None:
    dispatch(client, ready)
    colleague = make_driver(branch)
    ShiftFactory.create(driver=colleague)

    response = start(client_for(colleague.user), ready)

    assert response.status_code == 404
    assert Route.objects.get(pk=ready.pk).status == RouteStatus.DISPATCHED


def test_a_driver_without_an_active_profile_is_403(client: APIClient, ready: Route) -> None:
    dispatch(client, ready)
    driver = driver_client(ready)
    assert ready.driver is not None
    ready.driver.archived_at = timezone.now()
    ready.driver.save()

    for response in (driver.get(ME_ROUTES), start(driver, ready)):
        assert (response.status_code, code(response)) == (403, "driver.profile_inactive")


# --- the driver: reads -----------------------------------------------------------------------


def test_the_driver_sees_only_their_own_dispatched_routes(branch: Branch, today: Any) -> None:
    mine = make_driver(branch)
    visible = [
        assigned_route(
            branch, mine, make_vehicle(branch), route_date=today + timedelta(days=i), status=status
        )
        for i, status in enumerate([RouteStatus.DISPATCHED, RouteStatus.IN_PROGRESS])
    ]
    assigned_route(branch, mine, make_vehicle(branch), route_date=today + timedelta(days=5))
    RouteFactory.create(branch=branch, route_date=today)  # planned
    assigned_route(
        branch,
        make_driver(branch),
        make_vehicle(branch),
        route_date=today,
        status=RouteStatus.DISPATCHED,
    )  # a colleague's
    client = client_for(mine.user)

    response = client.get(ME_ROUTES)

    assert response.status_code == 200
    assert {r["public_id"] for r in response.json()["results"]} == {
        str(route.public_id) for route in visible
    }
    assert client.get(ME_ROUTES, {"status": "in_progress"}).json()["results"][0][
        "public_id"
    ] == str(visible[1].public_id)
    assert client.get(ME_ROUTES, {"status": "assigned"}).status_code == 400


def test_the_driver_detail_has_stops_but_no_assignment_internals(
    client: APIClient, ready: Route
) -> None:
    dispatch(client, ready)

    response = client_for(ready.driver.user).get(f"{ME_ROUTES}{ready.public_id}/")  # type: ignore[union-attr]

    assert response.status_code == 200
    body = response.json()
    assert len(body["stops"]) == 1
    assert body["stops"][0]["address"]["city"]
    assert body["vehicle"]["registration_number"]
    assert not {"driver", "capacity_override_reason", "cancelled_at"} & body.keys()


def test_an_undispatched_or_foreign_route_detail_is_404(branch: Branch, ready: Route) -> None:
    mine = make_driver(branch)
    ShiftFactory.create(driver=mine)

    assert client_for(mine.user).get(f"{ME_ROUTES}{ready.public_id}/").status_code == 404
    assert client_for(ready.driver.user).get(f"{ME_ROUTES}{ready.public_id}/").status_code == 404  # type: ignore[union-attr]


def test_driver_route_reads_do_not_grow_with_routes(branch: Branch, today: Any) -> None:
    mine = make_driver(branch)
    client = client_for(mine.user)

    def build(offset: int) -> Route:
        route = assigned_route(
            branch,
            mine,
            make_vehicle(branch),
            route_date=today + timedelta(days=offset),
            status=RouteStatus.DISPATCHED,
        )
        RouteStopFactory.create(route=route)
        return route

    first = build(0)

    def count(url: str) -> int:
        with CaptureQueriesContext(connection) as queries:
            assert client.get(url).status_code == 200
        return len(queries)

    list_before, detail_before = count(ME_ROUTES), count(f"{ME_ROUTES}{first.public_id}/")
    for offset in (1, 2, 3):
        route = build(offset)
        for _ in range(2):
            RouteStopFactory.create(route=route)

    assert count(ME_ROUTES) == list_before
    assert count(f"{ME_ROUTES}{route.public_id}/") == detail_before


# --- access ----------------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(RoleCode))
def test_role_matrix_for_dispatch_and_reassign(
    role: RoleCode, company: Company, branch: Branch, today: Any
) -> None:
    client = client_for(make_user(company, role, branch))
    route = assigned_route(branch, make_driver(branch), make_vehicle(branch), route_date=today)
    RouteStopFactory.create(route=route)

    dispatched = dispatch(client, route)
    reassigned = reassign(client, route, make_driver(branch), make_vehicle(branch), version=2)

    can_manage = role in {RoleCode.OWNER, RoleCode.ADMIN, RoleCode.OPS_MANAGER, RoleCode.DISPATCHER}
    can_reassign = role in {RoleCode.OWNER, RoleCode.ADMIN, RoleCode.OPS_MANAGER}
    assert dispatched.status_code == (200 if can_manage else 403)
    assert reassigned.status_code == (200 if can_reassign else 403)


@pytest.mark.parametrize("role", [r for r in RoleCode if r != RoleCode.DRIVER])
def test_only_the_driver_role_has_driver_routes(
    role: RoleCode, company: Company, branch: Branch, ready: Route
) -> None:
    client = client_for(make_user(company, role, branch))

    assert client.get(ME_ROUTES).status_code == 403
    assert client.get(f"{ME_ROUTES}{ready.public_id}/").status_code == 403
    assert start(client, ready).status_code == 403


def test_unauthenticated_is_401(ready: Route) -> None:
    anonymous = APIClient()

    assert anonymous.get(ME_ROUTES).status_code == 401
    assert (
        anonymous.post(f"{ME_ROUTES}{ready.public_id}/start/", {}, format="json").status_code == 401
    )
    assert anonymous.post(route_url(ready, "dispatch/"), {}, format="json").status_code == 401
    assert anonymous.post(route_url(ready, "reassign/"), {}, format="json").status_code == 401


# --- races -----------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_concurrent_starts_move_each_shipment_once() -> None:
    from apps.dispatch import services
    from apps.tenancy.factories import BranchFactory, CompanyFactory

    company = CompanyFactory.create()
    branch = BranchFactory.create(company=company)
    route = RouteFactory.create(
        branch=branch, route_date=company.local_today(), status=RouteStatus.DISPATCHED
    )
    DriverDocumentFactory.create(driver=route.driver)
    ShiftFactory.create(driver=route.driver)
    for _ in range(3):
        RouteStopFactory.create(route=route)
    user_id = route.driver.user_id  # type: ignore[union-attr]
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def run() -> None:
        from django.test import RequestFactory

        barrier.wait()
        try:
            services.start_route(
                actor=User.objects.get(pk=user_id),
                public_id=route.public_id,
                request=RequestFactory().post("/"),
            )
            outcomes.append("ok")
        except Exception as exc:  # any failure is the test's answer
            outcomes.append(repr(exc))
        finally:
            connection.close()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes == ["ok", "ok"]  # the loser sees a started route and repeats it
    assert TrackingEvent.objects.filter(status_after="out_for_delivery").count() == 3
    assert Route.objects.get(pk=route.pk).version == 2


def test_driver_factory_users_have_no_branches(branch: Branch) -> None:
    """The start must not depend on the driver's branch membership (memory D-API-32)."""
    driver = DriverFactory.create(company=branch.company, branch=branch)
    DriverDocumentFactory.create(driver=driver)

    assert not driver.user.branches.exists()
