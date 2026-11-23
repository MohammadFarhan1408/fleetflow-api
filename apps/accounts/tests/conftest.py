from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts.factories import DEFAULT_TEST_PASSWORD, UserFactory
from apps.accounts.models import User

LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"
ME_URL = "/api/v1/me/"


@pytest.fixture
def user(db: None) -> User:
    return UserFactory.create(email="dispatch@example.com", roles=["dispatcher"])


def login(
    client: APIClient, email: str, password: str = DEFAULT_TEST_PASSWORD, **extra: Any
) -> Any:
    return client.post(LOGIN_URL, {"email": email, "password": password}, format="json", **extra)


@pytest.fixture
def tokens(api_client: APIClient, user: User) -> dict[str, str]:
    response = login(api_client, user.email)
    assert response.status_code == 200, response.content
    body: dict[str, str] = response.json()
    return body


def client_for(user: User) -> APIClient:
    """An API client authenticated as `user` through the real login flow."""
    client = APIClient()
    access = login(client, user.email).json()["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client
