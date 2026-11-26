from datetime import date

import factory

from apps.dispatch.models import Route, RouteStop, StopType
from apps.shipments.factories import ShipmentFactory
from apps.shipments.models import ShipmentStatus
from apps.tenancy.factories import BranchFactory


class RouteFactory(factory.django.DjangoModelFactory[Route]):
    """A planned route far in the future, so any test date works."""

    class Meta:
        model = Route

    branch = factory.SubFactory(BranchFactory)
    company = factory.SelfAttribute("branch.company")
    route_date = date(2099, 6, 15)


class RouteStopFactory(factory.django.DjangoModelFactory[RouteStop]):
    """A pending delivery stop for a shipment that is `assigned` and was `sorted`."""

    class Meta:
        model = RouteStop

    route = factory.SubFactory(RouteFactory)
    company = factory.SelfAttribute("route.company")
    shipment = factory.SubFactory(
        ShipmentFactory,
        branch=factory.SelfAttribute("..route.branch"),
        status=ShipmentStatus.ASSIGNED,
    )
    type = StopType.DELIVERY
    sequence = factory.Sequence(lambda n: n + 1)
    shipment_status_before = ShipmentStatus.SORTED
