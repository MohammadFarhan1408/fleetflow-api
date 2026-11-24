import uuid

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.accounts.authentication import current_user
from apps.accounts.permissions import HasRequiredPermission
from apps.core.serializers import ErrorSerializer
from apps.uploads import services
from apps.uploads.models import UploadContentType, UploadPurpose
from apps.uploads.permissions import UPLOADS_CREATE
from apps.uploads.serializers import (
    UploadCreateSerializer,
    UploadSerializer,
    UploadTicketSerializer,
)
from apps.uploads.throttles import UploadCreateThrottle

ERROR = OpenApiResponse(ErrorSerializer)


class UploadAccess(APIView):
    permission_classes = [IsAuthenticated, HasRequiredPermission]
    required_permissions = {"POST": UPLOADS_CREATE}


class UploadCreateView(UploadAccess):
    """Start an upload (`uploads.create`): returns a presigned URL for one PUT of exactly the
    declared size and type. Confirm afterwards. Sizes over the limit for the type get 400
    `upload.too_large`."""

    # Replaces the defaults, so the general per-user limit is listed too.
    throttle_classes = [UserRateThrottle, UploadCreateThrottle]

    @extend_schema(
        request=UploadCreateSerializer,
        responses={
            201: UploadTicketSerializer,
            400: ERROR,
            401: ERROR,
            403: ERROR,
            429: ERROR,
            503: ERROR,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = UploadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = services.create_upload(
            actor=current_user(request),
            purpose=UploadPurpose(serializer.validated_data["purpose"]),
            content_type=UploadContentType(serializer.validated_data["content_type"]),
            size=serializer.validated_data["size"],
        )
        body = {
            "id": ticket.upload.public_id,
            "upload_url": ticket.url,
            "method": "PUT",
            "headers": ticket.headers,
            "expires_at": ticket.expires_at,
        }
        return Response(UploadTicketSerializer(body).data, status=status.HTTP_201_CREATED)


class UploadConfirmView(UploadAccess):
    """Verify the uploaded file (`uploads.create`; only the uploader's own uploads). The
    file's size and magic bytes are checked and image metadata (EXIF, XMP, IPTC) is
    removed. A rejected file is deleted and the upload must be started again. Repeating a
    successful confirm returns the same result."""

    @extend_schema(
        request=None,
        responses={
            200: UploadSerializer,
            400: ERROR,
            401: ERROR,
            403: ERROR,
            404: ERROR,
            503: ERROR,
        },
    )
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        upload = services.confirm_upload(
            actor=current_user(request), upload_id=public_id, request=request
        )
        return Response(UploadSerializer(upload).data)
