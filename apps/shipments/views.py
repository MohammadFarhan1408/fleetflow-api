import uuid
from dataclasses import replace

from django.db.models import QuerySet
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.authentication import current_user
from apps.accounts.permissions import HasRequiredPermission
from apps.core.idempotency import IDEMPOTENCY_KEY_HEADER, idempotency_required, idempotent
from apps.core.pagination import PublicIdCursorPagination
from apps.core.serializers import ErrorSerializer
from apps.shipments import selectors, services
from apps.shipments.models import Shipment, ShipmentStatus, TrackingEvent
from apps.shipments.permissions import SHIPMENTS_CORRECT_STATUS, SHIPMENTS_CREATE, SHIPMENTS_VIEW
from apps.shipments.serializers import (
    CorrectionSerializer,
    PublicTrackingSerializer,
    ShipmentCreateSerializer,
    ShipmentDetailSerializer,
    ShipmentListQuerySerializer,
    ShipmentListSerializer,
    ShipmentWriteSerializer,
    TrackingEventSerializer,
    TrackingTokenSerializer,
    TransitionSerializer,
)
from apps.shipments.throttles import PublicTrackingThrottle

ERROR = OpenApiResponse(ErrorSerializer)
READ_ERRORS = {401: ERROR, 403: ERROR, 404: ERROR}
WRITE_ERRORS = {400: ERROR, 401: ERROR, 403: ERROR, 404: ERROR, 409: ERROR, 422: ERROR}
REQUIRED_KEY = OpenApiParameter(
    IDEMPOTENCY_KEY_HEADER,
    location=OpenApiParameter.HEADER,
    required=True,
    description="Client-generated key (e.g. a UUID); retries with it replay the result.",
)


class ShipmentAccess(APIView):
    # Reads need shipments.view; writes (create, draft edit/delete, token rotation) need
    # shipments.create. Branch scope is applied by the selectors and services.
    permission_classes = [IsAuthenticated, HasRequiredPermission]
    required_permissions = {
        "GET": SHIPMENTS_VIEW,
        "POST": SHIPMENTS_CREATE,
        "PATCH": SHIPMENTS_CREATE,
        "DELETE": SHIPMENTS_CREATE,
    }


def _detail(
    request: Request, shipment: Shipment, status_code: int = status.HTTP_200_OK
) -> Response:
    # Re-read through the selector so the response has the same shape and query count as GET.
    shipment = selectors.get_shipment(current_user(request), shipment.public_id)
    return Response(ShipmentDetailSerializer(shipment).data, status=status_code)


@extend_schema_view(get=extend_schema(parameters=[ShipmentListQuerySerializer]))
class ShipmentListCreateView(ShipmentAccess, ListAPIView[Shipment]):
    """List shipments in the user's branches (`shipments.view`), newest first, or create one
    (`shipments.create`, `Idempotency-Key` required)."""

    serializer_class = ShipmentListSerializer

    def get_queryset(self) -> QuerySet[Shipment]:
        query = ShipmentListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        return selectors.list_shipments(current_user(self.request), **query.validated_data)

    @extend_schema(
        request=ShipmentCreateSerializer,
        parameters=[REQUIRED_KEY],
        responses={201: ShipmentDetailSerializer, **WRITE_ERRORS},
    )
    @idempotency_required
    def post(self, request: Request) -> Response:
        serializer = ShipmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        draft = serializer.validated_data.pop("draft")
        shipment = services.create_shipment(
            actor=current_user(request),
            fields=services.ShipmentFields(**serializer.validated_data),
            draft=draft,
            request=request,
        )
        return _detail(request, shipment, status.HTTP_201_CREATED)


class ShipmentDetailView(ShipmentAccess):
    """Read a shipment (`shipments.view`). Drafts can be edited or deleted
    (`shipments.create`); any other status answers 409 `shipment.not_draft`."""

    @extend_schema(responses={200: ShipmentDetailSerializer, **READ_ERRORS})
    def get(self, request: Request, public_id: uuid.UUID) -> Response:
        shipment = selectors.get_shipment(current_user(request), public_id)
        return Response(ShipmentDetailSerializer(shipment).data)

    @extend_schema(
        request=ShipmentWriteSerializer(partial=True),
        responses={200: ShipmentDetailSerializer, **WRITE_ERRORS},
    )
    @idempotent
    def patch(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        shipment = selectors.get_shipment(actor, public_id)
        serializer = ShipmentWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = replace(services.ShipmentFields.of(shipment), **serializer.validated_data)
        shipment = services.update_draft(actor=actor, shipment=shipment, fields=fields)
        return _detail(request, shipment)

    @extend_schema(responses={204: None, **READ_ERRORS, 409: ERROR})
    def delete(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        shipment = selectors.get_shipment(actor, public_id)
        services.delete_draft(actor=actor, shipment=shipment, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ShipmentTrackingTokenView(ShipmentAccess):
    """Rotate the public tracking token (`shipments.create`). The old token stops working."""

    @extend_schema(request=None, responses={200: TrackingTokenSerializer, **READ_ERRORS})
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        shipment = selectors.get_shipment(actor, public_id)
        shipment = services.rotate_tracking_token(actor=actor, shipment=shipment, request=request)
        return Response(TrackingTokenSerializer(shipment).data)


class ShipmentTransitionView(APIView):
    """Confirm a draft, cancel, or recover a cancelled shipment. Any user who can see the
    shipment may ask; the service checks the edge's permission (`shipments.create`,
    `shipments.cancel` or `shipments.recover`) and answers 403 otherwise."""

    permission_classes = [IsAuthenticated, HasRequiredPermission]
    required_permissions = {"POST": SHIPMENTS_VIEW}

    @extend_schema(
        request=TransitionSerializer, responses={200: ShipmentDetailSerializer, **WRITE_ERRORS}
    )
    @idempotent
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        shipment = selectors.get_shipment(actor, public_id)
        serializer = TransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shipment = services.transition(
            actor=actor,
            shipment=shipment,
            to=ShipmentStatus(serializer.validated_data["to"]),
            expected_status=ShipmentStatus(serializer.validated_data["expected_status"]),
            reason=serializer.validated_data["reason"],
            request=request,
        )
        return _detail(request, shipment)


class ShipmentCorrectionView(APIView):
    """Admin status correction (`shipments.correct_status`): any live status, reason
    required, audited. Appends a non-public correction event."""

    permission_classes = [IsAuthenticated, HasRequiredPermission]
    required_permissions = {"POST": SHIPMENTS_CORRECT_STATUS}

    @extend_schema(
        request=CorrectionSerializer, responses={200: ShipmentDetailSerializer, **WRITE_ERRORS}
    )
    @idempotent
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        shipment = selectors.get_shipment(actor, public_id)
        serializer = CorrectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shipment = services.correct_status(
            actor=actor,
            shipment=shipment,
            to=ShipmentStatus(serializer.validated_data["to"]),
            expected_status=ShipmentStatus(serializer.validated_data["expected_status"]),
            reason=serializer.validated_data["reason"],
            request=request,
        )
        return _detail(request, shipment)


class TimelinePagination(PublicIdCursorPagination):
    # Chronological by device time, then server time (architecture §12).
    ordering = ("occurred_at", "recorded_at", "pk")


class ShipmentTimelineView(ListAPIView[TrackingEvent]):
    """A shipment's full history, internal events included (`shipments.view`)."""

    permission_classes = [IsAuthenticated, HasRequiredPermission]
    required_permissions = {"GET": SHIPMENTS_VIEW}
    serializer_class = TrackingEventSerializer
    pagination_class = TimelinePagination

    def get_queryset(self) -> QuerySet[TrackingEvent]:
        return selectors.timeline(current_user(self.request), self.kwargs["public_id"])


class PublicTrackingView(APIView):
    """Public tracking by token, no login (architecture §7). Only whitelisted fields and
    public events; unknown, rotated and draft tokens answer 404."""

    # Credentials are ignored, so a stale Authorization header cannot turn this into a 401.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicTrackingThrottle]

    @extend_schema(auth=[], responses={200: PublicTrackingSerializer, 404: ERROR, 429: ERROR})
    def get(self, request: Request, token: str) -> Response:
        return Response(PublicTrackingSerializer(selectors.get_public_shipment(token)).data)
