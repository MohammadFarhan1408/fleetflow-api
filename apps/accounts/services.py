from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import Role, RoleCode, User, normalize_email
from apps.accounts.tasks import send_invite_email
from apps.audit import services as audit
from apps.audit.models import AuditAction
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
