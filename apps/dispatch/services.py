"""Route writes (architecture §5, §13). Views pass the acting user; every service re-locks
the route through the actor's company and branch scope (rules.md §3). Each change checks the
caller's `version` and bumps it, so concurrent dispatchers get 409 instead of overwriting."""

import uuid
from datetime import date, datetime

from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.selectors import branches_in_scope
from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.core.errors import DomainError, ErrorCode, invalid_reference
from apps.dispatch import selectors
from apps.dispatch.constants import MAX_STOPS_PER_ROUTE
from apps.dispatch.models import (
    CANCELLABLE_STATUSES,
    EDITABLE_STATUSES,
    Route,
    RouteStatus,
    RouteStop,
    StopStatus,
    StopType,
)
from apps.dispatch.permissions import DISPATCH_ALL_BRANCHES
from apps.shipments import selectors as shipment_selectors
from apps.shipments import services as shipment_services
from apps.shipments.models import Shipment, ShipmentStatus


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


def _lock_route(actor: User, route: Route, version: int) -> Route:
    locked = (
        selectors.routes_in_scope(actor).select_for_update(of=("self",)).filter(pk=route.pk).first()
    )
    if locked is None:
        raise DomainError(ErrorCode.NOT_FOUND)
    if locked.version != version:
        raise DomainError(
            ErrorCode.ROUTE_VERSION_CONFLICT,
            detail=f"The route is at version {locked.version}, not {version}.",
        )
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
