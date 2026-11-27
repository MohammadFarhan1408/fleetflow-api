import uuid
from dataclasses import replace
from datetime import date
from typing import Any

from django.db.models import QuerySet
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.authentication import current_user
from apps.accounts.models import User
from apps.accounts.permissions import HasRequiredPermission, get_user_permissions
from apps.core.idempotency import idempotency_required, idempotent
from apps.core.serializers import ErrorSerializer
from apps.fleet import selectors, services
from apps.fleet.models import Driver, DriverDocument, DriverLeave, Vehicle, VehicleDocument
from apps.fleet.permissions import FLEET_MANAGE, FLEET_VIEW, SHIFTS_MANAGE_OWN
from apps.fleet.serializers import (
    AvailabilityDateQuerySerializer,
    DownloadSerializer,
    DriverCreateSerializer,
    DriverDocumentListQuerySerializer,
    DriverDocumentSerializer,
    DriverDocumentWriteSerializer,
    DriverListQuerySerializer,
    DriverSerializer,
    DriverStatusSerializer,
    DriverUpdateSerializer,
    LeaveSerializer,
    LeaveWriteSerializer,
    MyDriverSerializer,
    ShiftSerializer,
    ShiftTimeSerializer,
    VehicleDocumentListQuerySerializer,
    VehicleDocumentSerializer,
    VehicleDocumentWriteSerializer,
    VehicleListQuerySerializer,
    VehicleSerializer,
    VehicleStatusChangeSerializer,
    VehicleWriteSerializer,
)

ERROR = OpenApiResponse(ErrorSerializer)
READ_ERRORS = {401: ERROR, 403: ERROR, 404: ERROR}
WRITE_ERRORS = {400: ERROR, 401: ERROR, 403: ERROR, 404: ERROR, 409: ERROR, 422: ERROR}


class FleetAccess(APIView):
    # Reads need fleet.view; every write needs fleet.manage. Company and branch scope are
    # applied by the selectors and re-checked by the services.
    permission_classes = [IsAuthenticated, HasRequiredPermission]
    required_permissions = {
        "GET": FLEET_VIEW,
        "POST": FLEET_MANAGE,
        "PATCH": FLEET_MANAGE,
        "DELETE": FLEET_MANAGE,
    }


def _on_date(request: Request, user: User) -> date:
    """The date availability is evaluated for: `availability_date`, else today in the
    company's timezone."""
    query = AvailabilityDateQuerySerializer(data=request.query_params)
    query.is_valid(raise_exception=True)
    on_date: date | None = query.validated_data.get("availability_date")
    return on_date or user.company.local_today()


def _driver_response(request: Request, driver: Driver, *, http_status: int = 200) -> Response:
    """Serialize `driver` as of the requested date (re-read, so it carries the leave
    annotation the availability needs)."""
    user = current_user(request)
    on_date = _on_date(request, user)
    fresh = selectors.get_driver(user, driver.public_id, on_date)
    return Response(DriverSerializer(fresh, context={"on_date": on_date}).data, status=http_status)


def _vehicle_response(request: Request, vehicle: Vehicle, *, http_status: int = 200) -> Response:
    user = current_user(request)
    on_date = _on_date(request, user)
    fresh = selectors.get_vehicle(user, vehicle.public_id, on_date)
    return Response(VehicleSerializer(fresh, context={"on_date": on_date}).data, status=http_status)


# --- drivers -------------------------------------------------------------------------------


@extend_schema_view(get=extend_schema(parameters=[DriverListQuerySerializer]))
class DriverListCreateView(FleetAccess, ListAPIView[Driver]):
    """List drivers (`fleet.view`) or create a driver profile for an existing user with the
    `driver` role (`fleet.manage`). Users see drivers of their own branches unless they
    hold `fleet.all_branches`."""

    serializer_class = DriverSerializer

    def get_queryset(self) -> QuerySet[Driver]:
        query = DriverListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        user = current_user(self.request)
        filters = dict(query.validated_data)
        on_date = filters.pop("availability_date", None) or user.company.local_today()
        return selectors.list_drivers(user, on_date=on_date, **filters)

    def get_serializer_context(self) -> dict[str, Any]:
        user = current_user(self.request)
        return {**super().get_serializer_context(), "on_date": _on_date(self.request, user)}

    @extend_schema(
        request=DriverCreateSerializer, responses={201: DriverSerializer, **WRITE_ERRORS}
    )
    @idempotent
    def post(self, request: Request) -> Response:
        serializer = DriverCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        driver = services.create_driver(
            actor=current_user(request),
            user_id=data.pop("user_id"),
            fields=services.DriverFields(**data),
            request=request,
        )
        return _driver_response(request, driver, http_status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        parameters=[AvailabilityDateQuerySerializer],
        responses={200: DriverSerializer, **READ_ERRORS},
    )
)
class DriverDetailView(FleetAccess):
    """Read (`fleet.view`) or update (`fleet.manage`) one driver. Archived drivers can be
    read but not changed (409 `driver.archived`)."""

    def get(self, request: Request, public_id: uuid.UUID) -> Response:
        user = current_user(request)
        on_date = _on_date(request, user)
        driver = selectors.get_driver(user, public_id, on_date)
        return Response(DriverSerializer(driver, context={"on_date": on_date}).data)

    @extend_schema(
        request=DriverUpdateSerializer(partial=True),
        parameters=[AvailabilityDateQuerySerializer],
        responses={200: DriverSerializer, **WRITE_ERRORS},
    )
    @idempotent
    def patch(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        driver = selectors.get_driver(actor, public_id, actor.company.local_today())
        serializer = DriverUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = replace(services.DriverFields.of(driver), **serializer.validated_data)
        driver = services.update_driver(actor=actor, driver=driver, fields=fields)
        return _driver_response(request, driver)


class DriverStatusView(FleetAccess):
    """Suspend or reinstate a driver (`fleet.manage`). A reason is required unless the
    status is `active`. Repeating the same change is a no-op."""

    @extend_schema(
        request=DriverStatusSerializer,
        parameters=[AvailabilityDateQuerySerializer],
        responses={200: DriverSerializer, **WRITE_ERRORS},
    )
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        driver = selectors.get_driver(actor, public_id, actor.company.local_today())
        serializer = DriverStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        driver = services.set_driver_status(
            actor=actor, driver=driver, request=request, **serializer.validated_data
        )
        return _driver_response(request, driver)


class DriverArchiveView(FleetAccess):
    """Archive a driver (`fleet.manage`). Repeating it is a no-op."""

    @extend_schema(
        request=None,
        parameters=[AvailabilityDateQuerySerializer],
        responses={200: DriverSerializer, **READ_ERRORS},
    )
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        driver = selectors.get_driver(actor, public_id, actor.company.local_today())
        driver = services.archive_driver(actor=actor, driver=driver, request=request)
        return _driver_response(request, driver)


class DriverUnarchiveView(FleetAccess):
    """Unarchive a driver (`fleet.manage`). Repeating it is a no-op."""

    @extend_schema(
        request=None,
        parameters=[AvailabilityDateQuerySerializer],
        responses={200: DriverSerializer, **READ_ERRORS},
    )
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        driver = selectors.get_driver(actor, public_id, actor.company.local_today())
        driver = services.unarchive_driver(actor=actor, driver=driver, request=request)
        return _driver_response(request, driver)


class DriverLeaveListCreateView(FleetAccess, ListAPIView[DriverLeave]):
    """List a driver's leave (`fleet.view`) or add a period (`fleet.manage`). Both days are
    included; periods of one driver cannot overlap (409 `driver.leave_overlap`)."""

    serializer_class = LeaveSerializer

    def get_queryset(self) -> QuerySet[DriverLeave]:
        user = current_user(self.request)
        driver = selectors.get_driver(user, self.kwargs["driver_id"], user.company.local_today())
        return selectors.leaves_of(driver)

    @extend_schema(request=LeaveWriteSerializer, responses={201: LeaveSerializer, **WRITE_ERRORS})
    @idempotent
    def post(self, request: Request, driver_id: uuid.UUID) -> Response:
        actor = current_user(request)
        driver = selectors.get_driver(actor, driver_id, actor.company.local_today())
        serializer = LeaveWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        leave = services.add_leave(
            actor=actor, driver=driver, request=request, **serializer.validated_data
        )
        return Response(LeaveSerializer(leave).data, status=status.HTTP_201_CREATED)


class DriverLeaveDetailView(FleetAccess):
    """Delete leave that has not started (`fleet.manage`); started leave is history
    (409 `driver.leave_started`)."""

    @extend_schema(request=None, responses={204: None, **READ_ERRORS, 409: ERROR})
    def delete(self, request: Request, driver_id: uuid.UUID, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        driver = selectors.get_driver(actor, driver_id, actor.company.local_today())
        leave = selectors.get_leave(driver, public_id)
        services.delete_leave(actor=actor, driver=driver, leave=leave, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- vehicles ------------------------------------------------------------------------------


@extend_schema_view(get=extend_schema(parameters=[VehicleListQuerySerializer]))
class VehicleListCreateView(FleetAccess, ListAPIView[Vehicle]):
    """List vehicles (`fleet.view`) or create one (`fleet.manage`). Users see vehicles of
    their own branches unless they hold `fleet.all_branches`."""

    serializer_class = VehicleSerializer

    def get_queryset(self) -> QuerySet[Vehicle]:
        query = VehicleListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        user = current_user(self.request)
        filters = dict(query.validated_data)
        on_date = filters.pop("availability_date", None) or user.company.local_today()
        return selectors.list_vehicles(user, on_date=on_date, **filters)

    def get_serializer_context(self) -> dict[str, Any]:
        user = current_user(self.request)
        return {**super().get_serializer_context(), "on_date": _on_date(self.request, user)}

    @extend_schema(
        request=VehicleWriteSerializer,
        parameters=[AvailabilityDateQuerySerializer],
        responses={201: VehicleSerializer, **WRITE_ERRORS},
    )
    @idempotent
    def post(self, request: Request) -> Response:
        serializer = VehicleWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vehicle = services.create_vehicle(
            actor=current_user(request),
            fields=services.VehicleFields(**serializer.validated_data),
            request=request,
        )
        return _vehicle_response(request, vehicle, http_status=status.HTTP_201_CREATED)


class VehicleDetailView(FleetAccess):
    """Read (`fleet.view`) or update (`fleet.manage`) one vehicle. Archived vehicles can be
    read but not changed (409 `vehicle.archived`)."""

    @extend_schema(
        parameters=[AvailabilityDateQuerySerializer],
        responses={200: VehicleSerializer, **READ_ERRORS},
    )
    def get(self, request: Request, public_id: uuid.UUID) -> Response:
        user = current_user(request)
        on_date = _on_date(request, user)
        vehicle = selectors.get_vehicle(user, public_id, on_date)
        return Response(VehicleSerializer(vehicle, context={"on_date": on_date}).data)

    @extend_schema(
        request=VehicleWriteSerializer(partial=True),
        parameters=[AvailabilityDateQuerySerializer],
        responses={200: VehicleSerializer, **WRITE_ERRORS},
    )
    @idempotent
    def patch(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        vehicle = selectors.get_vehicle(actor, public_id, actor.company.local_today())
        serializer = VehicleWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = replace(services.VehicleFields.of(vehicle), **serializer.validated_data)
        vehicle = services.update_vehicle(actor=actor, vehicle=vehicle, fields=fields)
        return _vehicle_response(request, vehicle)


class VehicleStatusView(FleetAccess):
    """Take a vehicle out of service or into maintenance, or put it back (`fleet.manage`).
    A reason is required unless the status is `active`."""

    @extend_schema(
        request=VehicleStatusChangeSerializer,
        parameters=[AvailabilityDateQuerySerializer],
        responses={200: VehicleSerializer, **WRITE_ERRORS},
    )
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        vehicle = selectors.get_vehicle(actor, public_id, actor.company.local_today())
        serializer = VehicleStatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vehicle = services.set_vehicle_status(
            actor=actor, vehicle=vehicle, request=request, **serializer.validated_data
        )
        return _vehicle_response(request, vehicle)


class VehicleArchiveView(FleetAccess):
    """Archive a vehicle (`fleet.manage`). Repeating it is a no-op."""

    @extend_schema(
        request=None,
        parameters=[AvailabilityDateQuerySerializer],
        responses={200: VehicleSerializer, **READ_ERRORS},
    )
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        vehicle = selectors.get_vehicle(actor, public_id, actor.company.local_today())
        vehicle = services.archive_vehicle(actor=actor, vehicle=vehicle, request=request)
        return _vehicle_response(request, vehicle)


# --- documents -----------------------------------------------------------------------------
# Four views per owner type. Each finds the owner through the scoped selector, then the
# document through the owner, before it reads the body (a missing or foreign ID is 404).
# Reads need `fleet.view`; every POST, the download included, needs `fleet.manage`, because
# licence scans are personal data (memory D-API-27).


def _document_context(request: Request) -> dict[str, Any]:
    user = current_user(request)
    return {
        "today": user.company.local_today(),
        "show_number": FLEET_MANAGE in get_user_permissions(user),
    }


def _driver_of(request: Request, driver_id: uuid.UUID) -> Driver:
    user = current_user(request)
    return selectors.get_driver(user, driver_id, user.company.local_today())


def _vehicle_of(request: Request, vehicle_id: uuid.UUID) -> Vehicle:
    user = current_user(request)
    return selectors.get_vehicle(user, vehicle_id, user.company.local_today())


def _download_response(request: Request, document: DriverDocument | VehicleDocument) -> Response:
    url, expires_at = services.download_document(
        actor=current_user(request), document=document, request=request
    )
    return Response(DownloadSerializer({"url": url, "expires_at": expires_at}).data)


@extend_schema_view(get=extend_schema(parameters=[DriverDocumentListQuerySerializer]))
class DriverDocumentListCreateView(FleetAccess, ListAPIView[DriverDocument]):
    """List a driver's documents (`fleet.view`; the number only for `fleet.manage`) or add
    one (`fleet.manage`). The file must be a confirmed `fleet_document` upload of yours."""

    serializer_class = DriverDocumentSerializer

    def get_queryset(self) -> QuerySet[DriverDocument]:
        query = DriverDocumentListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        driver = _driver_of(self.request, self.kwargs["driver_id"])
        return selectors.list_documents(selectors.driver_documents(driver), **query.validated_data)

    def get_serializer_context(self) -> dict[str, Any]:
        return {**super().get_serializer_context(), **_document_context(self.request)}

    @extend_schema(
        request=DriverDocumentWriteSerializer,
        responses={201: DriverDocumentSerializer, **WRITE_ERRORS},
    )
    @idempotent
    def post(self, request: Request, driver_id: uuid.UUID) -> Response:
        driver = _driver_of(request, driver_id)
        serializer = DriverDocumentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = services.add_driver_document(
            actor=current_user(request),
            driver=driver,
            fields=services.DocumentFields(**serializer.validated_data),
            request=request,
        )
        data = DriverDocumentSerializer(document, context=_document_context(request)).data
        return Response(data, status=status.HTTP_201_CREATED)


class DriverDocumentDetailView(FleetAccess):
    @extend_schema(responses={200: DriverDocumentSerializer, **READ_ERRORS})
    def get(self, request: Request, driver_id: uuid.UUID, public_id: uuid.UUID) -> Response:
        driver = _driver_of(request, driver_id)
        document = selectors.get_document(selectors.driver_documents(driver), public_id)
        return Response(DriverDocumentSerializer(document, context=_document_context(request)).data)


class DriverDocumentArchiveView(FleetAccess):
    """Archive a document (`fleet.manage`); it stops counting for availability. Repeating
    it is a no-op."""

    @extend_schema(request=None, responses={200: DriverDocumentSerializer, **READ_ERRORS})
    def post(self, request: Request, driver_id: uuid.UUID, public_id: uuid.UUID) -> Response:
        driver = _driver_of(request, driver_id)
        document = selectors.get_document(selectors.driver_documents(driver), public_id)
        document = services.archive_document(
            actor=current_user(request), document=document, request=request
        )
        return Response(DriverDocumentSerializer(document, context=_document_context(request)).data)


class DriverDocumentDownloadView(FleetAccess):
    """A 5-minute download URL for the document's file (`fleet.manage` only: licence scans
    are personal data). Each call is audited."""

    @extend_schema(request=None, responses={200: DownloadSerializer, **READ_ERRORS, 409: ERROR})
    def post(self, request: Request, driver_id: uuid.UUID, public_id: uuid.UUID) -> Response:
        driver = _driver_of(request, driver_id)
        document = selectors.get_document(selectors.driver_documents(driver), public_id)
        return _download_response(request, document)


@extend_schema_view(get=extend_schema(parameters=[VehicleDocumentListQuerySerializer]))
class VehicleDocumentListCreateView(FleetAccess, ListAPIView[VehicleDocument]):
    """List a vehicle's documents (`fleet.view`; the number only for `fleet.manage`) or add
    one (`fleet.manage`). The file must be a confirmed `fleet_document` upload of yours."""

    serializer_class = VehicleDocumentSerializer

    def get_queryset(self) -> QuerySet[VehicleDocument]:
        query = VehicleDocumentListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        vehicle = _vehicle_of(self.request, self.kwargs["vehicle_id"])
        return selectors.list_documents(
            selectors.vehicle_documents(vehicle), **query.validated_data
        )

    def get_serializer_context(self) -> dict[str, Any]:
        return {**super().get_serializer_context(), **_document_context(self.request)}

    @extend_schema(
        request=VehicleDocumentWriteSerializer,
        responses={201: VehicleDocumentSerializer, **WRITE_ERRORS},
    )
    @idempotent
    def post(self, request: Request, vehicle_id: uuid.UUID) -> Response:
        vehicle = _vehicle_of(request, vehicle_id)
        serializer = VehicleDocumentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = services.add_vehicle_document(
            actor=current_user(request),
            vehicle=vehicle,
            fields=services.DocumentFields(**serializer.validated_data),
            request=request,
        )
        data = VehicleDocumentSerializer(document, context=_document_context(request)).data
        return Response(data, status=status.HTTP_201_CREATED)


class VehicleDocumentDetailView(FleetAccess):
    @extend_schema(responses={200: VehicleDocumentSerializer, **READ_ERRORS})
    def get(self, request: Request, vehicle_id: uuid.UUID, public_id: uuid.UUID) -> Response:
        vehicle = _vehicle_of(request, vehicle_id)
        document = selectors.get_document(selectors.vehicle_documents(vehicle), public_id)
        return Response(
            VehicleDocumentSerializer(document, context=_document_context(request)).data
        )


class VehicleDocumentArchiveView(FleetAccess):
    """Archive a document (`fleet.manage`); it stops counting for availability. Repeating
    it is a no-op."""

    @extend_schema(request=None, responses={200: VehicleDocumentSerializer, **READ_ERRORS})
    def post(self, request: Request, vehicle_id: uuid.UUID, public_id: uuid.UUID) -> Response:
        vehicle = _vehicle_of(request, vehicle_id)
        document = selectors.get_document(selectors.vehicle_documents(vehicle), public_id)
        document = services.archive_document(
            actor=current_user(request), document=document, request=request
        )
        return Response(
            VehicleDocumentSerializer(document, context=_document_context(request)).data
        )


class VehicleDocumentDownloadView(FleetAccess):
    """A 5-minute download URL for the document's file (`fleet.manage` only). Audited."""

    @extend_schema(request=None, responses={200: DownloadSerializer, **READ_ERRORS, 409: ERROR})
    def post(self, request: Request, vehicle_id: uuid.UUID, public_id: uuid.UUID) -> Response:
        vehicle = _vehicle_of(request, vehicle_id)
        document = selectors.get_document(selectors.vehicle_documents(vehicle), public_id)
        return _download_response(request, document)


# --- the signed-in driver ------------------------------------------------------------------


class DriverSelfAccess(APIView):
    """Endpoints for the `driver` role acting on their own profile. Without an active
    profile they answer 403 `driver.profile_inactive`."""

    permission_classes = [IsAuthenticated, HasRequiredPermission]
    required_permissions = {"GET": SHIFTS_MANAGE_OWN, "POST": SHIFTS_MANAGE_OWN}


class MyDriverView(DriverSelfAccess):
    """The signed-in driver's profile, today's availability and the open shift."""

    @extend_schema(responses={200: MyDriverSerializer, 401: ERROR, 403: ERROR})
    def get(self, request: Request) -> Response:
        user = current_user(request)
        on_date = user.company.local_today()
        driver = selectors.own_driver(user, on_date)
        return Response(MyDriverSerializer(driver, context={"on_date": on_date}).data)


class MyShiftStartView(DriverSelfAccess):
    """Start the signed-in driver's shift. `Idempotency-Key` is required: the app queues
    this offline and replays it. A suspended driver gets 409 `driver.suspended`; a second
    open shift 409 `shift.already_open`."""

    @extend_schema(
        request=ShiftTimeSerializer,
        responses={
            201: ShiftSerializer,
            400: ERROR,
            401: ERROR,
            403: ERROR,
            409: ERROR,
            422: ERROR,
        },
    )
    @idempotency_required
    def post(self, request: Request) -> Response:
        actor = current_user(request)
        selectors.own_driver(actor, actor.company.local_today())  # 403 before body errors
        serializer = ShiftTimeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shift = services.start_shift(
            actor=actor, occurred_at=serializer.validated_data.get("occurred_at")
        )
        return Response(ShiftSerializer(shift).data, status=status.HTTP_201_CREATED)


class MyShiftEndView(DriverSelfAccess):
    """End the signed-in driver's open shift (409 `shift.not_open` if none). Needs an
    `Idempotency-Key`."""

    @extend_schema(
        request=ShiftTimeSerializer,
        responses={
            200: ShiftSerializer,
            400: ERROR,
            401: ERROR,
            403: ERROR,
            409: ERROR,
            422: ERROR,
        },
    )
    @idempotency_required
    def post(self, request: Request) -> Response:
        actor = current_user(request)
        selectors.own_driver(actor, actor.company.local_today())
        serializer = ShiftTimeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shift = services.end_shift(
            actor=actor, occurred_at=serializer.validated_data.get("occurred_at")
        )
        return Response(ShiftSerializer(shift).data)
