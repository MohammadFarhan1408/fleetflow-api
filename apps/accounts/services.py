import uuid
from datetime import timedelta

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
from apps.tenancy.models import Company


@transaction.atomic
def create_company_with_owner(
    *, company_name: str, owner_email: str, owner_name: str
) -> tuple[Company, User]:
    """Onboard a tenant: the company, its first owner, and the owner's invite email.

    The owner has no usable password until they follow the emailed link.
    """
    email = normalize_email(owner_email)
    if User.objects.filter(email=email).exists():
        raise ValidationError({"owner_email": "A user with this email already exists."})

    company = tenancy.create_company(name=company_name)
    owner = User.objects.create_user(email=email, full_name=owner_name, company=company)
    owner.roles.add(Role.objects.get(code=RoleCode.OWNER))

    audit.record(
        AuditAction.COMPANY_CREATED,
        company=company,
        object_type="company",
        object_id=str(company.public_id),
        after={"name": company.name},
    )
    audit.record(
        AuditAction.USER_CREATED,
        company=company,
        object_type="user",
        object_id=str(owner.public_id),
        after={"email": owner.email, "roles": [RoleCode.OWNER.value]},
    )
    transaction.on_commit(lambda: send_invite_email.delay(owner.pk))
    return company, owner


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
