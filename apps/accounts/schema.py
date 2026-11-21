from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.openapi import AutoSchema


class JWTAuthenticationScheme(OpenApiAuthenticationExtension):
    """Documents bearer JWT auth in the OpenAPI schema. Registered on import (apps.py)."""

    target_class = "apps.accounts.authentication.JWTAuthentication"
    name = "jwtAuth"

    def get_security_definition(self, auto_schema: AutoSchema) -> dict[str, Any]:
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
