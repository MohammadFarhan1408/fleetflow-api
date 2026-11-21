import threading
from collections.abc import Callable

import pytest
from django.db import connection
from django.test import RequestFactory

from apps.accounts import services
from apps.accounts.factories import UserFactory
from apps.accounts.models import TokenFamily
from apps.accounts.tokens import TokenPair, issue_token_pair
from apps.core.errors import DomainError


@pytest.mark.django_db(transaction=True)
def test_concurrent_refreshes_of_one_token_both_succeed_and_keep_the_session() -> None:
    user = UserFactory.create()
    pair = issue_token_pair(user, TokenFamily.objects.create(user=user))
    results: list[TokenPair | DomainError] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        request = RequestFactory().post("/api/v1/auth/refresh/")
        barrier.wait()
        try:
            results.append(services.refresh_session(raw_refresh=pair.refresh, request=request))
        except DomainError as exc:
            results.append(exc)
        finally:
            connection.close()

    threads: list[threading.Thread] = []
    target: Callable[[], None] = worker
    for _ in range(2):
        thread = threading.Thread(target=target)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()

    assert all(isinstance(result, TokenPair) for result in results), results
    assert TokenFamily.objects.get().revoked_at is None
