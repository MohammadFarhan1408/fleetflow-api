import threading
import uuid
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts.models import RoleCode
from apps.core.errors import DomainError, ErrorCode
from apps.core.models import AppendOnlyError
from apps.customers.factories import AddressFactory, CustomerFactory
from apps.customers.services import AddressFields
from apps.shipments import services
from apps.shipments.factories import ShipmentAddressFactory, ShipmentFactory, ShipmentItemFactory
from apps.shipments.models import (
    EventSource,
    Shipment,
    ShipmentStatus,
    TrackingEvent,
    TrackingEventType,
)
from apps.shipments.tests.conftest import make_user
from apps.tenancy.factories import BranchFactory, ServiceTypeFactory


def _fields(
    branch_id: uuid.UUID, customer_id: uuid.UUID, address: AddressFields
) -> services.ShipmentFields:
    return services.ShipmentFields(
        branch_id=branch_id,
        customer_id=customer_id,
        service_type="standard",
        pickup=address,
        delivery=address,
        items=(services.ItemFields(Decimal(1), Decimal(10), Decimal(10), Decimal(10)),),
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_creations_get_distinct_tracking_numbers() -> None:
    branch = BranchFactory.create(code="NY1")
    ServiceTypeFactory.create(company=branch.company, code="standard")
    customer = CustomerFactory.create(company=branch.company)
    actor = make_user(branch.company, RoleCode.DISPATCHER, branch)
    address = AddressFields(line1="1 Main St", city="NYC", country="US", contact_name="Rita")
    fields = _fields(branch.public_id, customer.public_id, address)
    start = threading.Barrier(4)
    failures: list[BaseException] = []

    def create_one() -> None:
        try:
            start.wait()
            services.create_shipment(
                actor=actor, fields=fields, draft=False, request=RequestFactory().post("/")
            )
        except BaseException as exc:  # surfaced by the assertion below
            failures.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=create_one) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    numbers = sorted(Shipment.objects.values_list("tracking_number", flat=True))
    assert numbers == [f"FF-NY1-00000{n}" for n in (1, 2, 3, 4)]


@pytest.mark.django_db
def test_services_reject_shipments_of_another_company() -> None:
    shipment = ShipmentFactory.create(status=ShipmentStatus.DRAFT)
    outsider = make_user(BranchFactory.create().company, RoleCode.OWNER)
    request = RequestFactory().post("/")

    with pytest.raises(DomainError) as rotate:
        services.rotate_tracking_token(actor=outsider, shipment=shipment, request=request)
    with pytest.raises(DomainError) as delete:
        services.delete_draft(actor=outsider, shipment=shipment, request=request)

    assert rotate.value.error_code == delete.value.error_code == ErrorCode.NOT_FOUND
    assert Shipment.objects.filter(pk=shipment.pk).exists()


@pytest.mark.django_db
def test_tracking_events_are_append_only() -> None:
    shipment = ShipmentFactory.create()
    event = TrackingEvent.objects.create(
        company=shipment.company,
        shipment=shipment,
        type=TrackingEventType.STATUS_CHANGE,
        status_after=ShipmentStatus.CREATED,
        occurred_at=timezone.now(),
        source=EventSource.ADMIN,
    )
    events = TrackingEvent.objects.for_company(shipment.company)

    with pytest.raises(AppendOnlyError):
        event.save()
    with pytest.raises(AppendOnlyError):
        event.delete()
    with pytest.raises(AppendOnlyError):
        events.update(public=False)
    with pytest.raises(AppendOnlyError):
        events.delete()


@pytest.mark.django_db
def test_shipment_with_events_cannot_be_deleted() -> None:
    shipment = ShipmentFactory.create()
    TrackingEvent.objects.create(
        company=shipment.company,
        shipment=shipment,
        type=TrackingEventType.STATUS_CHANGE,
        status_after=ShipmentStatus.CREATED,
        occurred_at=timezone.now(),
        source=EventSource.ADMIN,
    )

    with pytest.raises(IntegrityError):
        shipment.delete()


@pytest.mark.django_db
@pytest.mark.parametrize("weight", [Decimal(0), Decimal("30000.001")])
def test_total_weight_db_check(weight: Decimal) -> None:
    with pytest.raises(IntegrityError):
        ShipmentFactory.create(total_weight_kg=weight)


@pytest.mark.django_db
def test_snapshot_db_checks() -> None:
    shipment = ShipmentFactory.create()

    with pytest.raises(IntegrityError):
        ShipmentAddressFactory.create(shipment=shipment, role="pickup")  # one per role


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides", [{"contact_name": ""}, {"country": "de"}, {"geocode_confidence": "manual"}]
)
def test_snapshot_field_checks(overrides: dict[str, str]) -> None:
    shipment = ShipmentFactory.create()
    shipment.addresses.filter(role="pickup").delete()

    with pytest.raises(IntegrityError):
        ShipmentAddressFactory.create(shipment=shipment, role="pickup", **overrides)


@pytest.mark.django_db
def test_barcodes_are_unique_per_company() -> None:
    first = ShipmentItemFactory.create(barcode="FF-NY1-000001-001")
    ShipmentItemFactory.create(barcode="FF-NY1-000001-001")  # another company: fine

    with pytest.raises(IntegrityError):
        ShipmentItemFactory.create(shipment=first.shipment, barcode="FF-NY1-000001-001")


@pytest.mark.django_db
def test_address_book_entry_still_validates_after_refactor() -> None:
    # Address and ShipmentAddress share PostalAddress; the address-book checks still apply.
    with pytest.raises(IntegrityError):
        AddressFactory.create(country="de")
