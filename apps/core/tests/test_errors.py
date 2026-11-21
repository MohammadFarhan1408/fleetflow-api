import uuid
from typing import Any

import pytest
from django.test import Client
from rest_framework.test import APIClient

pytestmark = pytest.mark.urls("apps.core.tests.urls")

ENVELOPE_KEYS = {"type", "title", "status", "code", "detail", "request_id"}


def assert_envelope(body: dict[str, Any], status: int, code: str) -> None:
    assert body.keys() >= ENVELOPE_KEYS
    assert body["status"] == status
    assert body["code"] == code
    uuid.UUID(body["request_id"])


def test_validation_error_has_field_errors(api_client: APIClient) -> None:
    response = api_client.post("/api/v1/test/validation/", {"weight_kg": 0}, format="json")

    assert response.status_code == 400
    body = response.json()
    assert_envelope(body, 400, "validation.invalid")
    assert body["type"] == "validation_error"
    assert "weight_kg" in body["errors"]


def test_serializer_level_error_uses_non_field_errors(api_client: APIClient) -> None:
    response = api_client.post(
        "/api/v1/test/validation/", {"weight_kg": 5, "max_weight_kg": 2}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["errors"] == {"non_field_errors": ["weight_kg exceeds max_weight_kg."]}


def test_django_http404_in_view_is_envelope_without_detail_leak(api_client: APIClient) -> None:
    response = api_client.get("/api/v1/test/django-404/")

    assert response.status_code == 404
    assert_envelope(response.json(), 404, "not_found")
    assert "company 7" not in response.content.decode()


def test_django_permission_denied_in_view_is_403(api_client: APIClient) -> None:
    response = api_client.get("/api/v1/test/django-403/")

    assert response.status_code == 403
    assert_envelope(response.json(), 403, "permission.denied")


def test_malformed_json_is_400(api_client: APIClient) -> None:
    response = api_client.post(
        "/api/v1/test/validation/", "{not json", content_type="application/json"
    )

    assert response.status_code == 400
    assert_envelope(response.json(), 400, "request.malformed")


def test_unsupported_media_type_is_415(api_client: APIClient) -> None:
    response = api_client.post("/api/v1/test/validation/", "a=1", content_type="text/plain")

    assert response.status_code == 415
    assert_envelope(response.json(), 415, "unsupported_media_type")


def test_domain_error_renders_code_detail_errors_and_headers(api_client: APIClient) -> None:
    response = api_client.get("/api/v1/test/domain/")

    assert response.status_code == 400
    body = response.json()
    assert_envelope(body, 400, "validation.invalid_reference")
    assert body["detail"] == "Branch not found."
    assert body["errors"] == {"branch": ["not found"]}
    assert response["X-Test"] == "1"


def test_throttled_sets_retry_after_rounded_up(api_client: APIClient) -> None:
    response = api_client.get("/api/v1/test/throttled/")

    assert response.status_code == 429
    assert_envelope(response.json(), 429, "throttled")
    assert response["Retry-After"] == "13"


def test_unauthenticated_request_is_401_with_bearer_challenge(api_client: APIClient) -> None:
    response = api_client.get("/api/v1/test/protected/")

    assert response.status_code == 401
    assert_envelope(response.json(), 401, "auth.not_authenticated")
    assert response["WWW-Authenticate"].startswith("Bearer")


def test_method_not_allowed_is_405(api_client: APIClient) -> None:
    response = api_client.delete("/api/v1/test/domain/")

    assert response.status_code == 405
    assert_envelope(response.json(), 405, "method_not_allowed")


def test_unknown_url_is_json_404() -> None:
    response = Client().get("/api/v1/does-not-exist/")

    assert response.status_code == 404
    assert response["Content-Type"] == "application/json"
    assert_envelope(response.json(), 404, "not_found")


def test_disallowed_host_is_json_400() -> None:
    response = Client().get("/api/v1/test/domain/", HTTP_HOST="evil.example")

    assert response.status_code == 400
    assert response["Content-Type"] == "application/json"
    assert_envelope(response.json(), 400, "request.malformed")


def test_django_permission_denied_outside_drf_is_json_403() -> None:
    response = Client().get("/test/django-forbidden/")

    assert response.status_code == 403
    assert_envelope(response.json(), 403, "permission.denied")


def test_unhandled_api_exception_is_500_without_internals(
    api_client: APIClient, caplog: pytest.LogCaptureFixture
) -> None:
    response = api_client.get("/api/v1/test/crash/")

    assert response.status_code == 500
    assert_envelope(response.json(), 500, "server_error")
    assert "hunter2" not in response.content.decode()
    assert "Unhandled exception" in caplog.text


def test_unhandled_django_exception_is_json_500() -> None:
    response = Client(raise_request_exception=False).get("/test/django-crash/")

    assert response.status_code == 500
    assert response["Content-Type"] == "application/json"
    assert_envelope(response.json(), 500, "server_error")
    assert "secret" not in response.content.decode()
