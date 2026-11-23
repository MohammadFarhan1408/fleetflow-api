from decimal import Decimal

import factory

from apps.customers.factories import CustomerFactory
from apps.shipments.models import (
    AddressRole,
    Shipment,
    ShipmentAddress,
    ShipmentItem,
    ShipmentStatus,
)
from apps.shipments.services import new_tracking_token
from apps.tenancy.factories import BranchFactory, ServiceTypeFactory


class ShipmentFactory(factory.django.DjangoModelFactory[Shipment]):
    """A `created` shipment with one parcel and both address snapshots, all in the
    branch's company."""

    class Meta:
        model = Shipment
        skip_postgeneration_save = True

    branch = factory.SubFactory(BranchFactory)
    company = factory.SelfAttribute("branch.company")
    customer = factory.SubFactory(CustomerFactory, company=factory.SelfAttribute("..company"))
    service_type = factory.SubFactory(
        ServiceTypeFactory, company=factory.SelfAttribute("..company")
    )
    tracking_number = factory.Sequence(lambda n: f"FF-TEST-{n:06d}")
    tracking_token = factory.LazyFunction(new_tracking_token)
    status = ShipmentStatus.CREATED
    total_weight_kg = Decimal("2.500")

    pickup = factory.RelatedFactory(
        "apps.shipments.factories.ShipmentAddressFactory", "shipment", role=AddressRole.PICKUP
    )
    delivery = factory.RelatedFactory(
        "apps.shipments.factories.ShipmentAddressFactory", "shipment", role=AddressRole.DELIVERY
    )
    item = factory.RelatedFactory("apps.shipments.factories.ShipmentItemFactory", "shipment")


class ShipmentAddressFactory(factory.django.DjangoModelFactory[ShipmentAddress]):
    class Meta:
        model = ShipmentAddress

    shipment = factory.SubFactory(ShipmentFactory)
    company = factory.SelfAttribute("shipment.company")
    role = AddressRole.DELIVERY
    contact_name = factory.Faker("name")
    line1 = factory.Faker("street_address")
    city = factory.Faker("city")
    country = "US"


class ShipmentItemFactory(factory.django.DjangoModelFactory[ShipmentItem]):
    class Meta:
        model = ShipmentItem

    shipment = factory.SubFactory(ShipmentFactory)
    company = factory.SelfAttribute("shipment.company")
    barcode = factory.Sequence(lambda n: f"FF-TEST-ITEM-{n:06d}")
    weight_kg = Decimal("2.500")
    length_cm = Decimal(40)
    width_cm = Decimal(30)
    height_cm = Decimal(20)
