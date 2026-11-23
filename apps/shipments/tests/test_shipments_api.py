"""Shipment list, detail, draft edit/delete, token rotation, roles and branch scope."""

from datetime import timedelta
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import RoleCode, User
from apps.accounts.tests.conftest import client_for
from apps.audit.models import AuditAction, AuditLog
from apps.customers.models import Customer
from apps.shipments.factories import ShipmentFactory
from apps.shipments.models import Shipment, ShipmentAddress, ShipmentItem, ShipmentStatus
from apps.shipments.tests.conftest import (
    SHIPMENTS_URL,
    create,
    detail_url,
    inline_address,
    item,
    make_user,
    token_url,
)
from apps.tenancy.models import Branch, Company, ServiceType

pytestmark = pytest.mark.django_db


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["code"]


def draft_in(client: APIClient, valid: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    response = create(client, {**valid, "draft": True, **overrides})
    assert response.status_code == 201, response.content
    body: dict[str, Any] = response.json()
    return body


# --- Role x action matrix (memory D-API-21) -------------------------------------------

CAN_VIEW = set(RoleCode) - {RoleCode.DRIVER}
CAN_CREATE = {
    RoleCode.OWNER,
    RoleCode.ADMIN,
    RoleCode.OPS_MANAGER,
    RoleCode.DISPATCHER,
    RoleCode.CUSTOMER_SERVICE,
}


@pytest.mark.parametrize("role", list(RoleCode))
def test_role_matrix(
    role: RoleCode,
    company: Company,
    branch: Branch,
    dispatcher: User,
    valid: dict[str, Any],
) -> None:
    drafts = [draft_in(client_for(dispatcher), valid)["public_id"] for _ in range(2)]
    client = client_for(make_user(company, role, branch))

    reads = [client.get(SHIPMENTS_URL), client.get(detail_url(drafts[0]))]
    writes = [
        create(client, valid),
        client.patch(detail_url(drafts[0]), {"priority": "high"}, format="json"),
        client.post(token_url(drafts[0])),
        client.delete(detail_url(drafts[1])),
    ]

    assert {r.status_code for r in reads} == ({200} if role in CAN_VIEW else {403})
    expected = [201, 200, 200, 204] if role in CAN_CREATE else [403, 403, 403, 403]
    assert [r.status_code for r in writes] == expected
    for denied in (r for r in reads + writes if r.status_code == 403):
        assert denied.json()["code"] == "permission.denied"


def test_unauthenticated_reads() -> None:
    anonymous = APIClient()

    assert anonymous.get(SHIPMENTS_URL).status_code == 401
    assert anonymous.get(detail_url("0190a000-0000-7000-8000-000000000000")).status_code == 401


# --- Branch scope (architecture §8) ---------------------------------------------------


def test_branch_scope(
    company: Company, branch: Branch, other_branch: Branch, client: APIClient
) -> None:
    mine = ShipmentFactory.create(branch=branch)
    theirs = ShipmentFactory.create(branch=other_branch)

    listed = [s["public_id"] for s in client.get(SHIPMENTS_URL).json()["results"]]
    assert listed == [str(mine.public_id)]

    for response in (
        client.get(detail_url(theirs.public_id)),
        client.patch(detail_url(theirs.public_id), {"priority": "high"}, format="json"),
        client.delete(detail_url(theirs.public_id)),
        client.post(token_url(theirs.public_id)),
    ):
        assert error(response) == (404, "not_found")

    everyone = client_for(make_user(company, RoleCode.OPS_MANAGER))
    assert len(everyone.get(SHIPMENTS_URL).json()["results"]) == 2


def test_user_without_branches_sees_nothing(company: Company, branch: Branch) -> None:
    ShipmentFactory.create(branch=branch)

    client = client_for(make_user(company, RoleCode.DISPATCHER))

    assert client.get(SHIPMENTS_URL).json()["results"] == []


# --- List --------------------------------------------------------------------------------


def test_list_shape(client: APIClient, branch: Branch) -> None:
    shipment = ShipmentFactory.create(branch=branch, external_reference="PO-9")

    (row,) = client.get(SHIPMENTS_URL).json()["results"]

    assert row == {
        "public_id": str(shipment.public_id),
        "tracking_number": shipment.tracking_number,
        "status": "created",
        "priority": "normal",
        "branch": {"public_id": str(branch.public_id), "code": "NY1"},
        "customer": {"public_id": str(shipment.customer.public_id), "name": shipment.customer.name},
        "service_type": shipment.service_type.code,
        "scheduled_pickup_date": None,
        "external_reference": "PO-9",
        "total_weight_kg": "2.500",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    assert "tracking_token" not in row


def test_list_filters(client: APIClient, branch: Branch, company: Company) -> None:
    old = ShipmentFactory.create(
        branch=branch, tracking_number="FF-NY1-000001", status=ShipmentStatus.DRAFT
    )
    urgent = ShipmentFactory.create(
        branch=branch,
        tracking_number="FF-NY1-000002",
        priority="urgent",
        external_reference="PO-77",
    )
    Shipment.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=3))

    def ids(**params: Any) -> set[str]:
        response = client.get(SHIPMENTS_URL, params)
        assert response.status_code == 200, response.content
        return {row["public_id"] for row in response.json()["results"]}

    both = {str(old.public_id), str(urgent.public_id)}
    assert ids(status=["draft", "created"]) == both
    assert ids(status="draft") == {str(old.public_id)}
    assert ids(priority="urgent") == {str(urgent.public_id)}
    assert ids(branch=str(branch.public_id)) == both
    assert ids(customer=str(urgent.customer.public_id)) == {str(urgent.public_id)}
    assert ids(service_type=old.service_type.code) == {str(old.public_id)}
    assert ids(created_after=(timezone.now() - timedelta(days=1)).isoformat()) == {
        str(urgent.public_id)
    }
    assert ids(created_before=(timezone.now() - timedelta(days=1)).isoformat()) == {
        str(old.public_id)
    }
    assert ids(search="ff-ny1-000001") == {str(old.public_id)}
    assert ids(search="po-7") == {str(urgent.public_id)}


@pytest.mark.parametrize(
    "params", [{"status": "lost"}, {"priority": "asap"}, {"branch": "x"}, {"created_after": "x"}]
)
def test_list_rejects_bad_filters(client: APIClient, params: dict[str, str]) -> None:
    assert error(client.get(SHIPMENTS_URL, params)) == (400, "validation.invalid")


def test_list_paginates(client: APIClient, branch: Branch) -> None:
    ShipmentFactory.create_batch(3, branch=branch)

    first = client.get(SHIPMENTS_URL, {"page_size": 2}).json()
    second = client.get(first["next"]).json()

    assert len(first["results"]) == 2
    assert len(second["results"]) == 1


def _count_queries(client: APIClient, url: str) -> int:
    with CaptureQueriesContext(connection) as queries:
        assert client.get(url).status_code == 200
    return len(queries)


def test_list_query_count_is_constant(client: APIClient, branch: Branch) -> None:
    ShipmentFactory.create(branch=branch)
    one = _count_queries(client, SHIPMENTS_URL)
    ShipmentFactory.create_batch(9, branch=branch)

    assert (
        _count_queries(client, SHIPMENTS_URL) == one == 3
    )  # auth, permissions, one joined page query


def test_detail_query_count_is_constant(client: APIClient, valid: dict[str, Any]) -> None:
    small = create(client, valid).json()["public_id"]
    large = create(client, {**valid, "items": [item()] * 20}).json()["public_id"]

    assert _count_queries(client, detail_url(small)) == _count_queries(client, detail_url(large))
    assert _count_queries(client, detail_url(small)) == 5  # auth, perms, row, items, addresses


# --- Detail ------------------------------------------------------------------------------


def test_detail(client: APIClient, valid: dict[str, Any]) -> None:
    created = create(client, valid).json()

    response = client.get(detail_url(created["public_id"]))

    assert response.status_code == 200
    assert response.json() == created


# --- Draft edit -------------------------------------------------------------------------


def test_patch_changes_only_sent_fields(client: APIClient, valid: dict[str, Any]) -> None:
    draft = draft_in(client, valid)

    response = client.patch(detail_url(draft["public_id"]), {"priority": "high"}, format="json")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["priority"] == "high"
    for unchanged in ("pickup", "delivery", "items", "tracking_number", "tracking_token"):
        assert body[unchanged] == draft[unchanged]


def test_patch_replaces_items_and_addresses(client: APIClient, valid: dict[str, Any]) -> None:
    draft = draft_in(client, valid)

    response = client.patch(
        detail_url(draft["public_id"]),
        {"items": [item(weight_kg="1")] * 3, "delivery": inline_address(city="Hamburg")},
        format="json",
    )

    body = response.json()
    assert body["total_weight_kg"] == "3.000"
    assert [i["barcode"] for i in body["items"]] == [
        f"{draft['tracking_number']}-00{n}" for n in (1, 2, 3)
    ]
    assert body["delivery"]["city"] == "Hamburg"
    assert body["pickup"] == draft["pickup"]
    assert ShipmentItem.objects.count() == 3
    assert ShipmentAddress.objects.count() == 2


def test_patch_to_another_branch_issues_a_new_number(
    company: Company, valid: dict[str, Any], other_branch: Branch
) -> None:
    client = client_for(make_user(company, RoleCode.OPS_MANAGER))
    draft = draft_in(client, valid)

    body = client.patch(
        detail_url(draft["public_id"]), {"branch_id": str(other_branch.public_id)}, format="json"
    ).json()

    assert body["tracking_number"] == "FF-LA1-000001"
    assert [i["barcode"] for i in body["items"]] == ["FF-LA1-000001-001", "FF-LA1-000001-002"]


def test_patch_to_a_branch_outside_scope(
    client: APIClient, valid: dict[str, Any], other_branch: Branch
) -> None:
    draft = draft_in(client, valid)

    response = client.patch(
        detail_url(draft["public_id"]), {"branch_id": str(other_branch.public_id)}, format="json"
    )

    assert error(response) == (400, "validation.invalid_reference")


def test_invalid_patch_leaves_the_draft_unchanged(client: APIClient, valid: dict[str, Any]) -> None:
    draft = draft_in(client, valid)

    response = client.patch(
        detail_url(draft["public_id"]),
        {"priority": "urgent", "items": [item(weight_kg="0")]},
        format="json",
    )

    assert error(response) == (400, "shipment.invalid_weight")
    assert client.get(detail_url(draft["public_id"])).json() == draft


def test_patch_revalidates_the_whole_draft(
    client: APIClient, valid: dict[str, Any], standard: ServiceType
) -> None:
    draft = draft_in(client, valid)
    standard.is_active = False
    standard.save()

    response = client.patch(detail_url(draft["public_id"]), {"priority": "high"}, format="json")

    assert error(response) == (400, "shipment.unsupported_type")


def test_only_drafts_can_be_edited_or_deleted(client: APIClient, valid: dict[str, Any]) -> None:
    created = create(client, valid).json()

    patch = client.patch(detail_url(created["public_id"]), {"priority": "high"}, format="json")
    delete = client.delete(detail_url(created["public_id"]))

    assert error(patch) == error(delete) == (409, "shipment.not_draft")
    assert Shipment.objects.filter(public_id=created["public_id"]).exists()


def test_archived_customer_blocks_draft_edits(
    client: APIClient, valid: dict[str, Any], customer: Customer
) -> None:
    draft = draft_in(client, valid)
    customer.archived_at = timezone.now()
    customer.save()

    response = client.patch(detail_url(draft["public_id"]), {"priority": "high"}, format="json")

    assert error(response) == (400, "customer.inactive")


# --- Draft delete -----------------------------------------------------------------------


def test_delete_draft(client: APIClient, valid: dict[str, Any]) -> None:
    draft = draft_in(client, valid)

    response = client.delete(detail_url(draft["public_id"]))

    assert response.status_code == 204
    assert not Shipment.objects.exists()
    assert not ShipmentItem.objects.exists()
    assert not ShipmentAddress.objects.exists()
    entry = AuditLog.objects.get(action=AuditAction.SHIPMENT_DRAFT_DELETED)
    assert entry.object_id == draft["public_id"]
    assert error(client.get(detail_url(draft["public_id"]))) == (404, "not_found")


# --- Tracking token ---------------------------------------------------------------------


def test_rotate_tracking_token(client: APIClient, valid: dict[str, Any]) -> None:
    created = create(client, valid).json()

    response = client.post(token_url(created["public_id"]))

    assert response.status_code == 200
    new_token = response.json()["tracking_token"]
    assert len(new_token) == 22
    assert new_token != created["tracking_token"]
    assert client.get(detail_url(created["public_id"])).json()["tracking_token"] == new_token
    assert not Shipment.objects.filter(tracking_token=created["tracking_token"]).exists()
    entry = AuditLog.objects.get(action=AuditAction.SHIPMENT_TRACKING_TOKEN_ROTATED)
    assert new_token not in str(entry.after)
