from django.core.exceptions import ValidationError

from apps.customers.constants import ISO_3166_ALPHA2


def validate_country(value: str) -> None:
    if value not in ISO_3166_ALPHA2:
        raise ValidationError("Unknown ISO 3166-1 alpha-2 country code.", code="invalid_country")
