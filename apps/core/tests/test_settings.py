"""Each settings module is imported in a fresh interpreter so one environment's
configuration cannot leak into another's."""

import os
import subprocess
import sys

import pytest

REQUIRED_ENV = {
    "DJANGO_SECRET_KEY": "test-only-secret",
    "DATABASE_URL": "postgres://user:pass@localhost:5432/fleetflow",
    "REDIS_URL": "redis://localhost:6379/0",
    "DJANGO_ALLOWED_HOSTS": "api.example.com",
    "EMAIL_URL": "smtp+tls://mailer:secret@smtp.example.com:587",
    "DEFAULT_FROM_EMAIL": "FleetFlow <no-reply@example.com>",
    "PASSWORD_RESET_URL": "https://admin.example.com/reset-password",
}

PRINT_DEBUG = "from django.conf import settings; print(settings.DEBUG)"


def run_with_settings(module: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    # Drop inherited values so the test controls exactly which variables exist;
    # the dotenv path is pointed at nothing so a developer's .env cannot mask a gap.
    base_env = {k: v for k, v in os.environ.items() if k not in REQUIRED_ENV}
    return subprocess.run(
        [sys.executable, "-c", f"import django; django.setup(); {PRINT_DEBUG}"],
        env={**base_env, **env, "DJANGO_SETTINGS_MODULE": module, "FLEETFLOW_NO_DOTENV": "1"},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("module", "expected_debug"),
    [
        ("config.settings.local", "True"),
        ("config.settings.test", "False"),
        ("config.settings.production", "False"),
    ],
)
def test_settings_module_loads(module: str, expected_debug: str) -> None:
    result = run_with_settings(module, REQUIRED_ENV)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected_debug


@pytest.mark.parametrize("missing", ["DJANGO_SECRET_KEY", "DATABASE_URL", "REDIS_URL"])
def test_production_fails_fast_without_required_env(missing: str) -> None:
    env = {k: v for k, v in REQUIRED_ENV.items() if k != missing}

    result = run_with_settings("config.settings.production", env)

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr


@pytest.mark.parametrize(
    "missing",
    ["DJANGO_ALLOWED_HOSTS", "EMAIL_URL", "DEFAULT_FROM_EMAIL", "PASSWORD_RESET_URL"],
)
def test_production_requires_its_own_settings(missing: str) -> None:
    env = {k: v for k, v in REQUIRED_ENV.items() if k != missing}

    result = run_with_settings("config.settings.production", env)

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr


def test_local_settings_do_not_need_production_only_values() -> None:
    env = {
        k: v
        for k, v in REQUIRED_ENV.items()
        if k in {"DJANGO_SECRET_KEY", "DATABASE_URL", "REDIS_URL"}
    }

    result = run_with_settings("config.settings.local", env)

    assert result.returncode == 0, result.stderr
