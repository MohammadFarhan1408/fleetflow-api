from rest_framework.throttling import UserRateThrottle


class UploadCreateThrottle(UserRateThrottle):
    """Per user on upload creation (architecture §20)."""

    scope = "uploads"
