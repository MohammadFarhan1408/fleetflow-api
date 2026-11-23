from collections.abc import Callable

import pytest
from django.contrib.gis.geos import Point
from django.db import IntegrityError
from django.test import RequestFactory

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode
from apps.core.errors import DomainError, ErrorCode
from apps.customers import services
from apps.customers.constants import ISO_3166_ALPHA2
from apps.customers.factories import AddressFactory, CustomerFactory
from apps.customers.models import Address, Customer, GeocodeConfidence

pytestmark = pytest.mark.django_db


def test_location_requires_manual_confidence() -> None:
    address = AddressFactory.create()

    with pytest.raises(IntegrityError):
        Address.objects.filter(pk=address.pk).update(location=Point(13.4, 52.5, srid=4326))


def test_manual_confidence_requires_location() -> None:
    address = AddressFactory.create()

    with pytest.raises(IntegrityError):
        Address.objects.filter(pk=address.pk).update(geocode_confidence=GeocodeConfidence.MANUAL)


def test_customer_kind_constrained_at_db_level() -> None:
    customer = CustomerFactory.create()

    with pytest.raises(IntegrityError):
        Customer.objects.filter(pk=customer.pk).update(kind="government")


def test_country_list_is_complete() -> None:
    assert len(ISO_3166_ALPHA2) == 249
    assert {"US", "DE", "IN", "AE", "BR"} <= ISO_3166_ALPHA2


# Defence in depth (rules.md §3): services refuse another company's objects even when a
# caller skips the tenant-scoped selector.
def test_services_reject_other_company_objects() -> None:
    actor = UserFactory.create(roles=[RoleCode.OWNER])
    customer = CustomerFactory.create()
    address = AddressFactory.create()
    request = RequestFactory().post("/")
    fields = services.CustomerFields.of(customer)
    address_fields = services.AddressFields.of(address)

    calls: list[Callable[[], object]] = [
        lambda: services.update_customer(actor=actor, customer=customer, fields=fields),
        lambda: services.archive_customer(actor=actor, customer=customer, request=request),
        lambda: services.create_address(
            actor=actor, customer=customer, fields=address_fields, request=request
        ),
        lambda: services.update_address(actor=actor, address=address, fields=address_fields),
        lambda: services.archive_address(actor=actor, address=address, request=request),
    ]
    for call in calls:
        with pytest.raises(DomainError) as exc:
            call()
        assert exc.value.error_code == ErrorCode.NOT_FOUND

    customer.refresh_from_db()
    assert customer.archived_at is None
