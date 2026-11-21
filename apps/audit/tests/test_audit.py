import pytest
from django.test import RequestFactory
from pytest_django.fixtures import Settings

from apps.accounts.factories import UserFactory
from apps.audit import services as audit
from apps.audit.models import AppendOnlyError, AuditAction, AuditLog
from apps.core.request_id import set_request_id
from apps.tenancy.factories import CompanyFactory

pytestmark = pytest.mark.django_db


def test_record_captures_request_context() -> None:
    user = UserFactory.create()
    request = RequestFactory().get(
        "/", REMOTE_ADDR="203.0.113.7", HTTP_USER_AGENT="FleetFlowDriver/1.4.0"
    )
    set_request_id("6f1c2d3e-0000-4000-8000-000000000001")
    try:
        entry = audit.record(
            AuditAction.AUTH_LOGIN, company=user.company, actor=user, request=request
        )
    finally:
        set_request_id(None)

    entry.refresh_from_db()
    assert entry.ip == "203.0.113.7"
    assert entry.user_agent == "FleetFlowDriver/1.4.0"
    assert entry.request_id == "6f1c2d3e-0000-4000-8000-000000000001"
    assert entry.actor == user


def test_forwarded_for_is_ignored_without_trusted_proxies() -> None:
    company = CompanyFactory.create()
    request = RequestFactory().get("/", REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="198.51.100.1")

    entry = audit.record(AuditAction.COMPANY_CREATED, company=company, request=request)

    assert entry.ip == "10.0.0.5"


def test_forwarded_for_used_with_one_trusted_proxy(settings: Settings) -> None:
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
    company = CompanyFactory.create()
    request = RequestFactory().get(
        "/", REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="spoofed, 198.51.100.1"
    )

    entry = audit.record(AuditAction.COMPANY_CREATED, company=company, request=request)

    assert entry.ip == "198.51.100.1"


def test_unparseable_forwarded_ip_is_stored_as_null(settings: Settings) -> None:
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
    request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="not-an-ip")

    entry = audit.record(
        AuditAction.COMPANY_CREATED, company=CompanyFactory.create(), request=request
    )

    assert entry.ip is None


def test_long_user_agent_is_truncated() -> None:
    company = CompanyFactory.create()
    request = RequestFactory().get("/", HTTP_USER_AGENT="x" * 2000)

    entry = audit.record(AuditAction.COMPANY_CREATED, company=company, request=request)

    assert len(entry.user_agent) == 512


def test_audit_rows_cannot_be_updated_or_deleted() -> None:
    entry = audit.record(AuditAction.COMPANY_CREATED, company=CompanyFactory.create())

    with pytest.raises(AppendOnlyError):
        entry.save()
    with pytest.raises(AppendOnlyError):
        entry.delete()
    with pytest.raises(AppendOnlyError):
        AuditLog.objects.filter(pk=entry.pk).update(action=AuditAction.AUTH_LOGIN)
    with pytest.raises(AppendOnlyError):
        AuditLog.objects.filter(pk=entry.pk).delete()
