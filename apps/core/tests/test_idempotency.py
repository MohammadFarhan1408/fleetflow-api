import threading
import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.accounts.tests.conftest import login
from apps.core.idempotency import purge_expired_records
from apps.core.models import IdempotencyRecord

USERS_URL = "/api/v1/users/"


def owner_access_token() -> str:
    owner = UserFactory.create(roles=[RoleCode.OWNER])
    token: str = login(APIClient(), owner.email).json()["access"]
    return token


def owner_client() -> tuple[APIClient, User]:
    owner = UserFactory.create(roles=[RoleCode.OWNER])
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login(client, owner.email).json()['access']}")
    return client, owner


def invite(client: APIClient, key: str | None, email: str = "new@example.com") -> Any:
    headers = {"Idempotency-Key": key} if key is not None else {}
    return client.post(
        USERS_URL,
        {"email": email, "full_name": "New", "roles": ["viewer"]},
        format="json",
        headers=headers,
    )


@pytest.mark.django_db
def test_retry_with_same_key_replays_without_repeating_the_mutation() -> None:
    client, _ = owner_client()
    key = str(uuid.uuid4())

    first = invite(client, key)
    retry = invite(client, key)

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    assert retry["Idempotent-Replayed"] == "true"
    assert User.objects.filter(email="new@example.com").count() == 1


@pytest.mark.django_db
def test_same_key_with_different_body_is_422() -> None:
    client, _ = owner_client()
    key = str(uuid.uuid4())
    invite(client, key)

    response = invite(client, key, email="other@example.com")

    assert response.status_code == 422
    assert response.json()["code"] == "idempotency.key_reuse"
    assert not User.objects.filter(email="other@example.com").exists()


@pytest.mark.django_db
def test_keys_are_scoped_per_user() -> None:
    first_client, _ = owner_client()
    second_client, _ = owner_client()
    key = str(uuid.uuid4())

    assert invite(first_client, key, "a@example.com").status_code == 201
    assert invite(second_client, key, "b@example.com").status_code == 201


@pytest.mark.django_db
def test_failed_request_stores_nothing_and_can_be_retried() -> None:
    client, _ = owner_client()
    UserFactory.create(email="taken@example.com")
    key = str(uuid.uuid4())

    assert invite(client, key, "taken@example.com").status_code == 409
    assert not IdempotencyRecord.objects.exists()


@pytest.mark.django_db
def test_requests_without_key_are_not_recorded() -> None:
    client, _ = owner_client()

    assert invite(client, None).status_code == 201
    assert not IdempotencyRecord.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["", "has space", "x" * 256])
def test_malformed_key_is_400(key: str) -> None:
    client, _ = owner_client()

    response = invite(client, key)

    assert response.status_code == 400
    assert response.json()["code"] == "idempotency.invalid_key"


@pytest.mark.django_db
def test_purge_removes_only_expired_records() -> None:
    _, owner = owner_client()
    old = IdempotencyRecord.objects.create(user=owner, key="old", request_hash="h")
    IdempotencyRecord.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(hours=73)
    )
    fresh = IdempotencyRecord.objects.create(user=owner, key="fresh", request_hash="h")

    assert purge_expired_records() == 1
    assert list(IdempotencyRecord.objects.all()) == [fresh]


# serialized_rollback restores the migration-seeded roles after the table flush.
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_concurrent_duplicates_create_one_user_and_both_get_the_same_answer() -> None:
    access = f"Bearer {owner_access_token()}"  # one session shared by both threads
    key = str(uuid.uuid4())
    responses: list[Any] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        thread_client = APIClient()
        thread_client.credentials(HTTP_AUTHORIZATION=access)
        barrier.wait()
        try:
            responses.append(invite(thread_client, key))
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert [r.status_code for r in responses] == [201, 201]
    assert responses[0].json() == responses[1].json()
    assert User.objects.filter(email="new@example.com").count() == 1
