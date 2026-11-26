"""Permission codenames owned by the accounts module (rules.md §10) and lookups.

Role → permission assignments live in the DB and are seeded by data migrations.
"""

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.accounts.models import Permission, User

USERS_VIEW = "users.view"
USERS_MANAGE = "users.manage"
BRANCHES_MANAGE = "branches.manage"


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


class HasRequiredPermission(BasePermission):
    """Grants a request only if the user holds the codename the view maps to its method.

    Views declare `required_permissions = {"GET": USERS_VIEW, "POST": USERS_MANAGE}`.
    A handler the view implements but does not map (including OPTIONS) is denied, so a
    new handler cannot ship without a permission. Methods the view does not implement
    pass through so DRF answers 405 rather than a misleading 403. Combine with
    IsAuthenticated first, so anonymous requests get 401 rather than 403.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        method = request.method or ""
        if not hasattr(view, method.lower()):
            return True  # DRF responds 405 Method Not Allowed
        required: dict[str, str] = getattr(view, "required_permissions", {})
        codename = required.get(method)
        if codename is None or not isinstance(request.user, User):
            return False
        return codename in get_user_permissions(request.user)
