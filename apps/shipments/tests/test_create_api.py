"""Shipment creation (architecture §26.4 "Shipment creation")."""

import uuid
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import RoleCode
from apps.accounts.tests.conftest import client_for
from apps.audit.models import AuditAction, AuditLog
from apps.customers.factories import AddressFactory, CustomerFactory
from apps.customers.models import Address, Customer
from apps.shipments.factories import ShipmentFactory
from apps.shipments.models import Shipment, ShipmentStatus, TrackingEvent
from apps.shipments.tests.conftest import (
    SHIPMENTS_URL,
    create,
    inline_address,
    item,
    make_user,
)
from apps.tenancy.factories import BranchFactory, CompanyFactory, ServiceTypeFactory
from apps.tenancy.models import Branch, Company, ServiceType

pytestmark = pytest.mark.django_db


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["code"]


def test_valid_shipment_with_two_items(
    client: APIClient, valid: dict[str, Any], customer: Customer, pickup_address: Address
) -> None:
    response = create(client, valid)

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["status"] == "created"
    assert body["tracking_number"] == "FF-NY1-000001"
    assert len(body["tracking_token"]) == 22  # 16 random bytes, URL-safe base64
    assert body["priority"] == "normal"
    assert body["total_weight_kg"] == "6.500"
    assert body["service_type"] == "standard"
    assert body["customer"] == {"public_id": str(customer.public_id), "name": "Acme GmbH"}
    assert [i["barcode"] for i in body["items"]] == ["FF-NY1-000001-001", "FF-NY1-000001-002"]
    assert body["items"][0]["description"] == "Books"

    # Pickup copied from the address book; its empty contact falls back to the customer.
    assert body["pickup"]["source_address_id"] == str(pickup_address.public_id)
    assert body["pickup"]["contact_name"] == "Acme GmbH"
    assert body["pickup"]["line1"] == pickup_address.line1
    assert body["pickup"]["geocode_confidence"] == "none"
    # Delivery typed in, with client-supplied coordinates (memory D-API-18).
    assert body["delivery"]["source_address_id"] is None
    assert body["delivery"]["country"] == "DE"
    assert body["delivery"]["geocode_confidence"] == "manual"
    assert (body["delivery"]["latitude"], body["delivery"]["longitude"]) == (52.517, 13.389)

    shipment = Shipment.objects.get(public_id=body["public_id"])
    event = TrackingEvent.objects.get(shipment=shipment)
    assert (event.type, event.status_after, event.source) == ("status_change", "created", "admin")
    assert event.actor is not None
    entry = AuditLog.objects.get(action=AuditAction.SHIPMENT_CREATED)
    assert (entry.object_id, entry.after) == (body["public_id"], {"status": "created"})


def test_address_without_coordinates_is_accepted(client: APIClient, valid: dict[str, Any]) -> None:
    valid["delivery"] = inline_address(latitude=None, longitude=None)

    response = create(client, valid)

    assert response.status_code == 201
    assert response.json()["delivery"]["geocode_confidence"] == "none"


def test_draft_has_tracking_number_but_no_event(client: APIClient, valid: dict[str, Any]) -> None:
    response = create(client, {**valid, "draft": True})

    assert response.status_code == 201
    assert response.json()["status"] == "draft"
    assert response.json()["tracking_number"] == "FF-NY1-000001"
    assert not TrackingEvent.objects.exists()


def test_tracking_numbers_count_per_branch(
    company: Company, branch: Branch, other_branch: Branch, valid: dict[str, Any]
) -> None:
    client = client_for(make_user(company, RoleCode.OPS_MANAGER))

    numbers = [create(client, valid).json()["tracking_number"] for _ in range(2)]
    numbers.append(
        create(client, {**valid, "branch_id": str(other_branch.public_id)}).json()[
            "tracking_number"
        ]
    )

    assert numbers == ["FF-NY1-000001", "FF-NY1-000002", "FF-LA1-000001"]


def test_ignores_company_and_status_in_body(client: APIClient, valid: dict[str, Any]) -> None:
    other = CompanyFactory.create()

    response = create(client, {**valid, "company": str(other.public_id), "status": "delivered"})

    assert response.status_code == 201
    shipment = Shipment.objects.get(public_id=response.json()["public_id"])
    assert shipment.status == ShipmentStatus.CREATED
    assert shipment.company_id != other.pk


# --- Idempotency (architecture §20: required on shipment creation) --------------------


def test_missing_idempotency_key_is_rejected(client: APIClient, valid: dict[str, Any]) -> None:
    response = client.post(SHIPMENTS_URL, valid, format="json")

    assert error(response) == (400, "idempotency.key_required")
    assert not Shipment.objects.exists()


def test_retry_with_same_key_replays_the_original(client: APIClient, valid: dict[str, Any]) -> None:
    first = create(client, valid, key="create-1")
    replay = create(client, valid, key="create-1")

    assert (first.status_code, replay.status_code) == (201, 201)
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == first.json()
    assert Shipment.objects.count() == 1


def test_same_key_different_body_is_rejected(client: APIClient, valid: dict[str, Any]) -> None:
    create(client, valid, key="create-1")

    response = create(client, {**valid, "priority": "urgent"}, key="create-1")

    assert error(response) == (422, "idempotency.key_reuse")
    assert Shipment.objects.count() == 1


def test_failed_create_can_be_retried_with_the_same_key(
    client: APIClient, valid: dict[str, Any]
) -> None:
    failed = create(client, {**valid, "service_type": "nope"}, key="create-1")
    retried = create(client, {**valid, "service_type": "nope"}, key="create-1")

    assert error(failed) == error(retried) == (400, "shipment.unsupported_type")


# --- Validation ------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["pickup", "delivery", "customer_id", "branch_id", "items"])
def test_missing_required_field(client: APIClient, valid: dict[str, Any], missing: str) -> None:
    del valid[missing]

    response = create(client, valid)

    assert error(response) == (400, "validation.invalid")
    assert missing in response.json()["errors"]


@pytest.mark.parametrize(
    ("delivery", "field"),
    [
        (inline_address(phone="030 123456"), "phone"),
        (inline_address(country="XX"), "country"),
        (inline_address(contact_name=""), "contact_name"),
        (inline_address(line1=""), "line1"),
        (inline_address(longitude=None), "latitude"),
        (inline_address(latitude=91), "latitude"),
        (inline_address(label="Home"), None),  # no labels on shipments: field is ignored
    ],
)
def test_inline_address_validation(
    client: APIClient, valid: dict[str, Any], delivery: dict[str, Any], field: str | None
) -> None:
    response = create(client, {**valid, "delivery": delivery})

    if field is None:
        assert response.status_code == 201
        return
    assert error(response) == (400, "validation.invalid")
    assert field in response.json()["errors"]["delivery"]


@pytest.mark.parametrize(
    "delivery",
    [{"address_id": "not-a-uuid"}, "Berlin", {"address_id": str(uuid.uuid4()), "city": "X"}],
)
def test_malformed_address_reference(
    client: APIClient, valid: dict[str, Any], delivery: Any
) -> None:
    response = create(client, {**valid, "delivery": delivery})

    assert error(response) == (400, "validation.invalid")
    assert "delivery" in response.json()["errors"]


@pytest.mark.parametrize(
    ("items", "code"),
    [
        ([], "validation.invalid"),
        ([item(weight_kg="0")], "shipment.invalid_weight"),
        ([item(weight_kg="-1")], "shipment.invalid_weight"),
        ([item(length_cm="0")], "validation.invalid"),
        ([item(weight_kg="30.001")], "validation.invalid"),  # over the standard limit
        ([item(height_cm="80.1")], "validation.invalid"),
        ([item(weight_kg="abc")], "validation.invalid"),
    ],
)
def test_item_validation(
    client: APIClient, valid: dict[str, Any], items: list[dict[str, Any]], code: str
) -> None:
    response = create(client, {**valid, "items": items})

    assert error(response) == (400, code)
    assert "items" in response.json()["errors"]
    assert not Shipment.objects.exists()


def test_item_over_service_limit_names_the_field(client: APIClient, valid: dict[str, Any]) -> None:
    response = create(client, {**valid, "items": [item(), item(length_cm="121")]})

    assert response.json()["errors"]["items"] == {
        "1": {"length_cm": ["Exceeds the standard limit of 120.0."]}
    }


def test_total_weight_over_30000_kg(
    client: APIClient, valid: dict[str, Any], freight: ServiceType
) -> None:
    items = [item(weight_kg="1000")] * 30 + [item(weight_kg="0.001")]

    response = create(client, {**valid, "service_type": "freight", "items": items})

    assert error(response) == (400, "shipment.invalid_weight")


def test_total_weight_of_exactly_30000_kg_is_accepted(
    client: APIClient, valid: dict[str, Any], freight: ServiceType
) -> None:
    items = [item(weight_kg="1000")] * 30

    response = create(client, {**valid, "service_type": "freight", "items": items})

    assert response.status_code == 201
    assert response.json()["total_weight_kg"] == "30000.000"


@pytest.mark.parametrize(("count", "status_code"), [(500, 201), (501, 400)])
def test_item_count_boundary(
    client: APIClient, valid: dict[str, Any], count: int, status_code: int
) -> None:
    response = create(client, {**valid, "items": [item(weight_kg="0.1")] * count})

    assert response.status_code == status_code


def test_unknown_or_inactive_service_type(
    client: APIClient, valid: dict[str, Any], company: Company
) -> None:
    ServiceTypeFactory.create(company=company, code="retired", is_active=False)
    ServiceTypeFactory.create(code="theirs")  # another company's

    for code in ("nope", "retired", "theirs"):
        assert error(create(client, {**valid, "service_type": code})) == (
            400,
            "shipment.unsupported_type",
        )


def test_invalid_priority(client: APIClient, valid: dict[str, Any]) -> None:
    response = create(client, {**valid, "priority": "asap"})

    assert error(response) == (400, "validation.invalid")
    assert "priority" in response.json()["errors"]


def test_scheduled_date_in_the_past(client: APIClient, valid: dict[str, Any]) -> None:
    yesterday = timezone.localdate() - timedelta(days=1)

    response = create(client, {**valid, "scheduled_pickup_date": yesterday.isoformat()})

    assert error(response) == (400, "shipment.past_schedule")


def test_scheduled_date_invalid_format(client: APIClient, valid: dict[str, Any]) -> None:
    response = create(client, {**valid, "scheduled_pickup_date": "23/11/2026"})

    assert error(response) == (400, "validation.invalid")


def test_today_is_the_company_business_day(
    client: APIClient, valid: dict[str, Any], company: Company
) -> None:
    # UTC+14: its "today" is the UTC day or the next one, never earlier.
    company.timezone = "Pacific/Kiritimati"
    company.save()
    today = timezone.localdate(timezone=ZoneInfo("Pacific/Kiritimati"))

    accepted = create(client, {**valid, "scheduled_pickup_date": today.isoformat()})
    rejected = create(
        client, {**valid, "scheduled_pickup_date": (today - timedelta(days=1)).isoformat()}
    )

    assert accepted.status_code == 201
    assert error(rejected) == (400, "shipment.past_schedule")


# --- External reference ----------------------------------------------------------------


def test_duplicate_external_reference(client: APIClient, valid: dict[str, Any]) -> None:
    assert create(client, {**valid, "external_reference": "PO-1"}).status_code == 201

    response = create(client, {**valid, "external_reference": "PO-1"})

    assert error(response) == (409, "shipment.duplicate_reference")
    assert Shipment.objects.count() == 1


def test_same_reference_in_another_company_and_blank_references_are_allowed(
    client: APIClient, valid: dict[str, Any]
) -> None:
    ShipmentFactory.create(external_reference="PO-1")  # another company

    assert create(client, {**valid, "external_reference": "PO-1"}).status_code == 201
    assert create(client, valid).status_code == 201
    assert create(client, valid).status_code == 201


# --- References: tenant, archive and branch scope ---------------------------------------


def test_archived_customer(client: APIClient, valid: dict[str, Any], customer: Customer) -> None:
    customer.archived_at = timezone.now()
    customer.save()

    assert error(create(client, valid)) == (400, "customer.inactive")


def test_other_company_customer_is_an_invalid_reference(
    client: APIClient, valid: dict[str, Any]
) -> None:
    theirs = CustomerFactory.create(name="Their Secret Customer")

    response = create(client, {**valid, "customer_id": str(theirs.public_id)})

    assert error(response) == (400, "validation.invalid_reference")
    assert response.json()["errors"] == {"customer_id": ["Customer not found."]}
    assert "Their Secret Customer" not in response.content.decode()


@pytest.mark.parametrize("which", ["other_company", "archived", "unknown"])
def test_unusable_address_reference(
    client: APIClient, valid: dict[str, Any], customer: Customer, which: str
) -> None:
    if which == "other_company":
        address_id = AddressFactory.create(line1="Their Secret Street").public_id
    elif which == "archived":
        address_id = AddressFactory.create(customer=customer, archived_at=timezone.now()).public_id
    else:
        address_id = uuid.uuid4()

    response = create(client, {**valid, "delivery": {"address_id": str(address_id)}})

    assert error(response) == (400, "validation.invalid_reference")
    assert response.json()["errors"] == {"delivery": {"address_id": ["Address not found."]}}
    assert "Their Secret Street" not in response.content.decode()


def test_address_from_another_customer_of_the_company_is_allowed(
    client: APIClient, valid: dict[str, Any], company: Company
) -> None:
    recipient = AddressFactory.create(customer__company=company, contact_name="Rita")

    response = create(client, {**valid, "delivery": {"address_id": str(recipient.public_id)}})

    assert response.status_code == 201
    assert response.json()["delivery"]["contact_name"] == "Rita"


@pytest.mark.parametrize("which", ["not_member", "other_company", "unknown"])
def test_branch_outside_the_users_scope(
    client: APIClient, valid: dict[str, Any], other_branch: Branch, which: str
) -> None:
    branch_id = {
        "not_member": other_branch.public_id,
        "other_company": BranchFactory.create().public_id,
        "unknown": uuid.uuid4(),
    }[which]

    response = create(client, {**valid, "branch_id": str(branch_id)})

    assert error(response) == (400, "validation.invalid_reference")
    assert response.json()["errors"] == {"branch_id": ["Branch not found."]}


def test_all_branches_permission_creates_in_any_branch(
    company: Company, valid: dict[str, Any], other_branch: Branch
) -> None:
    client = client_for(make_user(company, RoleCode.OPS_MANAGER))  # member of no branch

    response = create(client, {**valid, "branch_id": str(other_branch.public_id)})

    assert response.status_code == 201
    assert response.json()["branch"]["code"] == "LA1"


def test_snapshot_does_not_follow_address_book_edits(
    client: APIClient, valid: dict[str, Any], pickup_address: Address
) -> None:
    public_id = create(client, valid).json()["public_id"]
    original = pickup_address.line1

    pickup_address.line1 = "Moved Street 9"
    pickup_address.save()

    detail = client.get(f"{SHIPMENTS_URL}{public_id}/").json()
    assert detail["pickup"]["line1"] == original


def test_unauthenticated(valid: dict[str, Any]) -> None:
    response = create(APIClient(), valid)

    assert error(response) == (401, "auth.not_authenticated")
