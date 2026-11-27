"""Route writes (architecture §5, §13). Views pass the acting user; every service re-locks
the route through the actor's company and branch scope (rules.md §3). Each change checks the
caller's `version` and bumps it, so concurrent dispatchers get 409 instead of overwriting."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, F, Sum
from django.http import HttpRequest
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.permissions import get_user_permissions
from apps.accounts.selectors import branches_in_scope
from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.core.errors import DomainError, ErrorCode, invalid_reference
from apps.dispatch import selectors
from apps.dispatch.constants import CM3_PER_M3, MAX_STOPS_PER_ROUTE
from apps.dispatch.models import (
    ACTIVE_STATUSES,
    CANCELLABLE_STATUSES,
    EDITABLE_STATUSES,
    ManifestItem,
    Route,
    RouteStatus,
    RouteStop,
    StopStatus,
    StopType,
)
from apps.dispatch.permissions import (
    DISPATCH_ALL_BRANCHES,
    DISPATCH_OVERRIDE_CAPACITY,
    DISPATCH_REASSIGN_ACTIVE,
)
from apps.fleet import selectors as fleet_selectors
from apps.fleet.models import Driver, Vehicle
from apps.shipments import selectors as shipment_selectors
from apps.shipments import services as shipment_services
from apps.shipments.models import EventSource, Shipment, ShipmentItem, ShipmentStatus


def _audit(
    action: AuditAction, actor: User, route: Route, request: HttpRequest, after: dict[str, str]
) -> None:
    # IDs and enums only (rules.md §3).
    audit.record(
        action,
        company=actor.company,
        actor=actor,
        object_type="route",
        object_id=str(route.public_id),
        after=after,
        request=request,
    )


def _check_date(actor: User, route_date: date) -> None:
    if route_date < actor.company.local_today():
        raise DomainError(
            ErrorCode.VALIDATION_INVALID, errors={"route_date": ["Must not be in the past."]}
        )


def _lock_scoped(actor: User, route: Route) -> Route:
    """The route, locked, if the actor may see it; no version check (idempotent repeats)."""
    locked = (
        selectors.routes_in_scope(actor).select_for_update(of=("self",)).filter(pk=route.pk).first()
    )
    if locked is None:
        raise DomainError(ErrorCode.NOT_FOUND)
    return locked


def _check_version(locked: Route, version: int) -> None:
    if locked.version != version:
        raise DomainError(
            ErrorCode.ROUTE_VERSION_CONFLICT,
            detail=f"The route is at version {locked.version}, not {version}.",
        )


def _lock_route(actor: User, route: Route, version: int) -> Route:
    locked = _lock_scoped(actor, route)
    _check_version(locked, version)
    return locked


def _lock_editable(actor: User, route: Route, version: int) -> Route:
    locked = _lock_route(actor, route, version)
    if locked.status not in EDITABLE_STATUSES:
        raise DomainError(
            ErrorCode.ROUTE_INVALID_STATUS, detail=f"A {locked.status} route cannot be changed."
        )
    return locked


def _bump(route: Route, *fields: str) -> None:
    route.version += 1
    route.save(update_fields=["version", "updated_at", *fields])


# --- routes --------------------------------------------------------------------------------


@transaction.atomic
def create_route(
    *,
    actor: User,
    branch_id: uuid.UUID,
    route_date: date,
    sequence_enforced: bool,
    request: HttpRequest,
) -> Route:
    branch = branches_in_scope(actor, DISPATCH_ALL_BRANCHES).filter(public_id=branch_id).first()
    if branch is None:
        raise invalid_reference("branch_id", "Branch not found.")
    _check_date(actor, route_date)
    route = Route(
        company=actor.company,
        branch=branch,
        route_date=route_date,
        sequence_enforced=sequence_enforced,
    )
    route.full_clean(validate_constraints=False)
    route.save()
    _audit(AuditAction.ROUTE_CREATED, actor, route, request, {"status": route.status})
    return route


@transaction.atomic
def update_route(
    *,
    actor: User,
    route: Route,
    version: int,
    route_date: date | None,
    sequence_enforced: bool | None,
) -> Route:
    route = _lock_route(actor, route, version)
    if route.status != RouteStatus.PLANNED:
        raise DomainError(
            ErrorCode.ROUTE_INVALID_STATUS, detail="Only a planned route can be edited."
        )
    if route_date is not None:
        _check_date(actor, route_date)
        route.route_date = route_date
    if sequence_enforced is not None:
        route.sequence_enforced = sequence_enforced
    _bump(route, "route_date", "sequence_enforced")
    return route


@transaction.atomic
def cancel_route(*, actor: User, route: Route, version: int, request: HttpRequest) -> Route:
    """Cancel the route; its shipments go back to the status they had before it. A route
    with stops that were already worked cannot be cancelled."""
    route = _lock_route(actor, route, version)
    if route.status not in CANCELLABLE_STATUSES:
        raise DomainError(
            ErrorCode.ROUTE_INVALID_STATUS, detail=f"A {route.status} route cannot be cancelled."
        )
    stops = _ordered_stops(route)
    if any(stop.status != StopStatus.PENDING for stop in stops):
        raise DomainError(ErrorCode.ROUTE_STOPS_STARTED)
    for shipment_id, before in {s.shipment_id: s.shipment_status_before for s in stops}.items():
        _release_shipment(actor, shipment_id, before, "Route cancelled", request)
    route.status = RouteStatus.CANCELLED
    route.cancelled_at = timezone.now()
    _bump(route, "status", "cancelled_at")
    _audit(AuditAction.ROUTE_CANCELLED, actor, route, request, {"status": route.status})
    return route


# --- stops ---------------------------------------------------------------------------------


def _release_shipment(
    actor: User, shipment_id: int, before: str, reason: str, request: HttpRequest
) -> None:
    """Return a shipment to its pre-route status. Skipped if its status was changed by
    other means meanwhile (an admin correction): the route no longer decides it."""
    shipment = (
        shipment_selectors.shipments_for_user(actor)
        .select_for_update(of=("self",))
        .filter(pk=shipment_id)
        .first()
    )
    if shipment is None:
        raise DomainError(ErrorCode.NOT_FOUND)
    if shipment.status != ShipmentStatus.ASSIGNED:
        return
    shipment_services.transition(
        actor=actor,
        shipment=shipment,
        to=ShipmentStatus(before),
        expected_status=ShipmentStatus.ASSIGNED,
        reason=reason,
        request=request,
        authorized_by_caller=True,
    )


def _ordered_stops(route: Route) -> list[RouteStop]:
    return list(RouteStop.objects.filter(route=route).order_by("sequence"))


def _validate_stops(route: Route) -> None:
    """The whole route's pickup rules: a pickup precedes its delivery, and a shipment that
    was not yet picked up (`created`) cannot be delivered without its pickup on the route."""
    by_shipment: dict[int, dict[str, RouteStop]] = {}
    for stop in _ordered_stops(route):
        by_shipment.setdefault(stop.shipment_id, {})[stop.type] = stop
    for stops in by_shipment.values():
        pickup, delivery = stops.get(StopType.PICKUP), stops.get(StopType.DELIVERY)
        if delivery is None:
            continue
        if pickup is None:
            if delivery.shipment_status_before == ShipmentStatus.CREATED:
                raise DomainError(
                    ErrorCode.ROUTE_PICKUP_DEPENDENCY,
                    detail="A shipment that was not picked up yet needs its pickup on the route.",
                )
        elif pickup.sequence > delivery.sequence:
            raise DomainError(ErrorCode.ROUTE_PICKUP_DEPENDENCY)


def _resequence(stops: list[RouteStop]) -> None:
    for number, stop in enumerate(stops, start=1):
        stop.sequence = number
    # One statement; the unique constraint is deferred, so interim duplicates are fine.
    RouteStop.objects.bulk_update(stops, ["sequence"])


@transaction.atomic
def add_stop(
    *,
    actor: User,
    route: Route,
    version: int,
    shipment_id: uuid.UUID,
    type: str,
    window_start: datetime | None,
    window_end: datetime | None,
    request: HttpRequest,
) -> RouteStop:
    """Append a pickup or delivery stop. A shipment's first stop moves it to `assigned`."""
    route = _lock_editable(actor, route, version)
    shipment = (
        shipment_selectors.shipments_for_user(actor)
        .select_for_update(of=("self",))
        .filter(public_id=shipment_id)
        .first()
    )
    if shipment is None:
        raise invalid_reference("shipment_id", "Shipment not found.")
    if shipment.branch_id != route.branch_id:
        raise DomainError(
            ErrorCode.VALIDATION_INVALID,
            errors={"shipment_id": ["The shipment belongs to another branch."]},
        )
    stops = _ordered_stops(route)
    if len(stops) >= MAX_STOPS_PER_ROUTE:
        raise DomainError(ErrorCode.ROUTE_TOO_MANY_STOPS)
    siblings = [s for s in stops if s.shipment_id == shipment.pk]
    if any(s.type == type for s in siblings):
        raise DomainError(ErrorCode.ROUTE_DUPLICATE_STOP)
    before = _status_before_route(shipment, siblings)
    if type == StopType.PICKUP and before == ShipmentStatus.SORTED:
        raise DomainError(
            ErrorCode.ROUTE_SHIPMENT_UNAVAILABLE,
            detail="The shipment is already at the warehouse; it cannot be picked up.",
        )
    stop = RouteStop(
        company=actor.company,
        route=route,
        shipment=shipment,
        type=type,
        sequence=len(stops) + 1,
        window_start=window_start,
        window_end=window_end,
        shipment_status_before=before,
    )
    stop.full_clean(exclude=["route", "shipment"], validate_constraints=False)
    stop.save()
    if not siblings:
        shipment_services.transition(
            actor=actor,
            shipment=shipment,
            to=ShipmentStatus.ASSIGNED,
            expected_status=ShipmentStatus(before),
            reason="Added to a route",
            request=request,
            authorized_by_caller=True,
        )
    _validate_stops(route)
    if route.vehicle is not None:
        excess = _capacity_excess(route, route.vehicle)
        if excess:  # a stored override covered the old load only; it does not cover new stops
            raise DomainError(ErrorCode.ASSIGNMENT_OVER_CAPACITY, detail=excess)
    _bump(route)
    return stop


def _status_before_route(shipment: Shipment, siblings: list[RouteStop]) -> str:
    """The status the shipment had before this route took it, or 409 if it cannot join."""
    if siblings:  # its other stop on this route already moved it to `assigned`
        if shipment.status == ShipmentStatus.ASSIGNED:
            return siblings[0].shipment_status_before
    elif shipment.status in (ShipmentStatus.CREATED, ShipmentStatus.SORTED):
        return str(shipment.status)
    raise DomainError(
        ErrorCode.ROUTE_SHIPMENT_UNAVAILABLE,
        detail=f"A {shipment.status} shipment cannot be added to a route.",
    )


@transaction.atomic
def remove_stop(
    *, actor: User, route: Route, stop: RouteStop, version: int, request: HttpRequest
) -> RouteStop:
    """Remove a stop and close the gap. When it was the shipment's last stop, the shipment
    returns to the status it had before the route."""
    route = _lock_editable(actor, route, version)
    locked = RouteStop.objects.select_for_update().filter(route=route, pk=stop.pk).first()
    if locked is None:
        raise DomainError(ErrorCode.NOT_FOUND)
    locked.delete()
    remaining = _ordered_stops(route)
    _resequence(remaining)
    if not any(s.shipment_id == locked.shipment_id for s in remaining):
        _release_shipment(
            actor, locked.shipment_id, locked.shipment_status_before, "Removed from route", request
        )
    _validate_stops(route)
    _bump(route)
    return locked


@transaction.atomic
def reorder_stops(*, actor: User, route: Route, version: int, stop_ids: list[uuid.UUID]) -> Route:
    """Set the stop order. `stop_ids` must list every stop of the route exactly once."""
    route = _lock_editable(actor, route, version)
    stops = _ordered_stops(route)
    by_id = {stop.public_id: stop for stop in stops}
    if len(stop_ids) != len(set(stop_ids)) or set(stop_ids) != set(by_id):
        raise DomainError(ErrorCode.ROUTE_INVALID_SEQUENCE)
    _resequence([by_id[stop_id] for stop_id in stop_ids])
    _validate_stops(route)
    _bump(route)
    return route


# --- assignment ----------------------------------------------------------------------------


def _load(route: Route) -> tuple[Decimal, Decimal]:
    """Total weight (kg) and volume (m³) of the route's shipments, each counted once.
    # ponytail: the whole route's total, not the peak load between pickups and deliveries;
    # a per-sequence profile if dispatchers need it."""
    shipments = RouteStop.objects.filter(route=route).values("shipment")
    weight = Shipment.objects.filter(pk__in=shipments).aggregate(total=Sum("total_weight_kg"))
    cubic_cm = ShipmentItem.objects.filter(shipment__in=shipments).aggregate(
        total=Sum(
            F("length_cm") * F("width_cm") * F("height_cm"),
            output_field=DecimalField(max_digits=24, decimal_places=3),
        )
    )
    return weight["total"] or Decimal(0), (cubic_cm["total"] or Decimal(0)) / CM3_PER_M3


def _capacity_excess(route: Route, vehicle: Vehicle) -> str | None:
    """How the route's load exceeds the vehicle's capacity, or None when it fits."""
    weight, volume = _load(route)
    if weight <= vehicle.capacity_weight_kg and volume <= vehicle.capacity_volume_m3:
        return None
    return (
        f"The load is {weight:.3f} kg and {volume:.3f} m³; the vehicle takes "
        f"{vehicle.capacity_weight_kg:.3f} kg and {vehicle.capacity_volume_m3:.3f} m³."
    )


def _lock_assignees(
    actor: User, route: Route, driver_id: uuid.UUID, vehicle_id: uuid.UUID
) -> tuple[Driver, Vehicle]:
    """Lock the driver, then the vehicle, and check both for the route date: in scope, the
    route's branch, available, licence category, and no other active route that date. Always
    that lock order, so two callers cannot deadlock."""
    on_date = route.route_date
    driver = (
        fleet_selectors.drivers_for_user(actor, on_date)
        .select_for_update(of=("self",))
        .filter(public_id=driver_id)
        .first()
    )
    if driver is None:
        raise invalid_reference("driver_id", "Driver not found.")
    vehicle = (
        fleet_selectors.vehicles_for_user(actor, on_date)
        .select_for_update(of=("self",))
        .filter(public_id=vehicle_id)
        .first()
    )
    if vehicle is None:
        raise invalid_reference("vehicle_id", "Vehicle not found.")
    for field, home_branch_id in (
        ("driver_id", driver.branch_id),
        ("vehicle_id", vehicle.branch_id),
    ):
        if home_branch_id != route.branch_id:
            raise DomainError(
                ErrorCode.VALIDATION_INVALID,
                errors={field: ["Must belong to the route's branch."]},
            )

    if driver_reasons := fleet_selectors.driver_unavailability(driver):
        raise DomainError(
            ErrorCode.ASSIGNMENT_DRIVER_UNAVAILABLE,
            errors={"reasons": [str(reason) for reason in driver_reasons]},
        )
    if vehicle_reasons := fleet_selectors.vehicle_unavailability(vehicle):
        raise DomainError(
            ErrorCode.ASSIGNMENT_VEHICLE_UNAVAILABLE,
            errors={"reasons": [str(reason) for reason in vehicle_reasons]},
        )
    if not fleet_selectors.license_covers(driver, vehicle, on_date):
        raise DomainError(
            ErrorCode.ASSIGNMENT_LICENSE_CATEGORY,
            detail="The driver's licence does not cover category "
            f"{vehicle.required_license_category}.",
        )
    others = (
        Route.objects.for_company(actor.company)
        .filter(route_date=on_date, status__in=ACTIVE_STATUSES)
        .exclude(pk=route.pk)
    )
    if others.filter(driver=driver).exists():
        raise DomainError(ErrorCode.ASSIGNMENT_DRIVER_CONFLICT)
    if others.filter(vehicle=vehicle).exists():
        raise DomainError(ErrorCode.ASSIGNMENT_VEHICLE_CONFLICT)
    return driver, vehicle


@transaction.atomic
def assign(
    *,
    actor: User,
    route: Route,
    version: int,
    driver_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    override_reason: str,
    request: HttpRequest,
) -> Route:
    """Give a planned (or assigned) route a driver and a vehicle, checked for the route date
    (architecture §13). The driver and vehicle rows are locked, always in that order, so two
    dispatchers cannot give one of them to two routes; the partial unique indexes are the
    backstop. Repeating the current assignment is a no-op, even with a stale version."""
    route = _lock_scoped(actor, route)
    if (
        route.status == RouteStatus.ASSIGNED
        and route.driver is not None
        and route.vehicle is not None
        and route.driver.public_id == driver_id
        and route.vehicle.public_id == vehicle_id
    ):
        return route
    _check_version(route, version)
    if route.status not in EDITABLE_STATUSES:
        raise DomainError(
            ErrorCode.ROUTE_INVALID_STATUS, detail=f"A {route.status} route cannot be assigned."
        )
    _check_date(actor, route.route_date)

    driver, vehicle = _lock_assignees(actor, route, driver_id, vehicle_id)
    excess = _capacity_excess(route, vehicle)
    if excess and not override_reason:
        raise DomainError(ErrorCode.ASSIGNMENT_OVER_CAPACITY, detail=excess)
    if excess and DISPATCH_OVERRIDE_CAPACITY not in get_user_permissions(actor):
        raise DomainError(ErrorCode.PERMISSION_DENIED)

    route.driver, route.vehicle = driver, vehicle
    route.status = RouteStatus.ASSIGNED
    # The reason is kept only when it was needed (the audit log never holds its text).
    route.capacity_override_reason = override_reason if excess else ""
    _bump(route, "driver", "vehicle", "status", "capacity_override_reason")
    _audit(
        AuditAction.ROUTE_ASSIGNED,
        actor,
        route,
        request,
        {
            "status": route.status,
            "driver": str(driver.public_id),
            "vehicle": str(vehicle.public_id),
            "capacity_override": "true" if excess else "false",
        },
    )
    return route


@transaction.atomic
def unassign(*, actor: User, route: Route, version: int, request: HttpRequest) -> Route:
    """Take the driver and vehicle off an assigned route, back to `planned`. Its stops, and
    so its shipments' `assigned` status, stay (memory D-API-31). Repeating it is a no-op."""
    route = _lock_scoped(actor, route)
    if route.status == RouteStatus.PLANNED:
        return route
    _check_version(route, version)
    if route.status != RouteStatus.ASSIGNED:
        raise DomainError(
            ErrorCode.ROUTE_INVALID_STATUS, detail=f"A {route.status} route cannot be unassigned."
        )
    route.driver = route.vehicle = None
    route.status = RouteStatus.PLANNED
    route.capacity_override_reason = ""
    _bump(route, "driver", "vehicle", "status", "capacity_override_reason")
    _audit(AuditAction.ROUTE_UNASSIGNED, actor, route, request, {"status": route.status})
    return route


# --- dispatch, reassignment and the driver's start ------------------------------------------


@transaction.atomic
def dispatch_route(*, actor: User, route: Route, version: int, request: HttpRequest) -> Route:
    """Send an assigned route out. The driver, vehicle, licence and capacity are checked
    again for the route date (they may have changed since assignment), and the shipments
    the route is expected to carry are recorded as its manifest. Repeating it on a
    dispatched route is a no-op. Shipments change status only when the driver starts."""
    route = _lock_scoped(actor, route)
    if route.status == RouteStatus.DISPATCHED:
        return route
    _check_version(route, version)
    if route.status != RouteStatus.ASSIGNED or route.driver is None or route.vehicle is None:
        raise DomainError(
            ErrorCode.ROUTE_INVALID_STATUS, detail=f"A {route.status} route cannot be dispatched."
        )
    _check_date(actor, route.route_date)
    stops = _ordered_stops(route)
    if not stops:
        raise DomainError(ErrorCode.ROUTE_EMPTY)
    driver, vehicle = _lock_assignees(actor, route, route.driver.public_id, route.vehicle.public_id)
    excess = _capacity_excess(route, vehicle)
    if excess and not route.capacity_override_reason:  # an override covers the load it was given
        raise DomainError(ErrorCode.ASSIGNMENT_OVER_CAPACITY, detail=excess)
    ManifestItem.objects.bulk_create(
        ManifestItem(company=actor.company, route=route, shipment_id=shipment_id)
        for shipment_id in dict.fromkeys(stop.shipment_id for stop in stops)
    )
    route.status = RouteStatus.DISPATCHED
    route.dispatched_at = timezone.now()
    _bump(route, "status", "dispatched_at")
    _audit(
        AuditAction.ROUTE_DISPATCHED,
        actor,
        route,
        request,
        {
            "status": route.status,
            "driver": str(driver.public_id),
            "vehicle": str(vehicle.public_id),
        },
    )
    return route


@transaction.atomic
def reassign(
    *,
    actor: User,
    route: Route,
    version: int,
    driver_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    request: HttpRequest,
) -> Route:
    """Change the driver and vehicle of a dispatched or in-progress route
    (`dispatch.reassign_active`). Allowed only while every stop is still pending: splitting
    a route that has worked stops waits for the delivery workflow (Phase 5, memory D-API-32).
    The route goes back to `dispatched`, so the new driver starts it with their own shift.
    The new vehicle must fit the load; there is no override here."""
    if DISPATCH_REASSIGN_ACTIVE not in get_user_permissions(actor):
        raise DomainError(ErrorCode.PERMISSION_DENIED)
    route = _lock_scoped(actor, route)
    if (
        route.driver is not None
        and route.vehicle is not None
        and route.driver.public_id == driver_id
        and route.vehicle.public_id == vehicle_id
        and route.status in (RouteStatus.DISPATCHED, RouteStatus.IN_PROGRESS)
    ):
        return route
    _check_version(route, version)
    if route.status not in (RouteStatus.DISPATCHED, RouteStatus.IN_PROGRESS):
        raise DomainError(
            ErrorCode.ROUTE_INVALID_STATUS,
            detail=f"A {route.status} route cannot be reassigned here; use assign or unassign.",
        )
    if any(stop.status != StopStatus.PENDING for stop in _ordered_stops(route)):
        raise DomainError(ErrorCode.ROUTE_STOPS_STARTED)
    previous_driver, previous_vehicle = route.driver, route.vehicle
    driver, vehicle = _lock_assignees(actor, route, driver_id, vehicle_id)
    excess = _capacity_excess(route, vehicle)
    if excess:
        raise DomainError(ErrorCode.ASSIGNMENT_OVER_CAPACITY, detail=excess)
    route.driver, route.vehicle = driver, vehicle
    route.status = RouteStatus.DISPATCHED
    route.started_at = None
    route.capacity_override_reason = ""
    _bump(route, "driver", "vehicle", "status", "started_at", "capacity_override_reason")
    _audit(
        AuditAction.ROUTE_REASSIGNED,
        actor,
        route,
        request,
        {
            "status": route.status,
            "driver": str(driver.public_id),
            "vehicle": str(vehicle.public_id),
            "previous_driver": str(previous_driver.public_id) if previous_driver else "",
            "previous_vehicle": str(previous_vehicle.public_id) if previous_vehicle else "",
        },
    )
    return route


@transaction.atomic
def start_route(*, actor: User, public_id: uuid.UUID, request: HttpRequest) -> Route:
    """The assigned driver starts a dispatched route (needs an open shift, and the route's
    date must have come). Shipments with a delivery stop that were already at the warehouse
    become `out_for_delivery`; those still to be picked up stay `assigned` until the
    delivery workflow confirms the pickup (Phase 5, memory D-API-32). Repeating it on a
    started route is a no-op. The driver's own route answers 409 `route.not_dispatched` until
    it is dispatched, so it is looked up here rather than through the dispatched-only
    selector."""
    driver = fleet_selectors.own_driver(actor, actor.company.local_today())
    locked = (
        Route.objects.for_company(actor.company)
        .select_for_update(of=("self",))
        .filter(public_id=public_id, driver=driver)
        .first()
    )
    if locked is None:  # not theirs, not another company's, or never assigned
        raise DomainError(ErrorCode.NOT_FOUND)
    if locked.status == RouteStatus.IN_PROGRESS:
        return locked
    if locked.status != RouteStatus.DISPATCHED:
        raise DomainError(ErrorCode.ROUTE_NOT_DISPATCHED)
    if locked.route_date > actor.company.local_today():
        raise DomainError(ErrorCode.ROUTE_NOT_DUE)
    if fleet_selectors.active_shift(driver) is None:
        raise DomainError(ErrorCode.SHIFT_NOT_OPEN)
    route_shipments = Shipment.objects.for_company(actor.company).filter(
        pk__in=RouteStop.objects.filter(route=locked).values("shipment")
    )
    for stop in (
        RouteStop.objects.filter(
            route=locked,
            type=StopType.DELIVERY,
            shipment_status_before=ShipmentStatus.SORTED,
            shipment__status=ShipmentStatus.ASSIGNED,  # skip one an admin has since corrected
        )
        .select_related("shipment")
        .order_by("sequence")
    ):
        shipment_services.transition(
            actor=actor,
            shipment=stop.shipment,
            to=ShipmentStatus.OUT_FOR_DELIVERY,
            expected_status=ShipmentStatus.ASSIGNED,
            reason="Route started",
            request=request,
            authorized_by_caller=True,
            source=EventSource.DRIVER_APP,
            scope=route_shipments,
        )
    locked.status = RouteStatus.IN_PROGRESS
    locked.started_at = timezone.now()
    _bump(locked, "status", "started_at")
    return locked
