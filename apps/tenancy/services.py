from apps.tenancy.constants import DEFAULT_SERVICE_TYPES
from apps.tenancy.models import Branch, Company, ServiceType


def create_company(*, name: str, currency: str, timezone: str) -> Company:
    """Create a tenant with its default service types. Call inside a transaction."""
    company = Company(name=name.strip(), currency=currency.strip().upper(), timezone=timezone)
    company.full_clean()
    company.save()
    ServiceType.objects.bulk_create(
        ServiceType(company=company, **defaults) for defaults in DEFAULT_SERVICE_TYPES
    )
    return company


def create_branch(*, company: Company, name: str, code: str) -> Branch:
    branch = Branch(company=company, name=name.strip(), code=code.strip().upper())
    branch.full_clean()
    branch.save()
    return branch
