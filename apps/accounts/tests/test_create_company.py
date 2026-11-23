from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.management import CommandError, call_command

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.accounts.tasks import send_invite_email
from apps.audit.models import AuditAction, AuditLog
from apps.tenancy.models import Branch, Company, ServiceType

pytestmark = pytest.mark.django_db

type CaptureOnCommit = Callable[..., AbstractContextManager[list[Callable[[], Any]]]]


def run(**options: str) -> None:
    call_command(
        "create_company",
        name=options.get("name", "Acme Logistics"),
        currency=options.get("currency", "eur"),
        timezone=options.get("timezone", "Europe/Berlin"),
        branch_name=options.get("branch_name", "Berlin Hub"),
        branch_code=options.get("branch_code", "ber1"),
        owner_email=options.get("owner_email", "Owner@Acme.example"),
        owner_name=options.get("owner_name", "Olivia Owner"),
    )


def test_creates_company_owner_audit_and_invite(
    django_capture_on_commit_callbacks: CaptureOnCommit,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        run()

    company = Company.objects.get(name="Acme Logistics")
    assert (company.currency, company.timezone) == ("EUR", "Europe/Berlin")
    seeded = ServiceType.objects.for_company(company).values_list("code", flat=True)
    assert sorted(seeded) == ["express", "freight", "standard"]
    owner = User.objects.get(email="owner@acme.example")
    assert owner.company == company
    assert list(owner.roles.values_list("code", flat=True)) == [RoleCode.OWNER]
    assert owner.has_usable_password() is False
    branch = Branch.objects.for_company(company).get()
    assert (branch.name, branch.code) == ("Berlin Hub", "BER1")
    assert list(owner.branches.all()) == [branch]

    actions = set(AuditLog.objects.filter(company=company).values_list("action", flat=True))
    assert actions == {AuditAction.COMPANY_CREATED, AuditAction.USER_CREATED}

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["owner@acme.example"]
    link = next(line for line in mail.outbox[0].body.splitlines() if "uid=" in line)
    query = parse_qs(urlparse(link).query)
    assert query["uid"] == [str(owner.public_id)]
    assert default_token_generator.check_token(owner, query["token"][0])


def test_duplicate_owner_email_creates_nothing(
    django_capture_on_commit_callbacks: CaptureOnCommit,
) -> None:
    UserFactory.create(email="owner@acme.example")
    companies_before = Company.objects.count()

    with django_capture_on_commit_callbacks(execute=True), pytest.raises(CommandError):
        run()

    assert Company.objects.count() == companies_before
    assert mail.outbox == []


def test_blank_company_name_is_rejected() -> None:
    with pytest.raises(CommandError):
        run(name="   ")

    assert not User.objects.filter(email="owner@acme.example").exists()


@pytest.mark.parametrize(
    "options",
    [
        {"currency": "XYZ"},
        {"currency": "EURO"},
        {"timezone": "Mars/Base"},
        {"timezone": "utc"},
        {"branch_code": "B"},
        {"branch_code": "NY-1"},
        {"branch_name": "  "},
    ],
)
def test_invalid_company_settings_are_rejected(options: dict[str, str]) -> None:
    with pytest.raises(CommandError):
        run(**options)

    assert not Company.objects.filter(name="Acme Logistics").exists()


@pytest.mark.parametrize("missing", ["--currency", "--timezone", "--branch-name", "--branch-code"])
def test_company_settings_are_required(missing: str) -> None:
    arguments = {
        "--name": "Acme",
        "--currency": "EUR",
        "--timezone": "UTC",
        "--branch-name": "Main",
        "--branch-code": "MAIN",
        "--owner-email": "o@a.example",
        "--owner-name": "O",
    }
    del arguments[missing]

    with pytest.raises(CommandError, match=missing):
        call_command("create_company", *(f"{flag}={value}" for flag, value in arguments.items()))


def test_invite_not_sent_to_deactivated_user() -> None:
    user = UserFactory.create(is_active=False)

    send_invite_email(user.pk)

    assert mail.outbox == []
