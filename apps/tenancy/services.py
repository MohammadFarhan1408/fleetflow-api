from apps.tenancy.constants import DEFAULT_SERVICE_TYPES
from apps.tenancy.models import Company, ServiceType


def create_company(*, name: str, currency: str, timezone: str) -> Company:
    """Create a tenant with its default service types. Call inside a transaction."""
    company = Company(name=name.strip(), currency=currency.strip().upper(), timezone=timezone)
    company.full_clean()
    company.save()
    ServiceType.objects.bulk_create(
        ServiceType(company=company, **defaults) for defaults in DEFAULT_SERVICE_TYPES
    )
    return company
