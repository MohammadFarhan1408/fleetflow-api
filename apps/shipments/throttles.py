from rest_framework.throttling import AnonRateThrottle


class PublicTrackingThrottle(AnonRateThrottle):
    """Per client IP on the unauthenticated tracking page (architecture §20: 60/min)."""

    scope = "public_tracking"
