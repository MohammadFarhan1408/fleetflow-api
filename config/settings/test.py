from config.settings.base import *  # noqa: F403  (settings module layering)

# Fast hashing in tests only; production keeps Argon2.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
