from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

from apps.customers.constants import ISO_3166_ALPHA2

# E.164 shape only (memory D-API-19): a plus sign and up to 15 digits, no leading zero.
validate_e164 = RegexValidator(
    r"^\+[1-9]\d{1,14}$", "Use international E.164 format, e.g. +14155550123."
)


def validate_country(value: str) -> None:
    if value not in ISO_3166_ALPHA2:
        raise ValidationError("Unknown ISO 3166-1 alpha-2 country code.", code="invalid_country")
