import re
from datetime import timedelta

MAX_VEHICLE_WEIGHT_KG = 60_000
MAX_VEHICLE_VOLUME_M3 = 200
# Longest single leave. Longer absences are recorded as several periods.
MAX_LEAVE_DAYS = 366
# Upper-case letters and digits, with single spaces or hyphens inside (1-20 characters).
REGISTRATION_PATTERN = r"^[A-Z0-9][A-Z0-9 -]{0,18}[A-Z0-9]$"
REGISTRATION_RE = re.compile(REGISTRATION_PATTERN)

# A shift start or end may carry the device's clock (it can be queued offline). A little
# future is tolerated for clock skew; the past is bounded so a stale queue cannot rewrite
# history (architecture §12 uses the same 5 minutes).
SHIFT_MAX_FUTURE_SKEW = timedelta(minutes=5)
SHIFT_MAX_BACKDATE = timedelta(hours=24)
