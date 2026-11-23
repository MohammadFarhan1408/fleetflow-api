import factory

from apps.customers.models import Address, Customer, CustomerKind
from apps.tenancy.factories import CompanyFactory


class CustomerFactory(factory.django.DjangoModelFactory[Customer]):
    class Meta:
        model = Customer

    company = factory.SubFactory(CompanyFactory)
    kind = CustomerKind.BUSINESS
    name = factory.Faker("company")
    email = factory.Faker("company_email")
    phone = "+14155550123"


class AddressFactory(factory.django.DjangoModelFactory[Address]):
    class Meta:
        model = Address

    customer = factory.SubFactory(CustomerFactory)
    # An address always belongs to its customer's company.
    company = factory.SelfAttribute("customer.company")
    line1 = factory.Faker("street_address")
    city = factory.Faker("city")
    postal_code = factory.Faker("postcode")
    country = "US"
