from datetime import date

import factory
from django.utils import timezone

from apps.dispatch.models import Route, RouteStatus, RouteStop, StopType
from apps.fleet.factories import DriverFactory, VehicleFactory
from apps.shipments.factories import ShipmentFactory
from apps.shipments.models import ShipmentStatus
from apps.tenancy.factories import BranchFactory


class RouteFactory(factory.django.DjangoModelFactory[Route]):
    """A planned route far in the future, so any test date works."""

    class Meta:
        model = Route
        exclude = ("has_crew",)

    branch = factory.SubFactory(BranchFactory)
    company = factory.SelfAttribute("branch.company")
    route_date = date(2099, 6, 15)
    status = RouteStatus.PLANNED
    # Every status past `planned` (cancelled aside) has a driver and vehicle (DB check), so
    # the factory supplies them; pass `driver=None, vehicle=None` or your own to override.
    has_crew = factory.LazyAttribute(
        lambda o: o.status not in (RouteStatus.PLANNED, RouteStatus.CANCELLED)
    )
    dispatched_at = factory.LazyAttribute(
        lambda o: (
            timezone.now()
            if o.status in (RouteStatus.DISPATCHED, RouteStatus.IN_PROGRESS, RouteStatus.COMPLETED)
            else None
        )
    )
    started_at = factory.LazyAttribute(
        lambda o: (
            timezone.now() if o.status in (RouteStatus.IN_PROGRESS, RouteStatus.COMPLETED) else None
        )
    )
    driver = factory.Maybe(
        "has_crew",
        yes_declaration=factory.SubFactory(
            DriverFactory,
            company=factory.SelfAttribute("..company"),
            branch=factory.SelfAttribute("..branch"),
        ),
        no_declaration=None,
    )
    vehicle = factory.Maybe(
        "has_crew",
        yes_declaration=factory.SubFactory(
            VehicleFactory,
            company=factory.SelfAttribute("..company"),
            branch=factory.SelfAttribute("..branch"),
        ),
        no_declaration=None,
    )


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
