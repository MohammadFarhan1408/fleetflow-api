from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail
from rest_framework.test import APIClient

from apps.accounts import lockout
from apps.accounts.factories import UserFactory
from apps.accounts.models import TokenFamily, TokenFamilyRevokedReason, User
from apps.accounts.tests.conftest import ME_URL, REFRESH_URL, login
from apps.audit.models import AuditAction, AuditLog

pytestmark = pytest.mark.django_db

RESET_URL = "/api/v1/auth/password-reset/"
CONFIRM_URL = "/api/v1/auth/password-reset/confirm/"
NEW_PASSWORD = "a-much-better-passphrase-2026"


def request_reset(client: APIClient, email: str, **extra: Any) -> Any:
    return client.post(RESET_URL, {"email": email}, format="json", **extra)


def link_params(message: mail.EmailMessage) -> dict[str, str]:
    link = next(line for line in message.body.splitlines() if "uid=" in line)
    return {key: values[0] for key, values in parse_qs(urlparse(link).query).items()}


def confirm(client: APIClient, params: dict[str, str], password: str = NEW_PASSWORD) -> Any:
    return client.post(CONFIRM_URL, {**params, "new_password": password}, format="json")


def test_known_and_unknown_emails_get_the_same_answer(api_client: APIClient, user: User) -> None:
    known = request_reset(api_client, user.email)
    unknown = request_reset(api_client, "nobody@example.com")

    assert known.status_code == unknown.status_code == 202
    assert known.content == unknown.content == b""
    assert [m.to for m in mail.outbox] == [[user.email]]


def test_inactive_user_gets_no_email(api_client: APIClient) -> None:
    inactive = UserFactory.create(is_active=False)

    assert request_reset(api_client, inactive.email).status_code == 202
    assert mail.outbox == []


def test_reset_sets_password_ends_sessions_and_audits(api_client: APIClient, user: User) -> None:
    old = login(api_client, user.email).json()
    request_reset(api_client, user.email)

    response = confirm(api_client, link_params(mail.outbox[0]))

    assert response.status_code == 204
    assert login(api_client, user.email, NEW_PASSWORD).status_code == 200
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {old['access']}")
    assert api_client.get(ME_URL).status_code == 401
    api_client.credentials()
    assert (
        api_client.post(REFRESH_URL, {"refresh": old["refresh"]}, format="json").status_code == 401
    )
    assert TokenFamily.objects.filter(
        revoked_reason=TokenFamilyRevokedReason.PASSWORD_RESET
    ).exists()
    assert AuditLog.objects.filter(action=AuditAction.AUTH_PASSWORD_RESET, actor=user).exists()


def test_reset_link_is_single_use(api_client: APIClient, user: User) -> None:
    request_reset(api_client, user.email)
    params = link_params(mail.outbox[0])
    assert confirm(api_client, params).status_code == 204

    response = confirm(api_client, params, "another-good-passphrase-77")

    assert response.status_code == 400
    assert response.json()["code"] == "auth.invalid_reset_token"


def test_expired_reset_link_is_rejected(api_client: APIClient, user: User, settings: Any) -> None:
    request_reset(api_client, user.email)
    params = link_params(mail.outbox[0])
    settings.PASSWORD_RESET_TIMEOUT = -1

    assert confirm(api_client, params).json()["code"] == "auth.invalid_reset_token"


def test_unknown_uid_and_bad_token_look_the_same(api_client: APIClient, user: User) -> None:
    unknown = confirm(api_client, {"uid": "01a1ff8d-0000-7000-8000-000000000000", "token": "x"})
    bad = confirm(api_client, {"uid": str(user.public_id), "token": "x"})

    assert unknown.status_code == bad.status_code == 400
    assert unknown.json()["code"] == bad.json()["code"] == "auth.invalid_reset_token"


def test_weak_password_rejected_with_field_error(api_client: APIClient, user: User) -> None:
    request_reset(api_client, user.email)

    response = confirm(api_client, link_params(mail.outbox[0]), "12345678")

    assert response.status_code == 400
    assert "new_password" in response.json()["errors"]
    user.refresh_from_db()
    assert not user.check_password("12345678")


def test_invite_link_completes_through_confirm(api_client: APIClient) -> None:
    from apps.accounts.tasks import send_invite_email

    invited = UserFactory.create(password=None)
    send_invite_email(invited.pk)

    assert confirm(api_client, link_params(mail.outbox[0])).status_code == 204
    assert login(api_client, invited.email, NEW_PASSWORD).status_code == 200


def test_reset_clears_lockout_failures(api_client: APIClient, user: User) -> None:
    for _ in range(lockout.FAILURE_THRESHOLD - 1):
        lockout.register_failure(user.email)
    request_reset(api_client, user.email)
    confirm(api_client, link_params(mail.outbox[0]))

    assert lockout.register_failure(user.email) is None


def test_reset_requests_throttled_per_email(api_client: APIClient, user: User) -> None:
    statuses = [
        request_reset(api_client, user.email, REMOTE_ADDR=f"198.51.100.{n}").status_code
        for n in range(4)
    ]

    assert statuses == [202, 202, 202, 429]
    assert len(mail.outbox) == 3
