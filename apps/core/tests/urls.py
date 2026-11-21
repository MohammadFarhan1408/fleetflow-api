"""Test-only views that raise each error class, to verify the envelope end to end."""

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.urls import path
from rest_framework import exceptions, serializers
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import DomainError, ErrorCode
from apps.core.views import health_live


class EchoSerializer(serializers.Serializer[dict[str, int]]):
    weight_kg = serializers.IntegerField(min_value=1)
    max_weight_kg = serializers.IntegerField(required=False)

    def validate(self, attrs: dict[str, int]) -> dict[str, int]:
        if attrs["weight_kg"] > attrs.get("max_weight_kg", attrs["weight_kg"]):
            raise serializers.ValidationError("weight_kg exceeds max_weight_kg.")
        return attrs


class OpenView(APIView):
    permission_classes = [AllowAny]


class ValidationView(OpenView):
    def post(self, request: Request) -> Response:
        serializer = EchoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data)


class DomainErrorView(OpenView):
    def get(self, request: Request) -> Response:
        raise DomainError(
            ErrorCode.VALIDATION_INVALID_REFERENCE,
            "Branch not found.",
            errors={"branch": ["not found"]},
            headers={"X-Test": "1"},
        )


class ThrottledView(OpenView):
    def get(self, request: Request) -> Response:
        raise exceptions.Throttled(wait=12.2)


class DjangoNotFoundView(OpenView):
    def get(self, request: Request) -> Response:
        raise Http404("Shipment 42 not found in company 7")


class DjangoPermissionDeniedView(OpenView):
    def get(self, request: Request) -> Response:
        raise PermissionDenied


class ProtectedView(APIView):
    def get(self, request: Request) -> Response:
        return Response({})


class CrashingApiView(OpenView):
    def get(self, request: Request) -> Response:
        raise RuntimeError("password=hunter2 at db-internal.example")


def crashing_django_view(request: HttpRequest) -> HttpResponse:
    raise RuntimeError("secret internal detail")


urlpatterns = [
    path("health/live", health_live),
    path("api/v1/test/validation/", ValidationView.as_view()),
    path("api/v1/test/domain/", DomainErrorView.as_view()),
    path("api/v1/test/throttled/", ThrottledView.as_view()),
    path("api/v1/test/protected/", ProtectedView.as_view()),
    path("api/v1/test/django-404/", DjangoNotFoundView.as_view()),
    path("api/v1/test/django-403/", DjangoPermissionDeniedView.as_view()),
    path("api/v1/test/crash/", CrashingApiView.as_view()),
    path("test/django-crash/", crashing_django_view),
]

handler404 = "apps.core.views.not_found"
handler500 = "apps.core.views.server_error"
