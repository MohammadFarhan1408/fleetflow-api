import uuid

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404

from apps.customers.models import Address, Customer
from apps.tenancy.models import Company


def customers_for_company(
    company: Company, *, include_archived: bool = False, search: str = ""
) -> QuerySet[Customer]:
    customers = Customer.objects.for_company(company)
    if not include_archived:
        customers = customers.filter(archived_at__isnull=True)
    if search:
        customers = customers.filter(name__icontains=search)
    return customers


def get_company_customer(company: Company, public_id: uuid.UUID) -> Customer:
    """A customer of `company` (archived included), or 404: other tenants' customers are
    indistinguishable from missing ones (architecture §9)."""
    return get_object_or_404(Customer.objects.for_company(company), public_id=public_id)


def addresses_for_customer(
    company: Company, customer: Customer, *, include_archived: bool = False
) -> QuerySet[Address]:
    addresses = Address.objects.for_company(company).filter(customer=customer)
    if not include_archived:
        addresses = addresses.filter(archived_at__isnull=True)
    return addresses


def get_customer_address(company: Company, customer: Customer, public_id: uuid.UUID) -> Address:
    return get_object_or_404(
        addresses_for_customer(company, customer, include_archived=True), public_id=public_id
    )
