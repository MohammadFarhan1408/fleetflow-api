import uuid

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.core.middleware import AppVersionMiddleware, parse_version

pytestmark = [pytest.mark.urls("apps.core.tests.urls"), pytest.mark.django_db]

API_PATH = "/api/v1/test/domain/"  # any API path; it answers 400 when reached


def test_request_id_generated_and_echoed() -> None:
    response = Client().get("/health/live", HTTP_HOST="localhost")

    uuid.UUID(response["X-Request-ID"])


def test_valid_incoming_request_id_is_kept() -> None:
    incoming = str(uuid.uuid4())

    response = Client().get(API_PATH, headers={"X-Request-ID": incoming})

    assert response["X-Request-ID"] == incoming
    assert response.json()["request_id"] == incoming


def test_non_uuid_request_id_is_replaced() -> None:
    response = Client().get(API_PATH, headers={"X-Request-ID": "evil\nlog-line"})

    assert response["X-Request-ID"] != "evil\nlog-line"
    uuid.UUID(response["X-Request-ID"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.2.3", (1, 2, 3)),
        (" 10.0.1 ", (10, 0, 1)),
        ("1.2", None),
        ("1.2.3-beta", None),
        ("", None),
    ],
)
def test_parse_version(value: str, expected: tuple[int, int, int] | None) -> None:
    assert parse_version(value) == expected


@pytest.fixture
def min_version(settings: SettingsWrapper) -> None:
    settings.DRIVER_APP_MIN_VERSION = "1.4.0"


@pytest.mark.usefixtures("min_version")
@pytest.mark.parametrize("version", ["1.3.9", "0.9.0"])
def test_old_app_version_gets_426(version: str) -> None:
    response = Client().get(API_PATH, headers={"X-App-Version": version})

    assert response.status_code == 426
    assert response.json()["code"] == "app.update_required"


@pytest.mark.usefixtures("min_version")
@pytest.mark.parametrize("version", ["1.4.0", "1.10.0", "2.0.0"])
def test_current_app_version_passes(version: str) -> None:
    response = Client().get(API_PATH, headers={"X-App-Version": version})

    assert response.status_code == 400  # reached the view


@pytest.mark.usefixtures("min_version")
def test_malformed_app_version_is_400() -> None:
    response = Client().get(API_PATH, headers={"X-App-Version": "latest"})

    assert response.status_code == 400
    assert response.json()["code"] == "app.invalid_version"


@pytest.mark.usefixtures("min_version")
def test_requests_without_header_are_not_checked() -> None:
    assert Client().get(API_PATH).json()["code"] == "validation.invalid_reference"


@pytest.mark.usefixtures("min_version")
def test_non_api_paths_are_not_checked() -> None:
    response = Client().get("/health/live", headers={"X-App-Version": "0.0.1"})

    assert response.status_code == 200


def test_check_disabled_when_min_version_empty(settings: SettingsWrapper) -> None:
    settings.DRIVER_APP_MIN_VERSION = ""

    response = Client().get(API_PATH, headers={"X-App-Version": "0.0.1"})

    assert response.status_code == 400
    assert response.json()["code"] == "validation.invalid_reference"


def test_misconfigured_min_version_fails_fast(settings: SettingsWrapper) -> None:
    settings.DRIVER_APP_MIN_VERSION = "v1"

    def get_response(request: HttpRequest) -> HttpResponse:
        return HttpResponse()

    with pytest.raises(ImproperlyConfigured):
        AppVersionMiddleware(get_response)
