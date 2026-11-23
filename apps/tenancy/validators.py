import zoneinfo
from functools import cache

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

from apps.tenancy.constants import ISO_4217_CODES

BRANCH_CODE_PATTERN = r"^[A-Z0-9]{2,10}$"
# Branch codes appear in tracking numbers (FF-<BRANCH>-<seq>), so they stay short and plain.
validate_branch_code = RegexValidator(
    BRANCH_CODE_PATTERN, "Use 2 to 10 uppercase letters or digits."
)


@cache
def _iana_timezones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


def validate_currency(value: str) -> None:
    if value not in ISO_4217_CODES:
        raise ValidationError("Unknown ISO 4217 currency code.", code="invalid_currency")


def validate_timezone(value: str) -> None:
    if value not in _iana_timezones():
        raise ValidationError("Unknown IANA timezone.", code="invalid_timezone")
