import pytest
from django.db import IntegrityError

from apps.tenancy.factories import BranchFactory, CompanyFactory
from apps.tenancy.models import Branch, Company, CompanyStatus

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
        Company.objects.create(name="")


def test_company_active_flag_follows_status() -> None:
    company = CompanyFactory.create(status=CompanyStatus.SUSPENDED)

    assert company.is_active is False


def test_public_ids_are_uuid7() -> None:
    assert CompanyFactory.create().public_id.version == 7
