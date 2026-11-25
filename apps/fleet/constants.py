import re

MAX_VEHICLE_WEIGHT_KG = 60_000
MAX_VEHICLE_VOLUME_M3 = 200
# Longest single leave. Longer absences are recorded as several periods.
MAX_LEAVE_DAYS = 366
# Upper-case letters and digits, with single spaces or hyphens inside (1-20 characters).
REGISTRATION_PATTERN = r"^[A-Z0-9][A-Z0-9 -]{0,18}[A-Z0-9]$"
REGISTRATION_RE = re.compile(REGISTRATION_PATTERN)
