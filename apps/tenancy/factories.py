from decimal import Decimal

import factory

from apps.tenancy.models import Branch, Company, ServiceType


class CompanyFactory(factory.django.DjangoModelFactory[Company]):
    class Meta:
        model = Company

    name = factory.Sequence(lambda n: f"Company {n}")
    currency = "USD"
    timezone = "UTC"


class BranchFactory(factory.django.DjangoModelFactory[Branch]):
    class Meta:
        model = Branch

    company = factory.SubFactory(CompanyFactory)
    name = factory.Sequence(lambda n: f"Branch {n}")
    code = factory.Sequence(lambda n: f"BR{n}")


class ServiceTypeFactory(factory.django.DjangoModelFactory[ServiceType]):
    class Meta:
        model = ServiceType

    company = factory.SubFactory(CompanyFactory)
    code = factory.Sequence(lambda n: f"service{n}")
    name = factory.Sequence(lambda n: f"Service {n}")
    max_weight_kg = Decimal(30)
    max_length_cm = Decimal(120)
    max_width_cm = Decimal(80)
    max_height_cm = Decimal(80)
