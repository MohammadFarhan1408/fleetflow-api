"""Permission codenames owned by the accounts module (rules.md §10) and lookups.

Role → permission assignments live in the DB and are seeded by data migrations.
"""

from apps.accounts.models import Permission, User


def get_user_permissions(user: User) -> frozenset[str]:
    """Permission codenames granted to `user` through their roles.

    Memoized on the user instance, which lives for one request only. Never cache this
    across requests: role changes must take effect on the next request (architecture §22).
    """
    cached: frozenset[str] | None = getattr(user, "_permission_codenames", None)
    if cached is None:
        cached = frozenset(
            Permission.objects.filter(roles__users=user).values_list("codename", flat=True)
        )
        user._permission_codenames = cached  # type: ignore[attr-defined]  # per-request memo
    return cached
