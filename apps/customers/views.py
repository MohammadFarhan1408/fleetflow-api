import uuid
from dataclasses import replace

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
from apps.core.idempotency import idempotent
from apps.core.serializers import ErrorSerializer
from apps.customers import selectors, services
from apps.customers.models import Address, Customer
from apps.customers.permissions import CUSTOMERS_MANAGE, CUSTOMERS_VIEW
from apps.customers.serializers import (
    AddressSerializer,
    AddressWriteSerializer,
    CustomerListQuerySerializer,
    CustomerSerializer,
    CustomerWriteSerializer,
    ListQuerySerializer,
)

ERROR = OpenApiResponse(ErrorSerializer)
READ_ERRORS = {401: ERROR, 403: ERROR, 404: ERROR}
WRITE_ERRORS = {400: ERROR, 401: ERROR, 403: ERROR, 404: ERROR, 409: ERROR, 422: ERROR}


class CustomerAccess(APIView):
    # Reads need customers.view; every write (POST, PATCH) needs customers.manage.
    permission_classes = [IsAuthenticated, HasRequiredPermission]
    required_permissions = {
        "GET": CUSTOMERS_VIEW,
        "POST": CUSTOMERS_MANAGE,
        "PATCH": CUSTOMERS_MANAGE,
    }


@extend_schema_view(get=extend_schema(parameters=[CustomerListQuerySerializer]))
class CustomerListCreateView(CustomerAccess, ListAPIView[Customer]):
    """List the company's customers (`customers.view`) or create one (`customers.manage`).
    Archived customers are hidden unless `include_archived=true`."""

    serializer_class = CustomerSerializer

    def get_queryset(self) -> QuerySet[Customer]:
        query = CustomerListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        return selectors.customers_for_company(
            current_user(self.request).company, **query.validated_data
        )

    @extend_schema(
        request=CustomerWriteSerializer, responses={201: CustomerSerializer, **WRITE_ERRORS}
    )
    @idempotent
    def post(self, request: Request) -> Response:
        serializer = CustomerWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = services.create_customer(
            actor=current_user(request),
            fields=services.CustomerFields(**serializer.validated_data),
            request=request,
        )
        return Response(CustomerSerializer(customer).data, status=status.HTTP_201_CREATED)


class CustomerDetailView(CustomerAccess):
    """Read (`customers.view`) or update (`customers.manage`) one customer. Archived
    customers can be read but not changed (409 `customer.archived`)."""

    @extend_schema(responses={200: CustomerSerializer, **READ_ERRORS})
    def get(self, request: Request, public_id: uuid.UUID) -> Response:
        customer = selectors.get_company_customer(current_user(request).company, public_id)
        return Response(CustomerSerializer(customer).data)

    @extend_schema(
        request=CustomerWriteSerializer(partial=True),
        responses={200: CustomerSerializer, **WRITE_ERRORS},
    )
    @idempotent
    def patch(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        customer = selectors.get_company_customer(actor.company, public_id)
        serializer = CustomerWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = replace(services.CustomerFields.of(customer), **serializer.validated_data)
        customer = services.update_customer(actor=actor, customer=customer, fields=fields)
        return Response(CustomerSerializer(customer).data)


class CustomerArchiveView(CustomerAccess):
    """Archive a customer and its addresses (`customers.manage`). Repeating it is a no-op."""

    @extend_schema(request=None, responses={200: CustomerSerializer, **READ_ERRORS})
    def post(self, request: Request, public_id: uuid.UUID) -> Response:
        actor = current_user(request)
        customer = selectors.get_company_customer(actor.company, public_id)
        customer = services.archive_customer(actor=actor, customer=customer, request=request)
        return Response(CustomerSerializer(customer).data)


@extend_schema_view(get=extend_schema(parameters=[ListQuerySerializer]))
class AddressListCreateView(CustomerAccess, ListAPIView[Address]):
    """List a customer's addresses (`customers.view`) or add one (`customers.manage`).
    Archived addresses are hidden unless `include_archived=true`."""

    serializer_class = AddressSerializer

    def get_queryset(self) -> QuerySet[Address]:
        query = ListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        company = current_user(self.request).company
        customer = selectors.get_company_customer(company, self.kwargs["customer_id"])
        return selectors.addresses_for_customer(company, customer, **query.validated_data)

    @extend_schema(
        request=AddressWriteSerializer, responses={201: AddressSerializer, **WRITE_ERRORS}
    )
    @idempotent
    def post(self, request: Request, customer_id: uuid.UUID) -> Response:
        actor = current_user(request)
        customer = selectors.get_company_customer(actor.company, customer_id)
        serializer = AddressWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        address = services.create_address(
            actor=actor,
            customer=customer,
            fields=services.AddressFields(**serializer.validated_data),
            request=request,
        )
        return Response(AddressSerializer(address).data, status=status.HTTP_201_CREATED)


def _get_address(request: Request, customer_id: uuid.UUID, public_id: uuid.UUID) -> Address:
    company = current_user(request).company
    customer = selectors.get_company_customer(company, customer_id)
    return selectors.get_customer_address(company, customer, public_id)


class AddressDetailView(CustomerAccess):
    """Read (`customers.view`) or update (`customers.manage`) one address. Archived
    addresses can be read but not changed (409 `address.archived`)."""

    @extend_schema(responses={200: AddressSerializer, **READ_ERRORS})
    def get(self, request: Request, customer_id: uuid.UUID, public_id: uuid.UUID) -> Response:
        return Response(AddressSerializer(_get_address(request, customer_id, public_id)).data)

    @extend_schema(
        request=AddressWriteSerializer(partial=True),
        responses={200: AddressSerializer, **WRITE_ERRORS},
    )
    @idempotent
    def patch(self, request: Request, customer_id: uuid.UUID, public_id: uuid.UUID) -> Response:
        address = _get_address(request, customer_id, public_id)
        serializer = AddressWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = replace(services.AddressFields.of(address), **serializer.validated_data)
        address = services.update_address(
            actor=current_user(request), address=address, fields=fields
        )
        return Response(AddressSerializer(address).data)


class AddressArchiveView(CustomerAccess):
    """Archive one address (`customers.manage`). Repeating it is a no-op."""

    @extend_schema(request=None, responses={200: AddressSerializer, **READ_ERRORS})
    def post(self, request: Request, customer_id: uuid.UUID, public_id: uuid.UUID) -> Response:
        address = _get_address(request, customer_id, public_id)
        address = services.archive_address(
            actor=current_user(request), address=address, request=request
        )
        return Response(AddressSerializer(address).data)
