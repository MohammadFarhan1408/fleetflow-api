from datetime import date
from decimal import Decimal

import factory

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode
from apps.fleet.models import Driver, DriverLeave, LicenseCategory, Vehicle
from apps.tenancy.factories import BranchFactory, CompanyFactory


class DriverFactory(factory.django.DjangoModelFactory[Driver]):
    class Meta:
        model = Driver

    company = factory.SubFactory(CompanyFactory)
    branch = factory.SubFactory(BranchFactory, company=factory.SelfAttribute("..company"))
    user = factory.SubFactory(
        UserFactory, company=factory.SelfAttribute("..company"), roles=[RoleCode.DRIVER]
    )
    phone = "+4915112345678"


class VehicleFactory(factory.django.DjangoModelFactory[Vehicle]):
    class Meta:
        model = Vehicle

    company = factory.SubFactory(CompanyFactory)
    branch = factory.SubFactory(BranchFactory, company=factory.SelfAttribute("..company"))
    registration_number = factory.Sequence(lambda n: f"B FF {n:04d}")
    required_license_category = LicenseCategory.C
    capacity_weight_kg = Decimal(3500)
    capacity_volume_m3 = Decimal(20)


class DriverLeaveFactory(factory.django.DjangoModelFactory[DriverLeave]):
    class Meta:
        model = DriverLeave

    driver = factory.SubFactory(DriverFactory)
    company = factory.SelfAttribute("driver.company")
    starts_on = date(2030, 1, 10)
    ends_on = date(2030, 1, 12)
    reason = "Annual leave"
