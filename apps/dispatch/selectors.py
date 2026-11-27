"""Tenant- and branch-scoped route reads (architecture §8, §9). Every read starts here."""

import math
import uuid
from datetime import date

from django.contrib.gis.geos import Point
from django.db.models import Count, Exists, OuterRef, Prefetch, QuerySet
from django.shortcuts import get_object_or_404

from apps.accounts.models import User
from apps.accounts.selectors import branches_in_scope
from apps.dispatch.models import Route, RouteStatus, RouteStop, StopType
from apps.dispatch.permissions import DISPATCH_ALL_BRANCHES
from apps.fleet import selectors as fleet_selectors
from apps.shipments.models import ShipmentAddress


def routes_in_scope(user: User) -> QuerySet[Route]:
    """The user's company's routes whose branch the user may see. Services lock through
    this, so they enforce the same scope as the views."""
    return (
        Route.objects.for_company(user.company)
        .filter(branch__in=branches_in_scope(user, DISPATCH_ALL_BRANCHES))
        .select_related("branch", "driver__user", "vehicle")
    )


def stops_with_details() -> QuerySet[RouteStop]:
    """Stops in order, with what the serializer reads (shipment, its addresses, and whether
    the stop's own address has coordinates) in a constant number of queries."""
    located = ShipmentAddress.objects.filter(
        shipment=OuterRef("shipment"), role=OuterRef("type"), location__isnull=False
    )
    return (
        RouteStop.objects.select_related("shipment")
        .prefetch_related(
            Prefetch(
                "shipment__addresses",
                queryset=ShipmentAddress.objects.select_related("source_address"),
            )
        )
        .annotate(has_location=Exists(located))
        .order_by("sequence")
    )


def routes_for_user(user: User) -> QuerySet[Route]:
    """Scoped routes with their stop count."""
    return routes_in_scope(user).annotate(stop_count=Count("stops"))


def list_routes(
    user: User,
    *,
    route_date: date | None = None,
    branch: uuid.UUID | None = None,
    status: RouteStatus | None = None,
) -> QuerySet[Route]:
    routes = routes_for_user(user)
    if route_date:
        routes = routes.filter(route_date=route_date)
    if branch:
        routes = routes.filter(branch__public_id=branch)
    if status:
        routes = routes.filter(status=status)
    return routes


def get_route(user: User, public_id: uuid.UUID) -> Route:
    """A route the user may see, with its stops in `ordered_stops`, or 404 (other tenants'
    and other branches' routes are indistinguishable from missing ones)."""
    routes = routes_for_user(user).prefetch_related(
        Prefetch("stops", queryset=stops_with_details(), to_attr="ordered_stops")
    )
    return get_object_or_404(routes, public_id=public_id)


def _distance_km(a: Point, b: Point) -> float:
    """Straight-line (great-circle) distance between two WGS 84 points, by haversine."""
    lat_a, lat_b = math.radians(a.y), math.radians(b.y)
    h = (
        math.sin((lat_b - lat_a) / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(math.radians(b.x - a.x) / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def suggest_stop_order(stops: list[RouteStop]) -> list[RouteStop]:
    """Greedy nearest-neighbour order of a route's stops (architecture §13, memory D-API-33).

    `stops` is the route in its manual order, from `stops_with_details`. The tour starts at
    the first stop that may come first and always goes on to the nearest stop that may
    follow, a delivery only after its pickup on the same route. Stops without coordinates,
    and deliveries whose pickup has none, keep their manual order at the end. Nothing is saved.
    # ponytail: O(n²) over at most 300 stops; a PostGIS query or the external optimizer
    # (architecture §13, FUTURE) if routes grow or road distance matters.
    """
    points: dict[int, Point | None] = {
        stop.pk: next(a.location for a in stop.shipment.addresses.all() if a.role == stop.type)
        for stop in stops
    }
    pickups = {s.shipment_id: s for s in stops if s.type == StopType.PICKUP}

    def has_point(stop: RouteStop) -> bool:
        # A delivery goes with its pickup: it cannot be placed before one that is not.
        pickup = pickups.get(stop.shipment_id)
        return points[stop.pk] is not None and (
            stop.type == StopType.PICKUP or pickup is None or points[pickup.pk] is not None
        )

    remaining = [s for s in stops if has_point(s)]
    tail = [s for s in stops if not has_point(s)]
    ordered: list[RouteStop] = []
    placed: set[int] = set()
    while remaining:
        ready = [
            s
            for s in remaining
            if s.type == StopType.PICKUP
            or s.shipment_id not in pickups
            or pickups[s.shipment_id].pk in placed
        ]
        if ordered:
            # min() keeps the first of equal distances, so ties follow the manual order.
            here = points[ordered[-1].pk]
            chosen = min(ready, key=lambda s: _distance_km(here, points[s.pk]))  # type: ignore[arg-type]
        else:
            chosen = ready[0]
        ordered.append(chosen)
        placed.add(chosen.pk)
        remaining.remove(chosen)
    return ordered + tail


def get_stop(route: Route, public_id: uuid.UUID) -> RouteStop:
    return get_object_or_404(
        RouteStop.objects.for_company(route.company), route=route, public_id=public_id
    )


# What a driver sees of their routes: nothing before the dispatcher sends it out.
DRIVER_VISIBLE_STATUSES = (RouteStatus.DISPATCHED, RouteStatus.IN_PROGRESS, RouteStatus.COMPLETED)


def own_routes(user: User) -> QuerySet[Route]:
    """The signed-in driver's dispatched, in-progress and completed routes with their stop
    count. Not branch-scoped: a driver is assigned a route, not a branch. Without an active
    driver profile this answers 403 `driver.profile_inactive` (architecture §7)."""
    driver = fleet_selectors.own_driver(user, user.company.local_today())
    return (
        Route.objects.for_company(user.company)
        .filter(driver=driver, status__in=DRIVER_VISIBLE_STATUSES)
        .select_related("branch", "vehicle")
        .annotate(stop_count=Count("stops"))
    )


def list_own_routes(
    user: User, *, route_date: date | None = None, status: RouteStatus | None = None
) -> QuerySet[Route]:
    routes = own_routes(user)
    if route_date:
        routes = routes.filter(route_date=route_date)
    if status:
        routes = routes.filter(status=status)
    return routes


def get_own_route(user: User, public_id: uuid.UUID) -> Route:
    """One of the driver's own routes with its stops in `ordered_stops`, or 404 (another
    driver's, another company's and a not yet dispatched route are all the same answer)."""
    routes = own_routes(user).prefetch_related(
        Prefetch("stops", queryset=stops_with_details(), to_attr="ordered_stops")
    )
    return get_object_or_404(routes, public_id=public_id)
