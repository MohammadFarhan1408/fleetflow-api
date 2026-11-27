"""The nearest-neighbour order suggestion (memory D-API-33): a read that saves nothing."""

import pytest
from django.contrib.gis.geos import Point
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode
from apps.accounts.tests.conftest import client_for
from apps.dispatch.factories import RouteFactory, RouteStopFactory
from apps.dispatch.models import Route, RouteStatus, RouteStop, StopType
from apps.dispatch.tests.conftest import code, make_shipment, make_user, post, route_url
from apps.fleet.factories import DriverFactory
from apps.shipments.models import Shipment, ShipmentAddress, ShipmentStatus
from apps.tenancy.models import Branch

pytestmark = pytest.mark.django_db

# Points on one parallel (52.5° N), so distance grows with the longitude gap.
A, B, C, D = (13.40, 52.5), (13.41, 52.5), (13.50, 52.5), (13.42, 52.5)


def locate(shipment: Shipment, role: str, lon_lat: tuple[float, float] | None) -> None:
    point = Point(*lon_lat, srid=4326) if lon_lat else None
    ShipmentAddress.objects.filter(shipment=shipment, role=role).update(
        location=point, geocode_confidence="manual" if point else "none"
    )


def stop(
    route: Route,
    sequence: int,
    lon_lat: tuple[float, float] | None,
    shipment: Shipment | None = None,
    stop_type: str = StopType.DELIVERY,
) -> RouteStop:
    shipment = shipment or make_shipment(route.branch, ShipmentStatus.ASSIGNED)
    locate(shipment, stop_type, lon_lat)
    return RouteStopFactory.create(
        route=route,
        shipment=shipment,
        type=stop_type,
        sequence=sequence,
        shipment_status_before=ShipmentStatus.CREATED if stop_type == "pickup" else "sorted",
    )


def suggest(client: APIClient, route: Route) -> object:
    return client.get(route_url(route, "suggest-order/"))


def ids(*stops: RouteStop) -> list[str]:
    return [str(s.public_id) for s in stops]


def test_stops_are_ordered_by_proximity_from_the_first_stop(
    client: APIClient, route: Route
) -> None:
    a, c, b, d = stop(route, 1, A), stop(route, 2, C), stop(route, 3, B), stop(route, 4, D)

    response = suggest(client, route)

    assert response.status_code == 200, response.content  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["stop_ids"] == ids(a, b, d, c)
    assert body["optimization"] == "nearest_neighbour"
    assert body["version"] == route.version


def test_nothing_is_saved_and_the_suggestion_applies_through_reorder(
    client: APIClient, route: Route
) -> None:
    a, c, b = stop(route, 1, A), stop(route, 2, C), stop(route, 3, B)
    before = list(
        RouteStop.objects.filter(route=route).order_by("sequence").values_list("pk", "sequence")
    )

    body = suggest(client, route).json()  # type: ignore[attr-defined]

    assert (
        list(
            RouteStop.objects.filter(route=route).order_by("sequence").values_list("pk", "sequence")
        )
        == before
    )
    assert Route.objects.get(pk=route.pk).version == body["version"]
    applied = post(
        client,
        route_url(route, "reorder/"),
        {"version": body["version"], "stop_ids": body["stop_ids"]},
    )
    assert applied.status_code == 200, applied.content
    assert [s["public_id"] for s in applied.json()["stops"]] == ids(a, b, c)


def test_repeated_calls_give_the_same_answer(client: APIClient, route: Route) -> None:
    for sequence, point in enumerate([A, C, B, D], start=1):
        stop(route, sequence, point)

    assert suggest(client, route).json() == suggest(client, route).json()  # type: ignore[attr-defined]


def test_a_pickup_comes_before_its_nearer_delivery(client: APIClient, route: Route) -> None:
    shipment = make_shipment(route.branch, ShipmentStatus.ASSIGNED)
    start = stop(route, 1, A)
    pickup = stop(route, 2, C, shipment, "pickup")
    delivery = stop(route, 3, B, shipment, "delivery")  # nearer to the start than its pickup
    near = stop(route, 4, D)

    body = suggest(client, route).json()  # type: ignore[attr-defined]

    assert body["stop_ids"] == ids(start, near, pickup, delivery)


def test_a_delivery_whose_pickup_comes_later_is_not_the_start(
    client: APIClient, route: Route
) -> None:
    shipment = make_shipment(route.branch, ShipmentStatus.ASSIGNED)
    pickup = stop(route, 1, C, shipment, "pickup")
    delivery = stop(route, 2, A, shipment, "delivery")
    # manual order is already valid; a start at the delivery would break it
    assert suggest(client, route).json()["stop_ids"] == ids(pickup, delivery)  # type: ignore[attr-defined]


def test_ungeocoded_stops_come_last_in_manual_order(client: APIClient, route: Route) -> None:
    bare1, c, bare2, a = (
        stop(route, 1, None),
        stop(route, 2, C),
        stop(route, 3, None),
        stop(route, 4, A),
    )

    body = suggest(client, route).json()  # type: ignore[attr-defined]

    assert body["stop_ids"] == ids(c, a, bare1, bare2)


def test_a_located_delivery_follows_an_ungeocoded_pickup_to_the_end(
    client: APIClient, route: Route
) -> None:
    shipment = make_shipment(route.branch, ShipmentStatus.ASSIGNED)
    pickup = stop(route, 1, None, shipment, "pickup")
    delivery = stop(route, 2, A, shipment, "delivery")
    other = stop(route, 3, C)

    body = suggest(client, route).json()  # type: ignore[attr-defined]

    assert body["stop_ids"] == ids(other, pickup, delivery)


def test_identical_coordinates_keep_the_manual_order(client: APIClient, route: Route) -> None:
    first, second, third = stop(route, 1, A), stop(route, 2, A), stop(route, 3, A)

    assert suggest(client, route).json()["stop_ids"] == ids(first, second, third)  # type: ignore[attr-defined]


def test_with_no_coordinates_the_manual_order_is_kept(client: APIClient, route: Route) -> None:
    first, second = stop(route, 1, None), stop(route, 2, None)

    assert suggest(client, route).json()["stop_ids"] == ids(first, second)  # type: ignore[attr-defined]


def test_no_stops_and_one_stop(client: APIClient, route: Route) -> None:
    assert suggest(client, route).json()["stop_ids"] == []  # type: ignore[attr-defined]
    only = stop(route, 1, A)
    assert suggest(client, route).json()["stop_ids"] == ids(only)  # type: ignore[attr-defined]


def test_the_query_count_does_not_grow_with_the_stops(client: APIClient, route: Route) -> None:
    stop(route, 1, A)
    suggest(client, route)
    with CaptureQueriesContext(connection) as one:
        suggest(client, route)
    for n in range(2, 12):
        stop(route, n, (13.0 + n / 100, 52.5))
    with CaptureQueriesContext(connection) as many:
        suggest(client, route)

    assert len(many) == len(one)


def test_a_300_stop_result_is_a_permutation(client: APIClient, route: Route) -> None:
    created = [stop(route, n, (13.0 + (n * 37 % 101) / 1000, 52.5)) for n in range(1, 301)]

    body = suggest(client, route).json()  # type: ignore[attr-defined]

    assert sorted(body["stop_ids"]) == sorted(ids(*created))


@pytest.mark.parametrize("status", [RouteStatus.PLANNED, RouteStatus.ASSIGNED])
def test_editable_routes_can_be_suggested(
    client: APIClient, branch: Branch, status: RouteStatus
) -> None:
    route = RouteFactory.create(branch=branch, status=status)
    stop(route, 1, A)

    assert suggest(client, route).status_code == 200  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "status", [RouteStatus.DISPATCHED, RouteStatus.IN_PROGRESS, RouteStatus.CANCELLED]
)
def test_other_statuses_are_409(client: APIClient, branch: Branch, status: RouteStatus) -> None:
    extra = {"cancelled_at": timezone.now()} if status == RouteStatus.CANCELLED else {}
    route = RouteFactory.create(branch=branch, status=status, **extra)

    response = suggest(client, route)

    assert (response.status_code, code(response)) == (409, "route.invalid_status")  # type: ignore[attr-defined]


def test_a_driver_without_dispatch_view_is_403(route: Route) -> None:
    driver = DriverFactory.create(
        user=UserFactory.create(company=route.company, roles=[RoleCode.DRIVER])
    )

    assert suggest(client_for(driver.user), route).status_code == 403  # type: ignore[attr-defined]


def test_a_route_of_another_branch_is_404(company, branch: Branch, other_branch: Branch) -> None:  # type: ignore[no-untyped-def]
    foreign = RouteFactory.create(branch=other_branch)
    user = make_user(company, RoleCode.DISPATCHER, branch)

    assert suggest(client_for(user), foreign).status_code == 404  # type: ignore[attr-defined]


def test_an_all_branch_role_sees_every_branch(company, other_branch: Branch) -> None:  # type: ignore[no-untyped-def]
    foreign = RouteFactory.create(branch=other_branch)
    owner = make_user(company, RoleCode.OWNER)

    assert suggest(client_for(owner), foreign).status_code == 200  # type: ignore[attr-defined]


def test_a_route_changed_after_the_suggestion_is_409_at_reorder(
    client: APIClient, route: Route
) -> None:
    a, b = stop(route, 1, A), stop(route, 2, B)
    body = suggest(client, route).json()  # type: ignore[attr-defined]
    Route.objects.filter(pk=route.pk).update(version=body["version"] + 1)

    response = post(
        client, route_url(route, "reorder/"), {"version": body["version"], "stop_ids": ids(b, a)}
    )

    assert (response.status_code, code(response)) == (409, "route.version_conflict")
