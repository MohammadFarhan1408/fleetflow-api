from datetime import date
from decimal import Decimal

import factory

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode
from apps.fleet.models import (
    Driver,
    DriverDocument,
    DriverDocumentType,
    DriverLeave,
    LicenseCategory,
    Vehicle,
    VehicleDocument,
    VehicleDocumentType,
)
from apps.tenancy.factories import BranchFactory, CompanyFactory
from apps.uploads.factories import AttachedUploadFactory


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


# Far enough ahead that any test date is inside the validity window.
FAR_FUTURE = date(2099, 12, 31)


class DriverDocumentFactory(factory.django.DjangoModelFactory[DriverDocument]):
    """A valid licence unless overridden."""

    class Meta:
        model = DriverDocument

    driver = factory.SubFactory(DriverFactory)
    company = factory.SelfAttribute("driver.company")
    type = DriverDocumentType.LICENSE
    number = "LIC-0001"
    categories = factory.LazyFunction(lambda: [LicenseCategory.B, LicenseCategory.C])
    expires_on = FAR_FUTURE
    upload = factory.SubFactory(AttachedUploadFactory, company=factory.SelfAttribute("..company"))


class VehicleDocumentFactory(factory.django.DjangoModelFactory[VehicleDocument]):
    """A valid registration unless overridden."""

    class Meta:
        model = VehicleDocument

    vehicle = factory.SubFactory(VehicleFactory)
    company = factory.SelfAttribute("vehicle.company")
    type = VehicleDocumentType.REGISTRATION
    number = "REG-0001"
    expires_on = FAR_FUTURE
    upload = factory.SubFactory(AttachedUploadFactory, company=factory.SelfAttribute("..company"))
