from config.settings.base import *  # noqa: F403  (settings module layering)

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]  # noqa: S104  (local Docker only)
API_DOCS_ENABLED = True
