import pytest
from rest_framework.test import APIClient

from apps.accounts import lockout
from apps.accounts.models import User
from apps.accounts.tests.conftest import login
from apps.audit.models import AuditAction, AuditLog

pytestmark = pytest.mark.django_db


def test_sixth_login_attempt_in_a_minute_is_throttled(api_client: APIClient, user: User) -> None:
    for _ in range(5):
        assert login(api_client, user.email, "wrong-password").status_code == 401

    response = login(api_client, user.email, "wrong-password")

    assert response.status_code == 429
    assert response.json()["code"] == "throttled"
    assert int(response["Retry-After"]) > 0


def test_login_throttle_is_per_email(api_client: APIClient, user: User) -> None:
    for _ in range(5):
        login(api_client, "someone-else@example.com", "wrong-password")

    assert login(api_client, user.email).status_code == 200


def fail_from_many_ips(client: APIClient, email: str, attempts: int) -> list[int]:
    """Spread failures across IPs so only the cross-IP lockout (not the throttle) applies."""
    return [
        login(client, email, "wrong-password", REMOTE_ADDR=f"198.51.100.{n}").status_code
        for n in range(attempts)
    ]


def test_lockout_after_failures_across_ips(api_client: APIClient, user: User) -> None:
    statuses = fail_from_many_ips(api_client, user.email, lockout.FAILURE_THRESHOLD)

    assert statuses[:-1] == [401] * (lockout.FAILURE_THRESHOLD - 1)
    assert statuses[-1] == 429

    # Even the right password is refused while locked, from a fresh IP.
    response = login(api_client, user.email, REMOTE_ADDR="203.0.113.99")
    assert response.status_code == 429
    assert response.json()["code"] == "auth.account_locked"
    assert 0 < int(response["Retry-After"]) <= int(lockout.BASE_LOCK.total_seconds())
    assert AuditLog.objects.filter(action=AuditAction.AUTH_ACCOUNT_LOCKED).count() == 1


def test_unknown_email_locks_identically(api_client: APIClient, db: None) -> None:
    statuses = fail_from_many_ips(api_client, "ghost@example.com", lockout.FAILURE_THRESHOLD)

    assert statuses[-1] == 429
    response = login(api_client, "ghost@example.com", REMOTE_ADDR="203.0.113.99")
    assert response.json()["code"] == "auth.account_locked"
    assert not AuditLog.objects.exists()  # nothing to attribute it to


def test_successful_login_resets_failure_count(api_client: APIClient, user: User) -> None:
    fail_from_many_ips(api_client, user.email, lockout.FAILURE_THRESHOLD - 1)
    assert login(api_client, user.email, REMOTE_ADDR="203.0.113.50").status_code == 200

    statuses = fail_from_many_ips(api_client, user.email, lockout.FAILURE_THRESHOLD - 1)

    assert 429 not in statuses


def test_repeated_locks_double_up_to_the_cap(db: None) -> None:
    email = "victim@example.com"
    durations = []
    for _ in range(9):
        lock_seconds = None
        for _ in range(lockout.FAILURE_THRESHOLD):
            lock_seconds = lockout.register_failure(email)
        durations.append(lock_seconds)

    base = int(lockout.BASE_LOCK.total_seconds())
    assert durations[:3] == [base, base * 2, base * 4]
    assert durations[-1] == int(lockout.MAX_LOCK.total_seconds())


def test_lock_expires(db: None) -> None:
    email = "victim@example.com"
    for _ in range(lockout.FAILURE_THRESHOLD):
        lockout.register_failure(email)
    assert lockout.seconds_until_unlock(email) is not None

    from django.core.cache import cache

    cache.set(lockout._key("lock", email), 0.0)  # unlock time in the past

    assert lockout.seconds_until_unlock(email) is None
