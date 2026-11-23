from django.core.exceptions import ImproperlyConfigured

from config.settings.base import *  # noqa: F403  (settings module layering)
from config.settings.base import JWT_SIGNING_KEY, env

DEBUG = False

# HS256 tokens can be brute-forced offline if the key is short (RFC 7518 §3.2: at least
# 256 bits). Refuse to start rather than issue guessable tokens.
if len(JWT_SIGNING_KEY) < 32:
    raise ImproperlyConfigured("JWT_SIGNING_KEY must be at least 32 characters in production.")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

# Required in production: no silent fallback to the console backend or localhost links.
EMAIL_CONFIG = env.email_url("EMAIL_URL")
vars().update(EMAIL_CONFIG)
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL")
PASSWORD_RESET_URL = env.str("PASSWORD_RESET_URL")

# TLS terminates at the load balancer, which sets X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = "DENY"
# Health probes come over plain HTTP from the orchestrator inside the network.
SECURE_REDIRECT_EXEMPT = [r"^health/"]
