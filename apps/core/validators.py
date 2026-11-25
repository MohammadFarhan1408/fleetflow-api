from django.core.validators import RegexValidator

# E.164 shape only (memory D-API-19): a plus sign and up to 15 digits, no leading zero.
validate_e164 = RegexValidator(
    r"^\+[1-9]\d{1,14}$", "Use international E.164 format, e.g. +14155550123."
)
