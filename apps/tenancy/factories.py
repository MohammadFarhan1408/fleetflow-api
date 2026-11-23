import factory

from apps.tenancy.models import Branch, Company


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
