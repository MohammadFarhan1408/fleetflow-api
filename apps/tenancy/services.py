from apps.tenancy.models import Company


def create_company(*, name: str) -> Company:
    company = Company(name=name.strip())
    company.full_clean()
    company.save()
    return company
