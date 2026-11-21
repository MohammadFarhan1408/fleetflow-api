from config.settings.base import *  # noqa: F403  (settings module layering)
from config.settings.base import SPECTACULAR_SETTINGS

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]  # noqa: S104  (local Docker only)

SPECTACULAR_SETTINGS = {
    **SPECTACULAR_SETTINGS,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
}
