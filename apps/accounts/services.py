import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from rest_framework_simplejwt.settings import api_settings

from apps.accounts import lockout
from apps.accounts.authentication import ensure_account_usable
from apps.accounts.models import (
    RefreshTokenRecord,
    Role,
    RoleCode,
    TokenFamily,
    TokenFamilyRevokedReason,
    User,
    normalize_email,
)
from apps.accounts.tasks import send_invite_email, send_password_reset_email
from apps.accounts.tokens import (
    TokenPair,
    decode_refresh_token,
    issue_token_pair,
    token_error,
)
from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.core.errors import DomainError, ErrorCode
from apps.tenancy import services as tenancy
from apps.tenancy.models import Branch, Company


@transaction.atomic
def create_company_with_owner(
    *,
    company_name: str,
    currency: str,
    timezone: str,
    branch_name: str,
    branch_code: str,
    owner_email: str,
    owner_name: str,
) -> tuple[Company, User]:
    """Onboard a tenant: the company, its first branch, its first owner (a member of that
    branch), and the owner's invite email. Shipments need a branch, and no branch API
    exists yet.

    The owner has no usable password until they follow the emailed link.
    """
    company = tenancy.create_company(name=company_name, currency=currency, timezone=timezone)
    branch = tenancy.create_branch(company=company, name=branch_name, code=branch_code)
    audit.record(
        AuditAction.COMPANY_CREATED,
        company=company,
        object_type="company",
        object_id=str(company.public_id),
        after={
            "name": company.name,
            "currency": company.currency,
            "timezone": company.timezone,
            "branch_code": branch.code,
        },
    )
    owner = _create_invited_user(
        company=company,
        email=owner_email,
        full_name=owner_name,
        roles=[RoleCode.OWNER],
        branches=[branch],
        actor=None,
        request=None,
    )
    return company, owner


# --- User management (users API) -----------------------------------------------------


@dataclass(frozen=True)
class UserChanges:
    """Fields a PATCH may change; None means "leave as is"."""

    full_name: str | None = None
    roles: list[RoleCode] | None = None
    branch_ids: list[uuid.UUID] | None = None
    is_active: bool | None = None


def invite_user(
    *,
    actor: User,
    email: str,
    full_name: str,
    roles: list[RoleCode],
    branch_ids: list[uuid.UUID],
    request: HttpRequest,
) -> User:
    """Create a user in the actor's company and email them a link to set a password."""
    if RoleCode.OWNER in roles and not _is_owner(actor):
        raise DomainError(ErrorCode.USER_OWNER_REQUIRED)
    with transaction.atomic():
        return _create_invited_user(
            company=actor.company,
            email=email,
            full_name=full_name,
            roles=roles,
            branches=_resolve_branches(actor.company, branch_ids),
            actor=actor,
            request=request,
        )


def update_user(*, actor: User, target: User, changes: UserChanges, request: HttpRequest) -> User:
    """Apply a users-API PATCH with the ownership guards (decision D-API-11).

    - Users cannot change their own roles, branches or active status.
    - Only owners may grant or revoke the owner role, or change an owner's access (roles,
      branches, status). Admins may still edit an owner's name.
    - The company always keeps at least one active owner.
    """
    # Defence in depth: the view resolves `target` through the tenant-scoped selector, but
    # the service must not rely on that (rules.md §3). Same answer as a missing user.
    if target.company_id != actor.company_id:
        raise DomainError(ErrorCode.NOT_FOUND)

    with transaction.atomic():
        # Serializes concurrent membership edits in one company, so two requests cannot
        # each remove "the other" owner and leave none.
        Company.objects.select_for_update().get(pk=actor.company_id)
        target = User.objects.select_for_update().get(pk=target.pk)

        old_roles = set(target.roles.values_list("code", flat=True))
        new_roles = set(changes.roles) if changes.roles is not None else old_roles
        touches_access = (
            changes.roles is not None
            or changes.branch_ids is not None
            or changes.is_active is not None
        )

        if target.pk == actor.pk and touches_access:
            raise DomainError(ErrorCode.USER_CANNOT_MODIFY_SELF)
        if touches_access and RoleCode.OWNER in (old_roles | new_roles) and not _is_owner(actor):
            raise DomainError(ErrorCode.USER_OWNER_REQUIRED)

        deactivating = changes.is_active is False and target.is_active
        losing_owner = RoleCode.OWNER in old_roles and (
            RoleCode.OWNER not in new_roles or deactivating
        )
        if losing_owner and not _other_active_owner_exists(target):
            raise DomainError(ErrorCode.USER_LAST_OWNER)

        if changes.full_name is not None:
            target.full_name = changes.full_name.strip()
            target.full_clean(exclude=["password"])
            target.save(update_fields=["full_name", "updated_at"])

        if changes.roles is not None and new_roles != old_roles:
            target.roles.set(Role.objects.filter(code__in=new_roles))
            _audit_user(
                AuditAction.USER_ROLES_CHANGED,
                actor,
                target,
                request,
                before={"roles": sorted(old_roles)},
                after={"roles": sorted(new_roles)},
            )

        if changes.branch_ids is not None:
            old_branches = {str(b) for b in target.branches.values_list("public_id", flat=True)}
            branches = _resolve_branches(actor.company, changes.branch_ids)
            new_branches = {str(b.public_id) for b in branches}
            if new_branches != old_branches:
                target.branches.set(branches)
                _audit_user(
                    AuditAction.USER_BRANCHES_CHANGED,
                    actor,
                    target,
                    request,
                    before={"branches": sorted(old_branches)},
                    after={"branches": sorted(new_branches)},
                )

        if changes.is_active is not None and changes.is_active != target.is_active:
            target.is_active = changes.is_active
            target.save(update_fields=["is_active", "updated_at"])
            if deactivating:
                # Ends every session now; the per-request check also rejects the user.
                _revoke_families(target.token_families.all(), TokenFamilyRevokedReason.DEACTIVATED)
            _audit_user(
                AuditAction.USER_DEACTIVATED if deactivating else AuditAction.USER_REACTIVATED,
                actor,
                target,
                request,
            )
    return target


def _create_invited_user(
    *,
    company: Company,
    email: str,
    full_name: str,
    roles: list[RoleCode],
    branches: list[Branch],
    actor: User | None,
    request: HttpRequest | None,
) -> User:
    """Create a user with no usable password, assign access, audit, and send the invite.

    Must run inside a transaction: the email goes out only after commit.
    """
    email = normalize_email(email)
    # Emails are unique across all companies (one login identity per person).
    if User.objects.filter(email=email).exists():
        raise DomainError(ErrorCode.USER_EMAIL_TAKEN, errors={"email": ["Already in use."]})
    user = User.objects.create_user(email=email, full_name=full_name, company=company)
    user.roles.set(Role.objects.filter(code__in=roles))
    user.branches.set(branches)
    audit.record(
        AuditAction.USER_CREATED,
        company=company,
        actor=actor,
        object_type="user",
        object_id=str(user.public_id),
        after={
            "email": user.email,
            "roles": sorted(roles),
            "branches": sorted(str(b.public_id) for b in branches),
        },
        request=request,
    )
    transaction.on_commit(lambda: send_invite_email.delay(user.pk))
    return user


def _resolve_branches(company: Company, branch_ids: list[uuid.UUID]) -> list[Branch]:
    """Branches of `company` by public ID. Unknown or other-tenant IDs are reported the
    same way, so the response never reveals another company's data."""
    wanted = set(branch_ids)
    branches = list(Branch.objects.for_company(company).filter(public_id__in=wanted))
    missing = wanted - {b.public_id for b in branches}
    if missing:
        raise DomainError(
            ErrorCode.VALIDATION_INVALID_REFERENCE,
            errors={"branches": [f"Unknown branch {branch_id}." for branch_id in sorted(missing)]},
        )
    return branches


def _is_owner(user: User) -> bool:
    return user.roles.filter(code=RoleCode.OWNER).exists()


def _other_active_owner_exists(user: User) -> bool:
    return (
        User.objects.for_company(user.company)
        .filter(is_active=True, roles__code=RoleCode.OWNER)
        .exclude(pk=user.pk)
        .exists()
    )


def _audit_user(
    action: AuditAction,
    actor: User,
    target: User,
    request: HttpRequest,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    audit.record(
        action,
        company=target.company,
        actor=actor,
        object_type="user",
        object_id=str(target.public_id),
        before=before,
        after=after,
        request=request,
    )


# --- Authentication (architecture §7) ------------------------------------------------

# A refresh token reused within this window after its rotation gets a fresh pair instead
# of revoking the session: mobile clients lose responses and retry (decision D-API-11).
REFRESH_REUSE_GRACE = timedelta(seconds=30)


def login(*, email: str, password: str, request: HttpRequest) -> TokenPair:
    email = normalize_email(email)
    locked_for = lockout.seconds_until_unlock(email)
    if locked_for is not None:
        raise _locked(locked_for)

    # AllowAllUsersModelBackend runs the password hasher even for unknown emails, so
    # response timing does not reveal which accounts exist.
    authenticated = authenticate(request, username=email, password=password)
    if not isinstance(authenticated, User):
        lock_seconds = lockout.register_failure(email)
        if lock_seconds is not None:
            _audit_lock(email, request)
            raise _locked(lock_seconds)
        raise DomainError(ErrorCode.AUTH_INVALID_CREDENTIALS)

    lockout.clear_failures(email)
    user = User.objects.select_related("company").get(pk=authenticated.pk)
    # Only after the password is verified, so these codes cannot be used to probe emails.
    ensure_account_usable(user)

    with transaction.atomic():
        family = TokenFamily.objects.create(user=user)
        pair = issue_token_pair(user, family)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        audit.record(
            AuditAction.AUTH_LOGIN,
            company=user.company,
            actor=user,
            object_type="user",
            object_id=str(user.public_id),
            request=request,
        )
    return pair


def refresh_session(*, raw_refresh: str, request: HttpRequest) -> TokenPair:
    payload = decode_refresh_token(raw_refresh)
    reuse_detected = False

    with transaction.atomic():
        record = (
            RefreshTokenRecord.objects.select_for_update(of=("self",))
            .select_related("family__user__company")
            .filter(jti=payload[api_settings.JTI_CLAIM])
            .first()
        )
        if record is None or record.family.revoked_at is not None:
            raise token_error(ErrorCode.AUTH_TOKEN_INVALID)

        family = record.family
        user = family.user
        now = timezone.now()
        if record.used_at is None:
            record.used_at = now
            record.save(update_fields=["used_at"])
        elif now - record.used_at > REFRESH_REUSE_GRACE:
            # A rotated token presented again outside the grace window: assume theft and
            # end the whole session for every holder of this family.
            _revoke_families(
                TokenFamily.objects.filter(pk=family.pk), TokenFamilyRevokedReason.REUSE
            )
            audit.record(
                AuditAction.AUTH_REFRESH_REUSE,
                company=user.company,
                actor=user,
                object_type="token_family",
                object_id=str(family.id),
                request=request,
            )
            reuse_detected = True

        if not reuse_detected:
            ensure_account_usable(user)
            return issue_token_pair(user, family)

    # Raised after the block commits, so the revocation and audit entry are kept.
    raise token_error(ErrorCode.AUTH_TOKEN_INVALID)


def logout(*, raw_refresh: str, request: HttpRequest) -> None:
    """End the session (token family) the refresh token belongs to. Idempotent."""
    try:
        payload = decode_refresh_token(raw_refresh)
    except DomainError as exc:
        if exc.error_code == ErrorCode.AUTH_TOKEN_EXPIRED:
            return  # the session is already over
        raise

    with transaction.atomic():
        record = (
            RefreshTokenRecord.objects.select_related("family__user__company")
            .filter(jti=payload[api_settings.JTI_CLAIM])
            .first()
        )
        if record is None or record.family.revoked_at is not None:
            return
        user = record.family.user
        _revoke_families(
            TokenFamily.objects.filter(pk=record.family_id), TokenFamilyRevokedReason.LOGOUT
        )
        audit.record(
            AuditAction.AUTH_LOGOUT,
            company=user.company,
            actor=user,
            object_type="token_family",
            object_id=str(record.family.id),
            request=request,
        )


def request_password_reset(*, email: str) -> None:
    """Email a reset link if an active account exists. Callers always answer the same way."""
    user = User.objects.filter(email=normalize_email(email), is_active=True).first()
    if user is not None:
        send_password_reset_email.delay(user.pk)


def confirm_password_reset(
    *, uid: uuid.UUID, token: str, new_password: str, request: HttpRequest
) -> None:
    """Set a new password from an emailed link (reset or invite) and end every session."""
    user = User.objects.select_related("company").filter(public_id=uid, is_active=True).first()
    # One error for unknown user and bad token, so the endpoint cannot probe accounts.
    if user is None or not default_token_generator.check_token(user, token):
        raise DomainError(ErrorCode.AUTH_INVALID_RESET_TOKEN)

    try:
        validate_password(new_password, user)
    except ValidationError as exc:
        raise DomainError(
            ErrorCode.VALIDATION_INVALID, errors={"new_password": exc.messages}
        ) from exc

    with transaction.atomic():
        user.set_password(new_password)
        user.save(update_fields=["password"])
        _revoke_families(user.token_families.all(), TokenFamilyRevokedReason.PASSWORD_RESET)
        audit.record(
            AuditAction.AUTH_PASSWORD_RESET,
            company=user.company,
            actor=user,
            object_type="user",
            object_id=str(user.public_id),
            request=request,
        )
    lockout.clear_failures(user.email)


def _revoke_families(families: QuerySet[TokenFamily], reason: TokenFamilyRevokedReason) -> None:
    families.filter(revoked_at__isnull=True).update(
        revoked_at=timezone.now(), revoked_reason=reason
    )


def _locked(seconds: int) -> DomainError:
    return DomainError(ErrorCode.AUTH_ACCOUNT_LOCKED, headers={"Retry-After": str(seconds)})


def _audit_lock(email: str, request: HttpRequest) -> None:
    user = User.objects.select_related("company").filter(email=email).first()
    if user is not None:
        audit.record(
            AuditAction.AUTH_ACCOUNT_LOCKED,
            company=user.company,
            object_type="user",
            object_id=str(user.public_id),
            request=request,
        )
