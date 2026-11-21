from unittest import mock

import pytest
import redis
from django.db import OperationalError
from django.test import Client

pytestmark = pytest.mark.django_db


def test_live_returns_ok(client: Client) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_ok_when_dependencies_are_up(client: Client) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


def test_ready_returns_503_when_database_is_down(client: Client) -> None:
    secret_detail = "could not connect to server at db-internal.example:5432"
    with mock.patch(
        "django.db.backends.utils.CursorWrapper.execute",
        side_effect=OperationalError(secret_detail),
    ):
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "fail", "redis": "ok"},
    }
    assert secret_detail not in response.content.decode()


def test_ready_returns_503_when_redis_is_down(client: Client) -> None:
    secret_detail = "Error connecting to redis-internal.example:6379"
    with mock.patch("redis.Redis.ping", side_effect=redis.ConnectionError(secret_detail)):
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "redis": "fail"}
    assert secret_detail not in response.content.decode()


def test_health_rejects_non_get(client: Client) -> None:
    assert client.post("/health/live").status_code == 405
