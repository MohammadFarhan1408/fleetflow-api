"""Architecture §26.4 'Authentication & authorization' rows, plus Phase 1 additions."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.accounts.factories import UserFactory
from apps.accounts.models import RefreshTokenRecord, TokenFamily, TokenFamilyRevokedReason, User
from apps.accounts.services import REFRESH_REUSE_GRACE
from apps.accounts.tests.conftest import LOGOUT_URL, ME_URL, REFRESH_URL, login
from apps.accounts.tokens import FAMILY_CLAIM, decode_refresh_token
from apps.audit.models import AuditAction, AuditLog
from apps.tenancy.models import CompanyStatus

pytestmark = pytest.mark.django_db


def bearer(client: APIClient, access: str) -> APIClient:
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


# --- Login -------------------------------------------------------------------------


def test_valid_credentials_issue_tokens_update_last_login_and_audit(
    api_client: APIClient, user: User
) -> None:
    response = login(api_client, "Dispatch@Example.com")  # email is case-insensitive

    assert response.status_code == 200
    assert {"access", "refresh"} == response.json().keys()
    user.refresh_from_db()
    assert user.last_login is not None
    assert AuditLog.objects.filter(action=AuditAction.AUTH_LOGIN, actor=user).count() == 1


def test_tokens_carry_public_id_and_family_never_internal_id(
    api_client: APIClient, user: User
) -> None:
    access = AccessToken(login(api_client, user.email).json()["access"])

    assert access["user_id"] == str(user.public_id)
    assert TokenFamily.objects.filter(id=access[FAMILY_CLAIM], user=user).exists()


def test_wrong_password_and_unknown_user_get_identical_responses(
    api_client: APIClient, user: User
) -> None:
    wrong = login(api_client, user.email, "wrong-password")
    unknown = login(api_client, "nobody@example.com", "wrong-password")

    assert wrong.status_code == unknown.status_code == 401
    wrong_body, unknown_body = wrong.json(), unknown.json()
    wrong_body.pop("request_id"), unknown_body.pop("request_id")
    assert wrong_body == unknown_body
    assert wrong_body["code"] == "auth.invalid_credentials"


def test_disabled_user_gets_user_disabled_and_no_tokens(api_client: APIClient) -> None:
    disabled = UserFactory.create(is_active=False)

    response = login(api_client, disabled.email)

    assert response.status_code == 401
    assert response.json()["code"] == "auth.user_disabled"
    assert "access" not in response.json()


def test_disabled_user_with_wrong_password_gets_generic_error(api_client: APIClient) -> None:
    disabled = UserFactory.create(is_active=False)

    response = login(api_client, disabled.email, "wrong-password")

    assert response.json()["code"] == "auth.invalid_credentials"


def test_suspended_company_rejected_at_login(api_client: APIClient, user: User) -> None:
    user.company.status = CompanyStatus.SUSPENDED
    user.company.save()

    response = login(api_client, user.email)

    assert response.status_code == 403
    assert response.json()["code"] == "auth.company_suspended"


def test_invited_user_without_password_cannot_log_in(api_client: APIClient) -> None:
    invited = UserFactory.create(password=None)
    invited.set_unusable_password()
    invited.save()

    assert login(api_client, invited.email, "").status_code == 400  # blank password
    assert login(api_client, invited.email, "anything").status_code == 401


def test_login_ignores_stale_authorization_header(api_client: APIClient, user: User) -> None:
    api_client.credentials(HTTP_AUTHORIZATION="Bearer expired.or.garbage")

    assert login(api_client, user.email).status_code == 200


# --- Access tokens -----------------------------------------------------------------


def test_me_returns_session_bootstrap(
    api_client: APIClient, tokens: dict[str, str], user: User
) -> None:
    response = bearer(api_client, tokens["access"]).get(ME_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {
        "public_id": str(user.public_id),
        "email": user.email,
        "full_name": user.full_name,
    }
    assert body["company"] == {"public_id": str(user.company.public_id), "name": user.company.name}
    assert body["roles"] == ["dispatcher"]
    assert body["permissions"] == [
        "customers.view",
        "dispatch.manage",
        "dispatch.override_capacity",
        "dispatch.view",
        "fleet.view",
        "shipments.cancel",
        "shipments.create",
        "shipments.view",
    ]
    assert body["branches"] == []


def test_me_lists_permissions_for_owner(api_client: APIClient) -> None:
    owner = UserFactory.create(roles=["owner"])
    access = login(api_client, owner.email).json()["access"]

    body = bearer(api_client, access).get(ME_URL).json()

    assert body["permissions"] == [
        "branches.manage",
        "customers.manage",
        "customers.view",
        "dispatch.all_branches",
        "dispatch.manage",
        "dispatch.override_capacity",
        "dispatch.view",
        "fleet.all_branches",
        "fleet.manage",
        "fleet.view",
        "shipments.all_branches",
        "shipments.cancel",
        "shipments.correct_status",
        "shipments.create",
        "shipments.recover",
        "shipments.view",
        "uploads.create",
        "users.manage",
        "users.view",
    ]


def test_me_query_count(
    api_client: APIClient, tokens: dict[str, str], django_assert_max_num_queries: object
) -> None:
    client = bearer(api_client, tokens["access"])
    with django_assert_max_num_queries(4):  # type: ignore[operator]
        assert client.get(ME_URL).status_code == 200


def test_missing_token_is_401(api_client: APIClient) -> None:
    response = api_client.get(ME_URL)

    assert response.status_code == 401
    assert response.json()["code"] == "auth.not_authenticated"


def test_expired_access_token_is_token_expired(api_client: APIClient, user: User) -> None:
    token = AccessToken.for_user(user)
    token.set_exp(lifetime=timedelta(seconds=-1))

    response = bearer(api_client, str(token)).get(ME_URL)

    assert response.status_code == 401
    assert response.json()["code"] == "auth.token_expired"
    assert response["WWW-Authenticate"].startswith("Bearer")


def test_tampered_token_is_token_invalid(api_client: APIClient, tokens: dict[str, str]) -> None:
    header, payload, signature = tokens["access"].split(".")
    tampered = f"{header}.{payload}.{signature[:-4]}AAAA"

    response = bearer(api_client, tampered).get(ME_URL)

    assert response.status_code == 401
    assert response.json()["code"] == "auth.token_invalid"


def test_refresh_token_cannot_be_used_as_access_token(
    api_client: APIClient, tokens: dict[str, str]
) -> None:
    response = bearer(api_client, tokens["refresh"]).get(ME_URL)

    assert response.json()["code"] == "auth.token_invalid"


def test_disabling_user_rejects_existing_access_token(
    api_client: APIClient, tokens: dict[str, str], user: User
) -> None:
    User.objects.filter(pk=user.pk).update(is_active=False)

    response = bearer(api_client, tokens["access"]).get(ME_URL)

    assert response.status_code == 401
    assert response.json()["code"] == "auth.user_disabled"


def test_suspending_company_rejects_existing_access_token(
    api_client: APIClient, tokens: dict[str, str], user: User
) -> None:
    user.company.status = CompanyStatus.SUSPENDED
    user.company.save()

    response = bearer(api_client, tokens["access"]).get(ME_URL)

    assert response.status_code == 403
    assert response.json()["code"] == "auth.company_suspended"


def test_logout_ends_access_token_immediately(
    api_client: APIClient, tokens: dict[str, str]
) -> None:
    api_client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json")

    response = bearer(api_client, tokens["access"]).get(ME_URL)

    assert response.status_code == 401
    assert response.json()["code"] == "auth.token_invalid"


def test_access_token_without_family_claim_is_invalid(api_client: APIClient, user: User) -> None:
    # Validly signed but not issued by our login flow.
    response = bearer(api_client, str(AccessToken.for_user(user))).get(ME_URL)

    assert response.json()["code"] == "auth.token_invalid"


def test_access_token_user_must_match_family(api_client: APIClient, user: User) -> None:
    other = UserFactory.create()
    token = AccessToken.for_user(other)
    token[FAMILY_CLAIM] = str(TokenFamily.objects.create(user=user).id)

    assert bearer(api_client, str(token)).get(ME_URL).status_code == 401


# --- Refresh rotation and reuse ----------------------------------------------------


def refresh(client: APIClient, token: str) -> Any:
    return client.post(REFRESH_URL, {"refresh": token}, format="json")


def test_refresh_rotates_and_keeps_family(api_client: APIClient, tokens: dict[str, str]) -> None:
    response = refresh(api_client, tokens["refresh"])

    assert response.status_code == 200
    new = response.json()
    assert new["refresh"] != tokens["refresh"]
    old_fam = decode_refresh_token(tokens["refresh"])[FAMILY_CLAIM]
    assert decode_refresh_token(new["refresh"])[FAMILY_CLAIM] == old_fam
    assert (
        RefreshTokenRecord.objects.get(jti=decode_refresh_token(tokens["refresh"])["jti"]).used_at
        is not None
    )


def test_reuse_within_grace_window_returns_new_pair(
    api_client: APIClient, tokens: dict[str, str]
) -> None:
    first = refresh(api_client, tokens["refresh"])
    retry = refresh(api_client, tokens["refresh"])  # e.g. the first response was lost

    assert first.status_code == retry.status_code == 200
    family = TokenFamily.objects.get()
    assert family.revoked_at is None
    # Both successors remain valid.
    assert refresh(api_client, first.json()["refresh"]).status_code == 200
    assert refresh(api_client, retry.json()["refresh"]).status_code == 200


def test_reuse_after_grace_window_revokes_family_and_audits(
    api_client: APIClient, tokens: dict[str, str], user: User
) -> None:
    successor = refresh(api_client, tokens["refresh"]).json()
    RefreshTokenRecord.objects.filter(jti=decode_refresh_token(tokens["refresh"])["jti"]).update(
        used_at=timezone.now() - REFRESH_REUSE_GRACE - timedelta(seconds=1)
    )

    response = refresh(api_client, tokens["refresh"])

    assert response.status_code == 401
    assert response.json()["code"] == "auth.token_invalid"
    family = TokenFamily.objects.get()
    assert family.revoked_reason == TokenFamilyRevokedReason.REUSE
    assert AuditLog.objects.filter(action=AuditAction.AUTH_REFRESH_REUSE, actor=user).exists()
    # Everything in the family is dead: the successor refresh token and access tokens.
    assert refresh(api_client, successor["refresh"]).status_code == 401
    assert bearer(api_client, successor["access"]).get(ME_URL).status_code == 401


def test_other_sessions_survive_reuse_revocation(api_client: APIClient, user: User) -> None:
    phone = login(api_client, user.email).json()
    laptop = login(api_client, user.email).json()
    refresh(api_client, phone["refresh"])
    RefreshTokenRecord.objects.filter(jti=decode_refresh_token(phone["refresh"])["jti"]).update(
        used_at=timezone.now() - timedelta(minutes=5)
    )
    refresh(api_client, phone["refresh"])

    assert refresh(api_client, laptop["refresh"]).status_code == 200


def test_refresh_rejected_for_disabled_user(
    api_client: APIClient, tokens: dict[str, str], user: User
) -> None:
    User.objects.filter(pk=user.pk).update(is_active=False)

    response = refresh(api_client, tokens["refresh"])

    assert response.json()["code"] == "auth.user_disabled"


def test_refresh_rejected_for_suspended_company(
    api_client: APIClient, tokens: dict[str, str], user: User
) -> None:
    user.company.status = CompanyStatus.SUSPENDED
    user.company.save()

    assert refresh(api_client, tokens["refresh"]).status_code == 403


def test_access_token_cannot_be_used_to_refresh(
    api_client: APIClient, tokens: dict[str, str]
) -> None:
    response = refresh(api_client, tokens["access"])

    assert response.json()["code"] == "auth.token_invalid"


def test_expired_refresh_token_is_token_expired(api_client: APIClient, user: User) -> None:
    token = RefreshToken.for_user(user)
    token.set_exp(lifetime=timedelta(seconds=-1))

    response = refresh(api_client, str(token))

    assert response.json()["code"] == "auth.token_expired"


def test_validly_signed_but_unknown_refresh_token_is_invalid(
    api_client: APIClient, user: User
) -> None:
    # Signed with our key but never recorded (e.g. forged after a key leak, or purged).
    response = refresh(api_client, str(RefreshToken.for_user(user)))

    assert response.json()["code"] == "auth.token_invalid"


# --- Logout ------------------------------------------------------------------------


def test_logout_revokes_family_and_audits(
    api_client: APIClient, tokens: dict[str, str], user: User
) -> None:
    response = api_client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json")

    assert response.status_code == 204
    assert TokenFamily.objects.get().revoked_reason == TokenFamilyRevokedReason.LOGOUT
    assert AuditLog.objects.filter(action=AuditAction.AUTH_LOGOUT, actor=user).exists()
    assert refresh(api_client, tokens["refresh"]).status_code == 401


def test_logout_is_idempotent(api_client: APIClient, tokens: dict[str, str]) -> None:
    api_client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json")

    response = api_client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json")

    assert response.status_code == 204
    assert AuditLog.objects.filter(action=AuditAction.AUTH_LOGOUT).count() == 1


def test_logout_only_ends_that_device(api_client: APIClient, user: User) -> None:
    phone = login(api_client, user.email).json()
    laptop = login(api_client, user.email).json()

    api_client.post(LOGOUT_URL, {"refresh": phone["refresh"]}, format="json")

    assert refresh(api_client, laptop["refresh"]).status_code == 200


def test_logout_with_expired_token_is_204(api_client: APIClient, user: User) -> None:
    token = RefreshToken.for_user(user)
    token.set_exp(lifetime=timedelta(seconds=-1))

    response = api_client.post(LOGOUT_URL, {"refresh": str(token)}, format="json")

    assert response.status_code == 204


def test_logout_with_forged_token_is_401(api_client: APIClient) -> None:
    response = api_client.post(LOGOUT_URL, {"refresh": "not.a.token"}, format="json")

    assert response.status_code == 401
