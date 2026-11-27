import uuid

from django.db.models import QuerySet
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.authentication import current_user
from apps.accounts.permissions import HasRequiredPermission
from apps.core.idempotency import idempotency_required, idempotent
from apps.core.serializers import ErrorSerializer
from apps.dispatch import selectors, services
from apps.dispatch.models import Route
from apps.dispatch.permissions import DISPATCH_MANAGE, DISPATCH_VIEW, ROUTES_WORK_OWN
from apps.dispatch.serializers import (
    AssignSerializer,
    DriverRouteDetailSerializer,
    DriverRouteListQuerySerializer,
    DriverRouteSerializer,
    ReassignSerializer,
    ReorderSerializer,
    RouteCreateSerializer,
    RouteDetailSerializer,
    RouteListQuerySerializer,
    RouteSerializer,
    RouteUpdateSerializer,
    RouteVersionSerializer,
    StopAddSerializer,
)

ERROR = OpenApiResponse(ErrorSerializer)
READ_ERRORS = {401: ERROR, 403: ERROR, 404: ERROR}
WRITE_ERRORS = {400: ERROR, 401: ERROR, 403: ERROR, 404: ERROR, 409: ERROR, 422: ERROR}


class DispatchAccess(APIView):
    # Reads need dispatch.view; every write needs dispatch.manage. Company and branch scope
    # are applied by the selectors and re-checked by the services.
    permission_classes = [IsAuthenticated, HasRequiredPermission]
    required_permissions = {
        "GET": DISPATCH_VIEW,
        "POST": DISPATCH_MANAGE,
        "PATCH": DISPATCH_MANAGE,
        "DELETE": DISPATCH_MANAGE,
    }


def _route_response(request: Request, route: Route, *, http_status: int = 200) -> Response:
    """The route as it is now, with its stops and new `version` (re-read, so it carries
    the annotations and prefetched stops)."""
    fresh = selectors.get_route(current_user(request), route.public_id)
    return Response(RouteDetailSerializer(fresh).data, status=http_status)


@extend_schema_view(get=extend_schema(parameters=[RouteListQuerySerializer]))
class RouteListCreateView(DispatchAccess, ListAPIView[Route]):
    """List routes (`dispatch.view`) or create a planned one (`dispatch.manage`). Users see
    routes of their own branches unless they hold `dispatch.all_branches`."""

    serializer_class = RouteSerializer

    def get_queryset(self) -> QuerySet[Route]:
        query = RouteListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        return selectors.list_routes(current_user(self.request), **query.validated_data)

    @extend_schema(
        request=RouteCreateSerializer, responses={201: RouteDetailSerializer, **WRITE_ERRORS}
    )
    @idempotent
    def post(self, request: Request) -> Response:
        serializer = RouteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        route = services.create_route(
            actor=current_user(request), request=request, **serializer.validated_data
        )
        return _route_response(request, route, http_status=status.HTTP_201_CREATED)


class RouteDetailView(DispatchAccess):
    """Read a route with its stops (`dispatch.view`), or change its date or sequencing
    flag while it is planned (`dispatch.manage`). Changes need the route's current
    `version`; a stale one is 409 `route.version_conflict`."""

    @extend_schema(responses={200: RouteDetailSerializer, **READ_ERRORS})
    def get(self, request: Request, public_id: uuid.UUID) -> Response:
        return Response(
            RouteDetailSerializer(selectors.get_route(current_user(request), public_id)).data
        )

    @extend_schema(
        request=RouteUpdateSerializer, responses={200: RouteDetailSerializer, **WRITE_ERRORS}
    )
    @idempotent
    def patch(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        route = selectors.get_route(actor, public_id)
        serializer = RouteUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        route = services.update_route(
            actor=actor,
            route=route,
            version=data["version"],
            route_date=data.get("route_date"),
            sequence_enforced=data.get("sequence_enforced"),
        )
        return _route_response(request, route)


class RouteStopsView(DispatchAccess):
    """Append a pickup or delivery stop (`dispatch.manage`). A shipment's first stop moves
    it to `assigned`. Returns the updated route."""

    @extend_schema(
        request=StopAddSerializer, responses={201: RouteDetailSerializer, **WRITE_ERRORS}
    )
    @idempotent
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        route = selectors.get_route(actor, public_id)
        serializer = StopAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.add_stop(actor=actor, route=route, request=request, **serializer.validated_data)
        return _route_response(request, route, http_status=status.HTTP_201_CREATED)


class RouteStopDetailView(DispatchAccess):
    """Remove a stop (`dispatch.manage`); `version` is a query parameter. When it was the
    shipment's last stop, the shipment returns to its status before the route."""

    @extend_schema(
        parameters=[RouteVersionSerializer], responses={200: RouteDetailSerializer, **WRITE_ERRORS}
    )
    def delete(self, request: Request, route_id: uuid.UUID, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        route = selectors.get_route(actor, route_id)
        stop = selectors.get_stop(route, public_id)
        query = RouteVersionSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        services.remove_stop(
            actor=actor,
            route=route,
            stop=stop,
            version=query.validated_data["version"],
            request=request,
        )
        return _route_response(request, route)


class RouteReorderView(DispatchAccess):
    """Set the stop order (`dispatch.manage`): every stop exactly once, a pickup before its
    delivery. Returns the updated route."""

    @extend_schema(
        request=ReorderSerializer, responses={200: RouteDetailSerializer, **WRITE_ERRORS}
    )
    @idempotent
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        route = selectors.get_route(actor, public_id)
        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.reorder_stops(actor=actor, route=route, **serializer.validated_data)
        return _route_response(request, route)


class RouteCancelView(DispatchAccess):
    """Cancel a route (`dispatch.manage`); its shipments return to their status before it.
    409 `route.stops_started` once a stop has been worked."""

    @extend_schema(
        request=RouteVersionSerializer, responses={200: RouteDetailSerializer, **WRITE_ERRORS}
    )
    @idempotent
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        route = selectors.get_route(actor, public_id)
        serializer = RouteVersionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.cancel_route(
            actor=actor, route=route, version=serializer.validated_data["version"], request=request
        )
        return _route_response(request, route)


class RouteAssignView(DispatchAccess):
    """Give a route a driver and a vehicle (`dispatch.manage`), checked for the route date:
    availability, licence, documents, one active route per date, branch and capacity. Over
    capacity needs `override_reason` and `dispatch.override_capacity`. Repeating the same
    assignment returns the route unchanged."""

    @extend_schema(request=AssignSerializer, responses={200: RouteDetailSerializer, **WRITE_ERRORS})
    @idempotent
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        route = selectors.get_route(actor, public_id)
        serializer = AssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.assign(actor=actor, route=route, request=request, **serializer.validated_data)
        return _route_response(request, route)


class RouteUnassignView(DispatchAccess):
    """Take the driver and vehicle off an `assigned` route, back to `planned`. Stops and
    shipment status stay. Repeating it on a planned route is a no-op."""

    @extend_schema(
        request=RouteVersionSerializer, responses={200: RouteDetailSerializer, **WRITE_ERRORS}
    )
    @idempotent
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        route = selectors.get_route(actor, public_id)
        serializer = RouteVersionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.unassign(
            actor=actor, route=route, version=serializer.validated_data["version"], request=request
        )
        return _route_response(request, route)


class RouteDispatchView(DispatchAccess):
    """Send an `assigned` route out (`dispatch.manage`). The driver, vehicle, licence and
    capacity are checked again for the route date, and the route's shipments are recorded as
    its manifest. 409 `route.empty` without stops. Repeating it returns the route unchanged."""

    @extend_schema(
        request=RouteVersionSerializer, responses={200: RouteDetailSerializer, **WRITE_ERRORS}
    )
    @idempotent
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        route = selectors.get_route(actor, public_id)
        serializer = RouteVersionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.dispatch_route(
            actor=actor, route=route, version=serializer.validated_data["version"], request=request
        )
        return _route_response(request, route)


class RouteReassignView(DispatchAccess):
    """Change the driver and vehicle of a `dispatched` or `in_progress` route
    (`dispatch.manage` and `dispatch.reassign_active`), while every stop is still pending.
    The route returns to `dispatched` for the new driver to start."""

    @extend_schema(
        request=ReassignSerializer, responses={200: RouteDetailSerializer, **WRITE_ERRORS}
    )
    @idempotent
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        route = selectors.get_route(actor, public_id)
        serializer = ReassignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.reassign(actor=actor, route=route, request=request, **serializer.validated_data)
        return _route_response(request, route)


# --- the signed-in driver ------------------------------------------------------------------


class DriverRouteAccess(APIView):
    """Endpoints for the `driver` role on their own routes (`routes.work_own`). Without an
    active profile they answer 403 `driver.profile_inactive`."""

    permission_classes = [IsAuthenticated, HasRequiredPermission]
    required_permissions = {"GET": ROUTES_WORK_OWN, "POST": ROUTES_WORK_OWN}


@extend_schema_view(
    get=extend_schema(
        parameters=[DriverRouteListQuerySerializer],
        responses={200: DriverRouteSerializer, 401: ERROR, 403: ERROR},
    )
)
class MyRouteListView(DriverRouteAccess, ListAPIView[Route]):
    """The driver's dispatched, in-progress and completed routes, newest first."""

    serializer_class = DriverRouteSerializer

    def get_queryset(self) -> QuerySet[Route]:
        query = DriverRouteListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        return selectors.list_own_routes(current_user(self.request), **query.validated_data)


class MyRouteDetailView(DriverRouteAccess):
    """One of the driver's own routes with its stops, in order."""

    @extend_schema(responses={200: DriverRouteDetailSerializer, **READ_ERRORS})
    def get(self, request: Request, public_id: uuid.UUID) -> Response:
        route = selectors.get_own_route(current_user(request), public_id)
        return Response(DriverRouteDetailSerializer(route).data)


class MyRouteStartView(DriverRouteAccess):
    """Start a dispatched route (needs an open shift; `Idempotency-Key` required). 409
    `route.not_dispatched`, `route.not_due` (before the route date) or `shift.not_open`.
    Repeating it on a started route returns it unchanged."""

    @extend_schema(request=None, responses={200: DriverRouteDetailSerializer, **WRITE_ERRORS})
    @idempotency_required
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        services.start_route(actor=actor, public_id=public_id, request=request)
        fresh = selectors.get_own_route(actor, public_id)
        return Response(DriverRouteDetailSerializer(fresh).data)
