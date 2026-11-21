"""Account lockout across IPs (architecture §20), keyed by email hash in the cache.

Unknown emails are counted exactly like real ones, so a lock never reveals whether an
account exists. Known trade-off: anyone can lock a victim's login for a while; the per-IP
login throttle limits how fast one client can do that.
"""

import hashlib
import math
import time
from datetime import timedelta

from django.core.cache import cache

FAILURE_THRESHOLD = 10
FAILURE_WINDOW = timedelta(minutes=15)
BASE_LOCK = timedelta(minutes=15)
MAX_LOCK = timedelta(hours=24)
# Repeated locks double in length until a full day passes without one.
STREAK_RESET = timedelta(hours=24)


def _key(kind: str, email: str) -> str:
    return f"auth:{kind}:{hashlib.sha256(email.encode()).hexdigest()}"


def seconds_until_unlock(email: str) -> int | None:
    unlock_at: float | None = cache.get(_key("lock", email))
    if unlock_at is None:
        return None
    remaining = unlock_at - time.time()
    return math.ceil(remaining) if remaining > 0 else None


def register_failure(email: str) -> int | None:
    """Count a failed login. Returns the lock duration in seconds if this failure locks."""
    failures_key = _key("failures", email)
    cache.add(failures_key, 0, timeout=int(FAILURE_WINDOW.total_seconds()))
    failures = cache.incr(failures_key)
    if failures < FAILURE_THRESHOLD:
        return None

    streak_key = _key("streak", email)
    cache.add(streak_key, 0, timeout=int(STREAK_RESET.total_seconds()))
    streak = cache.incr(streak_key)
    cache.touch(streak_key, timeout=int(STREAK_RESET.total_seconds()))

    lock_seconds = int(min(BASE_LOCK * 2 ** (streak - 1), MAX_LOCK).total_seconds())
    cache.set(_key("lock", email), time.time() + lock_seconds, timeout=lock_seconds)
    cache.delete(failures_key)
    return lock_seconds


def clear_failures(email: str) -> None:
    cache.delete(_key("failures", email))
