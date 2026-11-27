"""What the API tests cannot reach: scope re-checks in the services, database constraints
and real concurrency."""

import threading
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.core.errors import DomainError
from apps.dispatch import selectors, services
from apps.dispatch.factories import RouteFactory, RouteStopFactory
from apps.dispatch.models import Route, RouteStatus, RouteStop, StopStatus
from apps.dispatch.tests.conftest import make_shipment, make_user
from apps.dispatch.tests.test_assignment_api import make_driver, make_vehicle
from apps.shipments.models import Shipment, ShipmentStatus
from apps.tenancy.factories import BranchFactory, CompanyFactory
from apps.tenancy.models import Branch, Company

pytestmark = pytest.mark.django_db

S = ShipmentStatus


def request() -> Any:
    return RequestFactory().post("/")


# --- scope ---------------------------------------------------------------------------------


def test_services_refuse_routes_outside_the_actors_scope(
    company: Company, branch: Branch, other_branch: Branch
) -> None:
    local = make_user(company, RoleCode.DISPATCHER, branch)
    foreign_company = UserFactory.create(company=CompanyFactory.create(), roles=[RoleCode.OWNER])
    far_route = RouteFactory.create(branch=other_branch)
    stop = RouteStopFactory.create(route=far_route)
    shipment = make_shipment(other_branch, S.SORTED)

    def calls_for(actor: User) -> list[Any]:
        return [
            lambda: services.update_route(
                actor=actor, route=far_route, version=1, route_date=None, sequence_enforced=False
            ),
            lambda: services.cancel_route(
                actor=actor, route=far_route, version=1, request=request()
            ),
            lambda: services.reorder_stops(actor=actor, route=far_route, version=1, stop_ids=[]),
            lambda: services.remove_stop(
                actor=actor, route=far_route, stop=stop, version=1, request=request()
            ),
            lambda: services.add_stop(
                actor=actor,
                route=far_route,
                version=1,
                shipment_id=shipment.public_id,
                type="delivery",
                window_start=None,
                window_end=None,
                request=request(),
            ),
        ]

    for actor in (local, foreign_company):
        for call in calls_for(actor):
            with pytest.raises(DomainError) as error:
                call()
            assert error.value.status_code == 404
    assert Route.objects.get(pk=far_route.pk).version == 1
    assert Shipment.objects.get(pk=shipment.pk).status == S.SORTED


def test_create_route_refuses_a_branch_outside_the_actors_scope(
    company: Company, branch: Branch, other_branch: Branch
) -> None:
    local = make_user(company, RoleCode.DISPATCHER, branch)

    with pytest.raises(DomainError) as error:
        services.create_route(
            actor=local,
            branch_id=other_branch.public_id,
            route_date=date(2099, 1, 1),
            sequence_enforced=True,
            request=request(),
        )

    assert error.value.status_code == 400
    assert not Route.objects.exists()


def test_selectors_scope_routes_and_find_stops(
    company: Company, branch: Branch, other_branch: Branch
) -> None:
    local = make_user(company, RoleCode.DISPATCHER, branch)
    mine, far = RouteFactory.create(branch=branch), RouteFactory.create(branch=other_branch)
    stop = RouteStopFactory.create(route=mine)

    assert list(selectors.routes_in_scope(local)) == [mine]
    assert far not in selectors.routes_in_scope(local)
    assert selectors.get_stop(mine, stop.public_id) == stop


# --- database ------------------------------------------------------------------------------


def test_the_database_requires_a_unique_sequence_per_route(route: Route) -> None:
    first = RouteStopFactory.create(route=route, sequence=1)

    with pytest.raises(IntegrityError), transaction.atomic():
        RouteStopFactory.create(route=route, sequence=first.sequence)
        with connection.cursor() as cursor:  # the constraint is deferred: check it now
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_the_unique_sequence_is_deferred_so_a_swap_in_one_transaction_works(
    route: Route,
) -> None:
    a, b = (
        RouteStopFactory.create(route=route, sequence=1),
        RouteStopFactory.create(route=route, sequence=2),
    )

    with transaction.atomic():
        RouteStop.objects.filter(pk=a.pk).update(sequence=2)
        RouteStop.objects.filter(pk=b.pk).update(sequence=1)

    assert list(RouteStop.objects.order_by("sequence")) == [b, a]


def test_the_database_allows_a_shipment_one_stop_of_each_type_per_route(route: Route) -> None:
    stop = RouteStopFactory.create(route=route)

    with pytest.raises(IntegrityError), transaction.atomic():
        RouteStopFactory.create(route=route, shipment=stop.shipment, type=stop.type, sequence=9)
    RouteStopFactory.create(route=route, shipment=stop.shipment, type="pickup", sequence=9)


@pytest.mark.parametrize(
    "fields",
    [
        {"type": "return"},
        {"status": "lost"},
        {"sequence": 0},
        {"window_start": timezone.now()},  # one end only
        {"window_start": timezone.now(), "window_end": timezone.now() - timedelta(hours=1)},
        {"shipment_status_before": "delivered"},
    ],
)
def test_the_database_rejects_invalid_stops(route: Route, fields: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        RouteStopFactory.create(route=route, **fields)


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "flying"},
        {"status": RouteStatus.CANCELLED},  # without cancelled_at
        {"cancelled_at": timezone.now()},  # planned, yet cancelled_at set
    ],
)
def test_the_database_rejects_invalid_routes(branch: Branch, fields: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        RouteFactory.create(branch=branch, **fields)


def test_stop_statuses_beyond_pending_exist_for_phase_5(route: Route) -> None:
    for status in StopStatus.values:
        RouteStopFactory.create(route=route, status=status)

    assert RouteStop.objects.count() == len(StopStatus.values)


# --- concurrency ---------------------------------------------------------------------------


def run_in_threads(actions: list[Any]) -> list[str]:
    outcomes: list[str] = []
    barrier = threading.Barrier(len(actions))

    def run(action: Any) -> None:
        barrier.wait()
        try:
            action()
            outcomes.append("ok")
        except DomainError as exc:
            outcomes.append(str(exc.error_code))
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(action,)) for action in actions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return sorted(outcomes)


def add(actor_id: int, route_id: int, shipment_id: uuid.UUID, version: int) -> Any:
    def action() -> None:
        actor = User.objects.get(pk=actor_id)
        route = Route.objects.get(pk=route_id)
        services.add_stop(
            actor=actor,
            route=route,
            version=version,
            shipment_id=shipment_id,
            type="delivery",
            window_start=None,
            window_end=None,
            request=request(),
        )

    return action


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_dispatchers_adding_one_shipment_to_two_routes_have_one_winner() -> None:
    company = CompanyFactory.create()
    branch = BranchFactory.create(company=company)
    actor = UserFactory.create(company=company, roles=[RoleCode.OWNER])
    first, second = RouteFactory.create(branch=branch), RouteFactory.create(branch=branch)
    shipment = make_shipment(branch, S.SORTED)

    outcomes = run_in_threads(
        [
            add(actor.pk, first.pk, shipment.public_id, 1),
            add(actor.pk, second.pk, shipment.public_id, 1),
        ]
    )

    assert outcomes == ["ok", "route.shipment_unavailable"]
    assert RouteStop.objects.count() == 1
    assert Shipment.objects.get(pk=shipment.pk).status == S.ASSIGNED


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_edits_from_the_same_version_have_one_winner() -> None:
    company = CompanyFactory.create()
    branch = BranchFactory.create(company=company)
    actor = UserFactory.create(company=company, roles=[RoleCode.OWNER])
    route = RouteFactory.create(branch=branch)
    shipments = [make_shipment(branch, S.SORTED) for _ in range(2)]

    outcomes = run_in_threads(
        [add(actor.pk, route.pk, shipment.public_id, 1) for shipment in shipments]
    )

    assert outcomes == ["ok", "route.version_conflict"]
    assert RouteStop.objects.count() == 1
    assert Route.objects.get(pk=route.pk).version == 2
    assert sorted(Shipment.objects.values_list("status", flat=True)) == ["assigned", "sorted"]


# --- assignment: database backstops and races ----------------------------------------------


@pytest.mark.parametrize("field", ["driver", "vehicle"])
def test_the_database_allows_one_active_route_per_driver_or_vehicle_and_date(
    branch: Branch, field: str
) -> None:
    driver, vehicle = make_driver(branch), make_vehicle(branch)
    RouteFactory.create(branch=branch, driver=driver, vehicle=vehicle, status=RouteStatus.ASSIGNED)
    shared: dict[str, Any] = {"driver": driver} if field == "driver" else {"vehicle": vehicle}
    fresh: dict[str, Any] = (
        {"vehicle": make_vehicle(branch)} if field == "driver" else {"driver": make_driver(branch)}
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        RouteFactory.create(branch=branch, status=RouteStatus.ASSIGNED, **shared, **fresh)


@pytest.mark.parametrize(
    "fields",
    [
        {"driver": "set"},  # a driver without a vehicle
        {"vehicle": "set"},
        {"status": RouteStatus.ASSIGNED, "driver": None, "vehicle": None},  # no driver
        {"capacity_override_reason": "Why"},  # a reason without a vehicle
    ],
)
def test_the_database_rejects_inconsistent_assignments(
    branch: Branch, fields: dict[str, Any]
) -> None:
    resolved = {
        key: make_driver(branch)
        if key == "driver" and value == "set"
        else make_vehicle(branch)
        if key == "vehicle" and value == "set"
        else value
        for key, value in fields.items()
    }

    with pytest.raises(IntegrityError), transaction.atomic():
        RouteFactory.create(branch=branch, **resolved)


def assign_action(actor_id: int, route_id: int, driver_id: uuid.UUID, vehicle_id: uuid.UUID) -> Any:
    def action() -> None:
        services.assign(
            actor=User.objects.get(pk=actor_id),
            route=Route.objects.get(pk=route_id),
            version=1,
            driver_id=driver_id,
            vehicle_id=vehicle_id,
            override_reason="",
            request=request(),
        )

    return action


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_dispatchers_assigning_one_driver_to_two_routes_have_one_winner() -> None:
    company = CompanyFactory.create()
    branch = BranchFactory.create(company=company)
    actor = UserFactory.create(company=company, roles=[RoleCode.OWNER])
    first, second = RouteFactory.create(branch=branch), RouteFactory.create(branch=branch)
    driver = make_driver(branch)
    vehicles = [make_vehicle(branch), make_vehicle(branch)]

    outcomes = run_in_threads(
        [
            assign_action(actor.pk, route.pk, driver.public_id, vehicle.public_id)
            for route, vehicle in zip((first, second), vehicles, strict=True)
        ]
    )

    assert outcomes == ["assignment.driver_conflict", "ok"]
    assert Route.objects.filter(status=RouteStatus.ASSIGNED).count() == 1


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_dispatchers_assigning_one_vehicle_to_two_routes_have_one_winner() -> None:
    company = CompanyFactory.create()
    branch = BranchFactory.create(company=company)
    actor = UserFactory.create(company=company, roles=[RoleCode.OWNER])
    first, second = RouteFactory.create(branch=branch), RouteFactory.create(branch=branch)
    vehicle = make_vehicle(branch)
    drivers = [make_driver(branch), make_driver(branch)]

    outcomes = run_in_threads(
        [
            assign_action(actor.pk, route.pk, driver.public_id, vehicle.public_id)
            for route, driver in zip((first, second), drivers, strict=True)
        ]
    )

    assert outcomes == ["assignment.vehicle_conflict", "ok"]
    assert Route.objects.filter(status=RouteStatus.ASSIGNED).count() == 1


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_assigns_from_the_same_version_have_one_winner() -> None:
    company = CompanyFactory.create()
    branch = BranchFactory.create(company=company)
    actor = UserFactory.create(company=company, roles=[RoleCode.OWNER])
    route = RouteFactory.create(branch=branch)
    pairs = [(make_driver(branch), make_vehicle(branch)) for _ in range(2)]

    outcomes = run_in_threads(
        [
            assign_action(actor.pk, route.pk, driver.public_id, vehicle.public_id)
            for driver, vehicle in pairs
        ]
    )

    assert outcomes == ["ok", "route.version_conflict"]
    assert Route.objects.get(pk=route.pk).version == 2


def test_assign_and_unassign_refuse_routes_outside_the_actors_scope(
    company: Company, branch: Branch, other_branch: Branch
) -> None:
    local = make_user(company, RoleCode.DISPATCHER, branch)
    route = RouteFactory.create(branch=other_branch)
    driver, vehicle = make_driver(other_branch), make_vehicle(other_branch)

    calls: list[Any] = [
        lambda: services.assign(
            actor=local,
            route=route,
            version=1,
            driver_id=driver.public_id,
            vehicle_id=vehicle.public_id,
            override_reason="",
            request=request(),
        ),
        lambda: services.unassign(actor=local, route=route, version=1, request=request()),
    ]
    for call in calls:
        with pytest.raises(DomainError) as error:
            call()
        assert str(error.value.error_code) == "not_found"


def test_remove_stop_refuses_a_stop_that_is_not_on_the_route(
    company: Company, branch: Branch
) -> None:
    actor = make_user(company, RoleCode.DISPATCHER, branch)
    route = RouteFactory.create(branch=branch)
    elsewhere = RouteStopFactory.create(route=RouteFactory.create(branch=branch))

    with pytest.raises(DomainError) as error:
        services.remove_stop(actor=actor, route=route, stop=elsewhere, version=1, request=request())

    assert error.value.status_code == 404
    assert RouteStop.objects.filter(pk=elsewhere.pk).exists()
