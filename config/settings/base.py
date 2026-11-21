"""Settings shared by every environment. Environment-specific modules import from here."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# Local convenience only; real environments inject variables directly.
# FLEETFLOW_NO_DOTENV lets tests prove required variables are enforced.
if not env.bool("FLEETFLOW_NO_DOTENV", default=False):
    environ.Env.read_env(BASE_DIR / ".env")

# Required: fail fast at startup when missing (rules.md §10).
SECRET_KEY = env.str("DJANGO_SECRET_KEY")
DATABASES = {"default": env.db_url("DATABASE_URL")}
REDIS_URL = env.str("REDIS_URL")

DEBUG = False
ALLOWED_HOSTS: list[str] = env.list("DJANGO_ALLOWED_HOSTS", default=[])

# No Django admin, sessions or messages: the API authenticates with JWT only and has no
# server-rendered UI, so those apps would only add attack surface (memory D-API-12).
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "apps.core",
    "apps.tenancy",
    "apps.audit",
    "apps.accounts",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    # First, so every response (including early rejections below) carries the request ID.
    "apps.core.middleware.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.AppVersionMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": ["django.template.context_processors.request"],
        },
    },
]

DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Argon2 first (architecture §23); the others only verify legacy hashes.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # JWT authentication arrives in Phase 1 PR 3. Until then nothing authenticates, which
    # also removes DRF's implicit Session and Basic authentication defaults.
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    # Deny by default; every endpoint opts in to its access rules.
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "apps.core.exception_handler.exception_handler",
    # DRF's default (None) trusts the whole X-Forwarded-For header, which clients can
    # spoof. Set this to the number of trusted proxies in front of the app.
    "NUM_PROXIES": env.int("NUM_PROXIES", default=0),
}

# Interactive docs are served only where explicitly enabled (local). Clients consume the
# committed openapi/schema.yaml (architecture §25), so production needs no docs endpoint.
API_DOCS_ENABLED = env.bool("API_DOCS_ENABLED", default=False)

SPECTACULAR_SETTINGS = {
    "TITLE": "FleetFlow API",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
}

# Email: console locally; production must set EMAIL_URL (e.g. smtp+tls://user:pass@host:587).
EMAIL_CONFIG = env.email_url("EMAIL_URL", default="consolemail://")
vars().update(EMAIL_CONFIG)
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default="FleetFlow <no-reply@localhost>")

# Page in fleetflow-admin that completes password set/reset; the API appends uid and token.
PASSWORD_RESET_URL = env.str("PASSWORD_RESET_URL", default="http://localhost:3000/reset-password")

# Minimum driver-app version (e.g. "1.4.0"); empty disables the 426 check.
DRIVER_APP_MIN_VERSION = env.str("DRIVER_APP_MIN_VERSION", default="")

CELERY_BROKER_URL = REDIS_URL
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TIMEZONE = TIME_ZONE
