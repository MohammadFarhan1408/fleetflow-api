from collections.abc import Iterator

import pytest
from django.core.cache import cache
from pytest_django import Settings
from rest_framework.test import APIClient

from apps.core import storage


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    """Throttle and lockout counters live in the cache; never leak them between tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(scope="session", autouse=True)
def _upload_bucket() -> None:
    """Tests run against the real S3-compatible store (architecture §26.1); make sure the
    bucket exists, as `ensure_upload_bucket` does for the running app."""
    storage.ensure_bucket()


@pytest.fixture(autouse=True)
def _public_endpoint_is_reachable_from_tests(settings: Settings) -> None:
    """Inside the compose network the bucket is reachable only at S3_ENDPOINT_URL, while
    presigned URLs normally carry the host-facing address; tests use the one that works."""
    settings.S3_PUBLIC_ENDPOINT_URL = settings.S3_ENDPOINT_URL
