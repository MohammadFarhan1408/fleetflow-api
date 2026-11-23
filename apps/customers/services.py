"""Customer and address-book writes (architecture §5). Views pass the acting user; every
service re-checks that the object belongs to the actor's company (rules.md §3)."""

from dataclasses import asdict, dataclass

from django.contrib.gis.geos import Point
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.accounts.models import User
from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.core.errors import DomainError, ErrorCode
from apps.customers.models import Address, Customer, CustomerKind, GeocodeConfidence


@dataclass(frozen=True)
class CustomerFields:
    kind: CustomerKind
    name: str
    email: str = ""
    phone: str = ""

    @classmethod
    def of(cls, customer: Customer) -> CustomerFields:
        return cls(
            kind=CustomerKind(customer.kind),
            name=customer.name,
            email=customer.email,
            phone=customer.phone,
        )


@dataclass(frozen=True)
class AddressFields:
    line1: str
    city: str
    country: str
    label: str = ""
    contact_name: str = ""
    phone: str = ""
    line2: str = ""
    region: str = ""
    postal_code: str = ""
    # Client-supplied (memory D-API-18); None means the address has no coordinates.
    location: Point | None = None

    @classmethod
    def of(cls, address: Address) -> AddressFields:
        return cls(
            line1=address.line1,
            city=address.city,
            country=address.country,
            label=address.label,
            contact_name=address.contact_name,
            phone=address.phone,
            line2=address.line2,
            region=address.region,
            postal_code=address.postal_code,
            location=address.location,
        )


def _ensure_same_company(actor: User, obj: Customer | Address) -> None:
    # Same answer as a missing object, so another tenant's data is never confirmed.
    if obj.company_id != actor.company_id:
        raise DomainError(ErrorCode.NOT_FOUND)


def _audit(
    action: AuditAction,
    actor: User,
    obj: Customer | Address,
    request: HttpRequest,
    after: dict[str, str] | None = None,
) -> None:
    # IDs and enums only: names, phones and address text stay out of the log (rules.md §3).
    audit.record(
        action,
        company=actor.company,
        actor=actor,
        object_type="customer" if isinstance(obj, Customer) else "address",
        object_id=str(obj.public_id),
        after=after,
        request=request,
    )


@transaction.atomic
def create_customer(*, actor: User, fields: CustomerFields, request: HttpRequest) -> Customer:
    customer = Customer(company=actor.company, **asdict(fields))
    customer.full_clean()
    customer.save()
    _audit(AuditAction.CUSTOMER_CREATED, actor, customer, request, after={"kind": customer.kind})
    return customer


@transaction.atomic
def update_customer(*, actor: User, customer: Customer, fields: CustomerFields) -> Customer:
    _ensure_same_company(actor, customer)
    # Locked so an update cannot land on a customer archived by a concurrent request.
    customer = Customer.objects.for_company(actor.company).select_for_update().get(pk=customer.pk)
    if customer.is_archived:
        raise DomainError(ErrorCode.CUSTOMER_ARCHIVED)
    for name, value in asdict(fields).items():
        setattr(customer, name, value)
    customer.full_clean()
    customer.save()
    return customer


@transaction.atomic
def archive_customer(*, actor: User, customer: Customer, request: HttpRequest) -> Customer:
    """Archive the customer and its addresses. Archiving twice is a no-op."""
    _ensure_same_company(actor, customer)
    customer = Customer.objects.for_company(actor.company).select_for_update().get(pk=customer.pk)
    if customer.is_archived:
        return customer
    now = timezone.now()
    customer.archived_at = now
    customer.save(update_fields=["archived_at", "updated_at"])
    customer.addresses.filter(archived_at__isnull=True).update(archived_at=now, updated_at=now)
    _audit(AuditAction.CUSTOMER_ARCHIVED, actor, customer, request)
    return customer


def _apply_address_fields(address: Address, fields: AddressFields) -> None:
    for name, value in asdict(fields).items():
        setattr(address, name, value)
    address.geocode_confidence = (
        GeocodeConfidence.NONE if fields.location is None else GeocodeConfidence.MANUAL
    )
    address.full_clean()


@transaction.atomic
def create_address(
    *, actor: User, customer: Customer, fields: AddressFields, request: HttpRequest
) -> Address:
    _ensure_same_company(actor, customer)
    # Locked so the address cannot be added to a customer being archived concurrently.
    customer = Customer.objects.for_company(actor.company).select_for_update().get(pk=customer.pk)
    if customer.is_archived:
        raise DomainError(ErrorCode.CUSTOMER_ARCHIVED)
    address = Address(company_id=customer.company_id, customer=customer)
    _apply_address_fields(address, fields)
    address.save()
    _audit(AuditAction.ADDRESS_CREATED, actor, address, request)
    return address


@transaction.atomic
def update_address(*, actor: User, address: Address, fields: AddressFields) -> Address:
    _ensure_same_company(actor, address)
    address = Address.objects.for_company(actor.company).select_for_update().get(pk=address.pk)
    if address.is_archived:
        raise DomainError(ErrorCode.ADDRESS_ARCHIVED)
    _apply_address_fields(address, fields)
    address.save()
    return address


@transaction.atomic
def archive_address(*, actor: User, address: Address, request: HttpRequest) -> Address:
    """Archive one address. Archiving twice is a no-op."""
    _ensure_same_company(actor, address)
    address = Address.objects.for_company(actor.company).select_for_update().get(pk=address.pk)
    if address.is_archived:
        return address
    address.archived_at = timezone.now()
    address.save(update_fields=["archived_at", "updated_at"])
    _audit(AuditAction.ADDRESS_ARCHIVED, actor, address, request)
    return address
