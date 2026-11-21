from collections.abc import Iterator

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    """Throttle and lockout counters live in the cache; never leak them between tests."""
    cache.clear()
    yield
    cache.clear()
