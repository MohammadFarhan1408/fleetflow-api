"""Stops, reorder and cancel over HTTP, with their effect on shipment status."""

from datetime import timedelta
from typing import Any
from unittest import mock

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditAction, AuditLog
from apps.dispatch.factories import RouteFactory, RouteStopFactory
from apps.dispatch.models import Route, RouteStatus, RouteStop, StopStatus
from apps.dispatch.tests.conftest import (
    add_stop,
    code,
    make_shipment,
    post,
    remove_stop,
    route_url,
    version_of,
)
from apps.shipments.factories import ShipmentFactory
from apps.shipments.models import (
    Shipment,
    ShipmentAddress,
    ShipmentStatus,
    TrackingEvent,
)
from apps.tenancy.models import Branch

pytestmark = pytest.mark.django_db

S = ShipmentStatus


def status_of(shipment: Shipment) -> str:
    return str(Shipment.objects.get(pk=shipment.pk).status)


def sequence_of(route: Route) -> list[tuple[int, str]]:
    stops = RouteStop.objects.filter(route=route).order_by("sequence")
    return [(s.sequence, s.type) for s in stops]


# --- adding stops --------------------------------------------------------------------------


def test_a_direct_route_takes_a_pickup_and_a_delivery(
    client: APIClient, route: Route, branch: Branch
) -> None:
    shipment = make_shipment(branch)

    pickup = add_stop(client, route, shipment, "pickup")
    delivery = add_stop(client, route, shipment, "delivery")

    assert (pickup.status_code, delivery.status_code) == (201, 201), delivery.content
    body = delivery.json()
    assert [(s["sequence"], s["type"], s["status"]) for s in body["stops"]] == [
        (1, "pickup", "pending"),
        (2, "delivery", "pending"),
    ]
    assert (body["version"], body["stop_count"]) == (3, 2)
    assert status_of(shipment) == "assigned"
    events = TrackingEvent.objects.filter(shipment=shipment)
    assert [(e.status_after, e.reason) for e in events] == [("assigned", "Added to a route")]
    assert {s.shipment_status_before for s in RouteStop.objects.all()} == {"created"}


def test_a_sorted_shipment_takes_a_delivery_only(
    client: APIClient, route: Route, branch: Branch
) -> None:
    shipment = make_shipment(branch, S.SORTED)

    pickup = add_stop(client, route, shipment, "pickup")
    delivery = add_stop(client, route, shipment, "delivery")

    assert (pickup.status_code, code(pickup)) == (409, "route.shipment_unavailable")
    assert delivery.status_code == 201
    assert status_of(shipment) == "assigned"


def test_a_stop_shows_its_shipment_address_and_whether_it_has_coordinates(
    client: APIClient, route: Route, branch: Branch
) -> None:
    located, bare = make_shipment(branch, S.SORTED), make_shipment(branch, S.SORTED)
    ShipmentAddress.objects.filter(shipment=located, role="delivery").update(
        location=Point(13.4, 52.5, srid=4326), geocode_confidence="manual"
    )
    add_stop(client, route, located)

    stops = add_stop(client, route, bare).json()["stops"]

    first, second = stops
    assert (first["has_location"], second["has_location"]) == (True, False)
    assert (first["address"]["latitude"], first["address"]["longitude"]) == (52.5, 13.4)
    assert second["address"]["latitude"] is None
    assert first["shipment"]["tracking_number"] == located.tracking_number
    assert first["shipment"]["status"] == "assigned"
    assert {"line1", "city", "country", "contact_name"} <= set(first["address"])


def test_a_stop_can_carry_a_time_window(client: APIClient, route: Route, branch: Branch) -> None:
    start = timezone.now() + timedelta(days=1)
    end = start + timedelta(hours=2)

    response = add_stop(
        client,
        route,
        make_shipment(branch, S.SORTED),
        window_start=start.isoformat(),
        window_end=end.isoformat(),
    )

    stop = response.json()["stops"][0]
    assert (stop["window_start"], stop["window_end"]) != (None, None)
    assert RouteStop.objects.get().window_end == end


@pytest.mark.parametrize(
    "window",
    [
        lambda s, e: {"window_start": s},  # only one end
        lambda s, e: {"window_end": e},
        lambda s, e: {"window_start": e, "window_end": s},  # backwards
        lambda s, e: {"window_start": s, "window_end": s},  # empty
    ],
)
def test_invalid_windows_are_400(
    client: APIClient, route: Route, branch: Branch, window: Any
) -> None:
    start = timezone.now() + timedelta(days=1)
    extra = window(start.isoformat(), (start + timedelta(hours=1)).isoformat())

    response = add_stop(client, route, make_shipment(branch, S.SORTED), **extra)

    assert (response.status_code, code(response)) == (400, "validation.invalid")
    assert not RouteStop.objects.exists()


def test_a_delivery_of_an_unpicked_shipment_needs_its_pickup_first(
    client: APIClient, route: Route, branch: Branch
) -> None:
    shipment = make_shipment(branch)  # created: still with the sender

    response = add_stop(client, route, shipment, "delivery")

    assert (response.status_code, code(response)) == (400, "route.pickup_dependency")
    assert not RouteStop.objects.exists()
    assert status_of(shipment) == "created"  # rolled back with the stop
    assert not TrackingEvent.objects.exists()
    assert version_of(route) == 1


def test_the_same_stop_twice_is_400(client: APIClient, route: Route, branch: Branch) -> None:
    shipment = make_shipment(branch, S.SORTED)
    add_stop(client, route, shipment)

    again = add_stop(client, route, shipment)

    assert (again.status_code, code(again)) == (400, "route.duplicate_stop")
    assert RouteStop.objects.count() == 1


@pytest.mark.parametrize(
    "shipment_status",
    [
        S.DRAFT,
        S.RECEIVED_AT_WAREHOUSE,
        S.LOADED,
        S.OUT_FOR_DELIVERY,
        S.DELIVERED,
        S.DELIVERY_FAILED,
        S.RETURNING,
        S.RETURNED_TO_SENDER,
        S.CANCELLED,
    ],
)
def test_a_shipment_in_the_wrong_status_cannot_join(
    client: APIClient, route: Route, branch: Branch, shipment_status: S
) -> None:
    shipment = make_shipment(branch, shipment_status)

    response = add_stop(client, route, shipment)

    assert (response.status_code, code(response)) == (409, "route.shipment_unavailable")
    assert status_of(shipment) == shipment_status


def test_a_shipment_on_another_route_cannot_join(
    client: APIClient, route: Route, branch: Branch
) -> None:
    shipment = make_shipment(branch, S.SORTED)
    other = RouteFactory.create(branch=branch)
    add_stop(client, other, shipment)

    response = add_stop(client, route, shipment)

    assert (response.status_code, code(response)) == (409, "route.shipment_unavailable")


def test_the_shipment_must_belong_to_the_routes_branch_and_company(
    client: APIClient, route: Route, other_branch: Branch
) -> None:
    far = make_shipment(other_branch, S.SORTED)  # the dispatcher cannot see it

    for shipment in (far, ShipmentFactory.create(status=S.SORTED)):
        response = add_stop(client, route, shipment)
        assert (response.status_code, code(response)) == (400, "validation.invalid_reference")
        assert str(shipment.public_id) not in response.content.decode()
    assert not RouteStop.objects.exists()


def test_a_visible_shipment_of_another_branch_is_refused(
    company: Any, branch: Branch, other_branch: Branch
) -> None:
    from apps.accounts.models import RoleCode
    from apps.accounts.tests.conftest import client_for
    from apps.dispatch.tests.conftest import make_user

    admin = client_for(make_user(company, RoleCode.ADMIN))  # sees every branch
    route = RouteFactory.create(branch=branch)
    far = make_shipment(other_branch, S.SORTED)

    response = add_stop(admin, route, far)

    assert (response.status_code, code(response)) == (400, "validation.invalid")
    assert "shipment_id" in response.json()["errors"]


def test_an_unknown_shipment_is_a_missing_reference(client: APIClient, route: Route) -> None:
    response = post(
        client,
        route_url(route, "stops/"),
        {"version": 1, "shipment_id": "00000000-0000-0000-0000-000000000000", "type": "delivery"},
    )

    assert (response.status_code, code(response)) == (400, "validation.invalid_reference")


def test_a_stale_version_is_409_and_changes_nothing(
    client: APIClient, route: Route, branch: Branch
) -> None:
    shipment = make_shipment(branch, S.SORTED)

    response = add_stop(client, route, shipment, version=7)

    assert (response.status_code, code(response)) == (409, "route.version_conflict")
    assert status_of(shipment) == "sorted"
    assert version_of(route) == 1


def test_the_most_stops_a_route_can_hold(client: APIClient, route: Route, branch: Branch) -> None:
    from apps.dispatch.constants import MAX_STOPS_PER_ROUTE

    assert MAX_STOPS_PER_ROUTE == 300
    with mock.patch("apps.dispatch.services.MAX_STOPS_PER_ROUTE", 2):
        first = add_stop(client, route, make_shipment(branch, S.SORTED))
        second = add_stop(client, route, make_shipment(branch, S.SORTED))
        third_shipment = make_shipment(branch, S.SORTED)
        third = add_stop(client, route, third_shipment)

    assert (first.status_code, second.status_code) == (201, 201)  # the limit itself is allowed
    assert (third.status_code, code(third)) == (400, "route.too_many_stops")
    assert status_of(third_shipment) == "sorted"


@pytest.mark.parametrize("route_status", [RouteStatus.DISPATCHED, RouteStatus.COMPLETED])
def test_stops_cannot_change_once_the_route_is_dispatched(
    client: APIClient, branch: Branch, route_status: RouteStatus
) -> None:
    route = RouteFactory.create(branch=branch, status=route_status)
    stop = RouteStopFactory.create(route=route)

    added = add_stop(client, route, make_shipment(branch, S.SORTED))
    removed = remove_stop(client, route, stop.public_id)
    reordered = post(
        client, route_url(route, "reorder/"), {"version": 1, "stop_ids": [str(stop.public_id)]}
    )

    for response in (added, removed, reordered):
        assert (response.status_code, code(response)) == (409, "route.invalid_status")


def test_an_assigned_route_still_takes_stops(client: APIClient, branch: Branch) -> None:
    route = RouteFactory.create(branch=branch, status=RouteStatus.ASSIGNED)

    assert add_stop(client, route, make_shipment(branch, S.SORTED)).status_code == 201


# --- removing stops ------------------------------------------------------------------------


@pytest.mark.parametrize("before", [S.CREATED, S.SORTED])
def test_removing_the_last_stop_returns_the_shipment_to_where_it_was(
    client: APIClient, route: Route, branch: Branch, before: S
) -> None:
    shipment = make_shipment(branch, before)
    if before == S.CREATED:
        add_stop(client, route, shipment, "pickup")
    else:
        add_stop(client, route, shipment, "delivery")
    stop = RouteStop.objects.get()

    response = remove_stop(client, route, stop.public_id)

    assert response.status_code == 200, response.content
    assert response.json()["stops"] == []
    assert response.json()["version"] == 3
    assert status_of(shipment) == before
    last = TrackingEvent.objects.filter(shipment=shipment).latest("id")
    assert (last.status_after, last.reason) == (before, "Removed from route")


def test_removing_one_of_two_stops_keeps_the_shipment_assigned(
    client: APIClient, route: Route, branch: Branch
) -> None:
    shipment = make_shipment(branch)
    add_stop(client, route, shipment, "pickup")
    add_stop(client, route, shipment, "delivery")

    response = remove_stop(client, route, RouteStop.objects.get(type="delivery").public_id)

    assert response.status_code == 200
    assert status_of(shipment) == "assigned"
    assert [s["type"] for s in response.json()["stops"]] == ["pickup"]


def test_removing_a_pickup_whose_delivery_remains_is_refused(
    client: APIClient, route: Route, branch: Branch
) -> None:
    shipment = make_shipment(branch)
    add_stop(client, route, shipment, "pickup")
    add_stop(client, route, shipment, "delivery")
    version = version_of(route)

    response = remove_stop(client, route, RouteStop.objects.get(type="pickup").public_id)

    assert (response.status_code, code(response)) == (400, "route.pickup_dependency")
    assert RouteStop.objects.count() == 2
    assert sequence_of(route) == [(1, "pickup"), (2, "delivery")]
    assert version_of(route) == version


def test_removing_a_stop_closes_the_gap(client: APIClient, route: Route, branch: Branch) -> None:
    shipments = [make_shipment(branch, S.SORTED) for _ in range(3)]
    for shipment in shipments:
        add_stop(client, route, shipment)
    middle = RouteStop.objects.get(shipment=shipments[1])

    response = remove_stop(client, route, middle.public_id)

    assert [s["sequence"] for s in response.json()["stops"]] == [1, 2]
    assert [s["shipment"]["public_id"] for s in response.json()["stops"]] == [
        str(shipments[0].public_id),
        str(shipments[2].public_id),
    ]


def test_a_shipment_changed_by_other_means_is_left_alone_on_removal(
    client: APIClient, route: Route, branch: Branch
) -> None:
    shipment = make_shipment(branch, S.SORTED)
    add_stop(client, route, shipment)
    Shipment.objects.filter(pk=shipment.pk).update(status=S.DELIVERED)  # e.g. an admin correction

    response = remove_stop(client, route, RouteStop.objects.get().public_id)

    assert response.status_code == 200
    assert status_of(shipment) == "delivered"
    assert TrackingEvent.objects.filter(shipment=shipment).count() == 1  # only the "assigned" one


def test_remove_needs_the_current_version_and_a_stop_of_this_route(
    client: APIClient, route: Route, branch: Branch
) -> None:
    add_stop(client, route, make_shipment(branch, S.SORTED))
    stop = RouteStop.objects.get()
    foreign_stop = RouteStopFactory.create(route=RouteFactory.create(branch=branch))

    stale = remove_stop(client, route, stop.public_id, version=1)
    foreign = remove_stop(client, route, foreign_stop.public_id)
    missing = client.delete(route_url(route, f"stops/{stop.public_id}/"))  # no version

    assert (stale.status_code, code(stale)) == (409, "route.version_conflict")
    assert foreign.status_code == 404
    assert (missing.status_code, code(missing)) == (400, "validation.invalid")
    assert RouteStop.objects.filter(pk=stop.pk).exists()


# --- reorder -------------------------------------------------------------------------------


def stop_ids(route: Route) -> list[str]:
    stops = RouteStop.objects.filter(route=route).order_by("sequence")
    return [str(s.public_id) for s in stops]


def reorder(client: APIClient, route: Route, ids: list[str], version: int | None = None) -> Any:
    v = version if version is not None else version_of(route)
    return post(client, route_url(route, "reorder/"), {"version": v, "stop_ids": ids})


def three_stops(client: APIClient, route: Route, branch: Branch) -> list[str]:
    for _ in range(3):
        add_stop(client, route, make_shipment(branch, S.SORTED))
    return stop_ids(route)


def test_reorder_sets_the_new_order(client: APIClient, route: Route, branch: Branch) -> None:
    first, second, third = three_stops(client, route, branch)
    version = version_of(route)

    response = reorder(client, route, [third, first, second])

    assert response.status_code == 200, response.content
    assert [s["public_id"] for s in response.json()["stops"]] == [third, first, second]
    assert [s["sequence"] for s in response.json()["stops"]] == [1, 2, 3]
    assert response.json()["version"] == version + 1


def test_swapping_two_stops_works_despite_the_unique_sequence(
    client: APIClient, route: Route, branch: Branch
) -> None:
    first, second, third = three_stops(client, route, branch)

    response = reorder(client, route, [second, first, third])

    assert response.status_code == 200
    assert stop_ids(route) == [second, first, third]


def test_reorder_must_list_every_stop_exactly_once(
    client: APIClient, route: Route, branch: Branch
) -> None:
    first, second, third = three_stops(client, route, branch)
    unknown = "00000000-0000-0000-0000-000000000000"
    foreign = str(RouteStopFactory.create(route=RouteFactory.create(branch=branch)).public_id)
    version = version_of(route)

    for ids in (
        [first, second],  # a stop missing
        [first, second, third, third],  # a duplicate
        [first, first, second],  # a duplicate and a stop missing
        [first, second, unknown],  # an unknown one
        [first, second, foreign],  # another route's stop
        [first, second, third, unknown],  # an extra one
        [],
    ):
        response = reorder(client, route, ids)
        assert (response.status_code, code(response)) == (400, "route.invalid_sequence"), ids
    assert stop_ids(route) == [first, second, third]
    assert version_of(route) == version


def test_reorder_cannot_put_a_delivery_before_its_pickup(
    client: APIClient, route: Route, branch: Branch
) -> None:
    shipment = make_shipment(branch)
    add_stop(client, route, shipment, "pickup")
    add_stop(client, route, shipment, "delivery")
    pickup, delivery = stop_ids(route)

    response = reorder(client, route, [delivery, pickup])

    assert (response.status_code, code(response)) == (400, "route.pickup_dependency")
    assert stop_ids(route) == [pickup, delivery]  # rolled back


def test_reorder_with_a_stale_version_is_409(
    client: APIClient, route: Route, branch: Branch
) -> None:
    first, second, third = three_stops(client, route, branch)

    response = reorder(client, route, [third, second, first], version=1)

    assert (response.status_code, code(response)) == (409, "route.version_conflict")
    assert stop_ids(route) == [first, second, third]


# --- cancel --------------------------------------------------------------------------------


def test_cancelling_returns_every_shipment_and_keeps_the_stops(
    client: APIClient, route: Route, branch: Branch
) -> None:
    created, sorted_ = make_shipment(branch), make_shipment(branch, S.SORTED)
    add_stop(client, route, created, "pickup")
    add_stop(client, route, created, "delivery")
    add_stop(client, route, sorted_)
    version = version_of(route)

    response = post(client, route_url(route, "cancel/"), {"version": version})

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["cancelled_at"] is not None
    assert body["version"] == version + 1
    assert body["stop_count"] == 3  # history stays
    assert (status_of(created), status_of(sorted_)) == ("created", "sorted")
    last = TrackingEvent.objects.filter(shipment=sorted_).latest("id")
    assert (last.status_after, last.reason) == ("sorted", "Route cancelled")
    entry = AuditLog.objects.get(action=AuditAction.ROUTE_CANCELLED)
    assert (entry.object_id, entry.after) == (body["public_id"], {"status": "cancelled"})


def test_the_freed_shipments_can_join_another_route(
    client: APIClient, route: Route, branch: Branch
) -> None:
    shipment = make_shipment(branch, S.SORTED)
    add_stop(client, route, shipment)
    post(client, route_url(route, "cancel/"), {"version": version_of(route)})
    other = RouteFactory.create(branch=branch)

    assert add_stop(client, other, shipment).status_code == 201


def test_a_route_with_worked_stops_cannot_be_cancelled(
    client: APIClient, route: Route, branch: Branch
) -> None:
    add_stop(client, route, make_shipment(branch, S.SORTED))
    RouteStop.objects.update(status=StopStatus.COMPLETED)

    response = post(client, route_url(route, "cancel/"), {"version": version_of(route)})

    assert (response.status_code, code(response)) == (409, "route.stops_started")
    assert Route.objects.get(pk=route.pk).status == "planned"


def test_a_cancelled_route_cannot_be_cancelled_or_changed(
    client: APIClient, route: Route, branch: Branch
) -> None:
    post(client, route_url(route, "cancel/"), {"version": 1})
    version = version_of(route)

    again = post(client, route_url(route, "cancel/"), {"version": version})
    added = add_stop(client, route, make_shipment(branch, S.SORTED))

    for response in (again, added):
        assert (response.status_code, code(response)) == (409, "route.invalid_status")


def test_a_dispatched_route_can_be_cancelled_and_an_in_progress_one_cannot(
    client: APIClient, branch: Branch
) -> None:
    dispatched = RouteFactory.create(branch=branch, status=RouteStatus.DISPATCHED)
    stop = RouteStopFactory.create(route=dispatched)
    in_progress = RouteFactory.create(branch=branch, status=RouteStatus.IN_PROGRESS)

    ok = post(client, route_url(dispatched, "cancel/"), {"version": 1})
    refused = post(client, route_url(in_progress, "cancel/"), {"version": 1})

    assert ok.status_code == 200
    assert status_of(stop.shipment) == "sorted"
    assert (refused.status_code, code(refused)) == (409, "route.invalid_status")


def test_cancel_with_a_stale_version_is_409(client: APIClient, route: Route) -> None:
    response = post(client, route_url(route, "cancel/"), {"version": 5})

    assert (response.status_code, code(response)) == (409, "route.version_conflict")
    assert Route.objects.get(pk=route.pk).status == "planned"


def test_cancel_is_idempotent_with_a_key(client: APIClient, route: Route) -> None:
    headers = {"Idempotency-Key": "cancel-1"}
    first = post(client, route_url(route, "cancel/"), {"version": 1}, **headers)
    second = post(client, route_url(route, "cancel/"), {"version": 1}, **headers)

    assert (first.status_code, second.status_code) == (200, 200)
    assert AuditLog.objects.filter(action=AuditAction.ROUTE_CANCELLED).count() == 1


def test_a_second_stop_is_refused_if_the_shipment_was_changed_by_other_means(
    client: APIClient, route: Route, branch: Branch
) -> None:
    shipment = make_shipment(branch)
    add_stop(client, route, shipment, "pickup")
    Shipment.objects.filter(pk=shipment.pk).update(status=S.CANCELLED)  # e.g. a correction

    response = add_stop(client, route, shipment, "delivery")

    assert (response.status_code, code(response)) == (409, "route.shipment_unavailable")
    assert RouteStop.objects.count() == 1
