import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.tenancy.constants import ISO_4217_CODES
from apps.tenancy.factories import BranchFactory, CompanyFactory
from apps.tenancy.models import Branch, Company, CompanyStatus, ServiceType

pytestmark = pytest.mark.django_db


def test_branch_name_unique_within_company() -> None:
    branch = BranchFactory.create(name="North")

    with pytest.raises(IntegrityError):
        BranchFactory.create(company=branch.company, name="North")


def test_same_branch_name_allowed_in_another_company() -> None:
    BranchFactory.create(name="North")

    BranchFactory.create(name="North")  # different company via SubFactory

    assert Branch.objects.filter(name="North").count() == 2


def test_for_company_returns_only_that_tenants_rows() -> None:
    mine = BranchFactory.create()
    BranchFactory.create()  # another company

    assert list(Branch.objects.for_company(mine.company)) == [mine]


def test_company_status_constrained_at_db_level() -> None:
    company = CompanyFactory.create()

    with pytest.raises(IntegrityError):
        Company.objects.filter(pk=company.pk).update(status="deleted")


def test_company_name_cannot_be_empty() -> None:
    with pytest.raises(IntegrityError):
        Company.objects.create(name="", currency="USD", timezone="UTC")


def test_company_active_flag_follows_status() -> None:
    company = CompanyFactory.create(status=CompanyStatus.SUSPENDED)

    assert company.is_active is False


def test_public_ids_are_uuid7() -> None:
    assert CompanyFactory.create().public_id.version == 7


def test_branch_code_unique_within_company() -> None:
    branch = BranchFactory.create(code="NYC")

    with pytest.raises(IntegrityError):
        BranchFactory.create(company=branch.company, code="NYC")


@pytest.mark.parametrize("code", ["N", "nyc", "NY-1", "NY 1"])
def test_branch_code_format_enforced(code: str) -> None:
    branch = BranchFactory.build(company=CompanyFactory.create(), code=code)

    with pytest.raises(ValidationError):
        branch.full_clean()
    with pytest.raises(IntegrityError):
        branch.save()


@pytest.mark.parametrize(
    ("field", "value"), [("currency", "XYZ"), ("currency", "usd"), ("timezone", "Nowhere/City")]
)
def test_company_currency_and_timezone_validated(field: str, value: str) -> None:
    company = CompanyFactory.build(**{field: value})

    with pytest.raises(ValidationError) as exc:
        company.full_clean()

    assert field in exc.value.message_dict


def test_company_currency_format_enforced_at_db_level() -> None:
    company = CompanyFactory.create()

    with pytest.raises(IntegrityError):
        Company.objects.filter(pk=company.pk).update(currency="usd")


def test_currency_list_holds_only_iso_codes() -> None:
    assert {"USD", "EUR", "INR", "AED", "JPY"} <= ISO_4217_CODES
    assert all(len(code) == 3 and code.isupper() for code in ISO_4217_CODES)


def test_service_type_limits_must_be_positive() -> None:
    company = CompanyFactory.create()

    with pytest.raises(IntegrityError):
        ServiceType.objects.create(
            company=company,
            code="bad",
            name="Bad",
            max_weight_kg=0,
            max_length_cm=1,
            max_width_cm=1,
            max_height_cm=1,
        )
