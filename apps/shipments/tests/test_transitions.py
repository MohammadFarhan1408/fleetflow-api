"""Shipment state transitions (architecture §11, §26.4 "Shipment state transitions")."""

import threading
from typing import Any

import pytest
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RoleCode, User
from apps.accounts.tests.conftest import client_for
from apps.audit.models import AuditAction, AuditLog
from apps.core.errors import DomainError, ErrorCode
from apps.customers.models import Customer
from apps.shipments import services
from apps.shipments.factories import ShipmentFactory
from apps.shipments.models import (
    ALLOWED_TRANSITIONS,
    Shipment,
    ShipmentStatus,
    TrackingEvent,
    TrackingEventType,
)
from apps.shipments.permissions import SHIPMENTS_CANCEL
from apps.shipments.tests.conftest import create, detail_url, make_user
from apps.tenancy.models import Branch, Company

pytestmark = pytest.mark.django_db

S = ShipmentStatus
ALLOWED = [(a, b) for a, targets in ALLOWED_TRANSITIONS.items() for b in sorted(targets)]
DISALLOWED = [(a, b) for a in S for b in S if b not in ALLOWED_TRANSITIONS[a]]


def transition_url(shipment: Any) -> str:
    return f"{detail_url(shipment)}transitions/"


def correction_url(shipment: Any) -> str:
    return f"{detail_url(shipment)}corrections/"


def events_url(shipment: Any) -> str:
    return f"{detail_url(shipment)}events/"


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["code"]


@pytest.fixture
def owner(company: Company) -> User:
    return make_user(company, RoleCode.OWNER)


def move(actor: User, shipment: Shipment, to: S, reason: str = "") -> Shipment:
    """Take an edge as a later-phase service would: unmapped edges are pre-authorized."""
    return services.transition(
        actor=actor,
        shipment=shipment,
        to=to,
        expected_status=S(shipment.status),
        reason=reason,
        request=RequestFactory().post("/"),
        authorized_by_caller=(S(shipment.status), to) not in services.EDGE_PERMISSIONS,
    )


# --- The table (service level, every edge) ----------------------------------------------


def test_table_covers_every_status() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(S)
    assert not ALLOWED_TRANSITIONS[S.DELIVERED]
    assert not ALLOWED_TRANSITIONS[S.RETURNED_TO_SENDER]


@pytest.mark.parametrize(("origin", "target"), ALLOWED, ids=[f"{a}->{b}" for a, b in ALLOWED])
def test_every_allowed_edge(owner: User, branch: Branch, origin: S, target: S) -> None:
    shipment = ShipmentFactory.create(branch=branch, status=origin)

    moved = move(owner, shipment, target, reason="test")

    assert moved.status == target
    shipment.refresh_from_db()
    assert shipment.status == target
    event = TrackingEvent.objects.get(shipment=shipment)
    assert (event.type, event.status_after, event.actor) == ("status_change", target, owner)


@pytest.mark.parametrize(("origin", "target"), DISALLOWED, ids=[f"{a}->{b}" for a, b in DISALLOWED])
def test_every_disallowed_edge(owner: User, branch: Branch, origin: S, target: S) -> None:
    shipment = ShipmentFactory.create(branch=branch, status=origin)

    with pytest.raises(DomainError) as exc:
        move(owner, shipment, target, reason="test")

    assert exc.value.error_code == ErrorCode.SHIPMENT_INVALID_TRANSITION
    shipment.refresh_from_db()
    assert shipment.status == origin
    assert not TrackingEvent.objects.filter(shipment=shipment).exists()


def test_unmapped_edges_are_denied_unless_the_caller_authorized_them(
    owner: User, branch: Branch
) -> None:
    shipment = ShipmentFactory.create(branch=branch)

    with pytest.raises(DomainError) as exc:
        services.transition(
            actor=owner,
            shipment=shipment,
            to=S.ASSIGNED,
            expected_status=S.CREATED,
            request=RequestFactory().post("/"),
        )

    assert exc.value.error_code == ErrorCode.PERMISSION_DENIED
    assert not TrackingEvent.objects.exists()


def test_the_route_return_edges_are_service_only(owner: User, branch: Branch) -> None:
    for target in (S.CREATED, S.SORTED, S.CANCELLED):
        shipment = ShipmentFactory.create(branch=branch, status=S.ASSIGNED)

        with pytest.raises(DomainError) as exc:  # not authorized_by_caller: what an API call is
            services.transition(
                actor=owner,
                shipment=shipment,
                to=target,
                expected_status=S.ASSIGNED,
                reason="x",
                request=RequestFactory().post("/"),
            )

        assert exc.value.error_code == ErrorCode.SHIPMENT_INVALID_TRANSITION
        shipment.refresh_from_db()
        assert shipment.status == S.ASSIGNED
        assert not TrackingEvent.objects.filter(shipment=shipment).exists()


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("created", (409, "shipment.invalid_transition")),
        ("cancelled", (409, "shipment.invalid_transition")),
        ("sorted", (400, "validation.invalid")),  # not even an API target
    ],
)
def test_an_assigned_shipment_cannot_leave_its_route_through_the_api(
    client: APIClient, branch: Branch, target: str, expected: tuple[int, str]
) -> None:
    shipment = ShipmentFactory.create(branch=branch, status=S.ASSIGNED)

    response = client.post(
        transition_url(shipment.public_id),
        {"to": target, "expected_status": "assigned", "reason": "x"},
        format="json",
    )

    assert error(response) == expected
    shipment.refresh_from_db()
    assert shipment.status == S.ASSIGNED


# --- API: confirm, cancel, recover ------------------------------------------------------


def test_confirm_draft(client: APIClient, valid: dict[str, Any]) -> None:
    draft = create(client, {**valid, "draft": True}).json()

    response = client.post(
        transition_url(draft["public_id"]),
        {"to": "created", "expected_status": "draft"},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert response.json()["status"] == "created"
    assert response.json()["tracking_number"] == draft["tracking_number"]
    event = TrackingEvent.objects.get(shipment__public_id=draft["public_id"])
    assert (event.type, event.status_after) == ("status_change", "created")


def test_confirm_revalidates_a_stale_draft(
    client: APIClient, valid: dict[str, Any], customer: Customer
) -> None:
    draft = create(client, {**valid, "draft": True}).json()
    customer.archived_at = timezone.now()
    customer.save()

    response = client.post(
        transition_url(draft["public_id"]),
        {"to": "created", "expected_status": "draft"},
        format="json",
    )

    assert error(response) == (400, "customer.inactive")
    assert Shipment.objects.get(public_id=draft["public_id"]).status == S.DRAFT
    assert not TrackingEvent.objects.exists()


def test_cancel_requires_a_reason(client: APIClient, branch: Branch) -> None:
    shipment = ShipmentFactory.create(branch=branch)
    url = transition_url(shipment.public_id)

    missing = client.post(url, {"to": "cancelled", "expected_status": "created"}, format="json")
    blank = client.post(
        url, {"to": "cancelled", "expected_status": "created", "reason": "  "}, format="json"
    )
    done = client.post(
        url,
        {"to": "cancelled", "expected_status": "created", "reason": "Customer called"},
        format="json",
    )

    assert error(missing) == error(blank) == (400, "validation.invalid")
    assert done.status_code == 200
    assert done.json()["status"] == "cancelled"
    assert TrackingEvent.objects.get(shipment=shipment).reason == "Customer called"


def test_recovery_needs_permission_and_is_audited(
    client: APIClient, company: Company, branch: Branch
) -> None:
    shipment = ShipmentFactory.create(branch=branch, status=S.CANCELLED)
    body = {"to": "created", "expected_status": "cancelled", "reason": "Cancelled by mistake"}

    denied = client.post(transition_url(shipment.public_id), body, format="json")  # dispatcher
    manager = client_for(make_user(company, RoleCode.OPS_MANAGER))
    allowed = manager.post(transition_url(shipment.public_id), body, format="json")

    assert error(denied) == (403, "permission.denied")
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "created"
    entry = AuditLog.objects.get(action=AuditAction.SHIPMENT_RECOVERED)
    assert (entry.object_id, entry.after) == (str(shipment.public_id), {"status": "created"})


def test_recovery_requires_a_reason(company: Company, branch: Branch) -> None:
    shipment = ShipmentFactory.create(branch=branch, status=S.CANCELLED)
    manager = client_for(make_user(company, RoleCode.OPS_MANAGER))

    response = manager.post(
        transition_url(shipment.public_id),
        {"to": "created", "expected_status": "cancelled"},
        format="json",
    )

    assert error(response) == (400, "validation.invalid")


@pytest.mark.parametrize("target", ["assigned", "delivered", "draft", "bogus"])
def test_only_phase_2_edges_are_exposed(client: APIClient, branch: Branch, target: str) -> None:
    shipment = ShipmentFactory.create(branch=branch)

    response = client.post(
        transition_url(shipment.public_id),
        {"to": target, "expected_status": "created", "reason": "x"},
        format="json",
    )

    assert error(response) == (400, "validation.invalid")
    assert "to" in response.json()["errors"]


def test_stale_expected_status_is_a_conflict(client: APIClient, branch: Branch) -> None:
    shipment = ShipmentFactory.create(branch=branch, status=S.SORTED)

    response = client.post(
        transition_url(shipment.public_id),
        {"to": "cancelled", "expected_status": "created", "reason": "x"},
        format="json",
    )

    assert error(response) == (409, "shipment.status_conflict")
    assert not TrackingEvent.objects.exists()


def test_repeat_with_same_key_replays_and_without_key_conflicts(
    client: APIClient, branch: Branch
) -> None:
    shipment = ShipmentFactory.create(branch=branch)
    body = {"to": "cancelled", "expected_status": "created", "reason": "x"}
    url = transition_url(shipment.public_id)

    first = client.post(url, body, format="json", headers={"Idempotency-Key": "cancel-1"})
    replay = client.post(url, body, format="json", headers={"Idempotency-Key": "cancel-1"})
    again = client.post(url, body, format="json")

    assert (first.status_code, replay.status_code) == (200, 200)
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert error(again) == (409, "shipment.status_conflict")
    assert TrackingEvent.objects.filter(shipment=shipment).count() == 1


def test_delivered_and_cancelled_cannot_be_reopened_through_the_api(
    company: Company, branch: Branch
) -> None:
    client = client_for(make_user(company, RoleCode.OWNER))
    delivered = ShipmentFactory.create(branch=branch, status=S.DELIVERED)

    response = client.post(
        transition_url(delivered.public_id),
        {"to": "cancelled", "expected_status": "delivered", "reason": "x"},
        format="json",
    )

    assert error(response) == (409, "shipment.invalid_transition")


CAN_CANCEL = {
    RoleCode.OWNER,
    RoleCode.ADMIN,
    RoleCode.OPS_MANAGER,
    RoleCode.DISPATCHER,
    RoleCode.CUSTOMER_SERVICE,
}


@pytest.mark.parametrize("role", list(RoleCode))
def test_cancel_role_matrix(role: RoleCode, company: Company, branch: Branch) -> None:
    shipment = ShipmentFactory.create(branch=branch)
    client = client_for(make_user(company, role, branch))

    response = client.post(
        transition_url(shipment.public_id),
        {"to": "cancelled", "expected_status": "created", "reason": "x"},
        format="json",
    )

    assert response.status_code == (200 if role in CAN_CANCEL else 403)


def test_transition_scope(client: APIClient, other_branch: Branch) -> None:
    theirs = ShipmentFactory.create(branch=other_branch)
    body = {"to": "cancelled", "expected_status": "created", "reason": "x"}

    assert error(client.post(transition_url(theirs.public_id), body, format="json")) == (
        404,
        "not_found",
    )
    assert (
        APIClient().post(transition_url(theirs.public_id), body, format="json").status_code == 401
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_transitions_have_exactly_one_winner() -> None:
    shipment = ShipmentFactory.create()
    # Transactional tests truncate the migration-seeded roles, so grant the one
    # permission this test needs explicitly.
    role, _ = Role.objects.get_or_create(code=RoleCode.DISPATCHER)
    role.permissions.add(Permission.objects.get_or_create(codename=SHIPMENTS_CANCEL)[0])
    actor = make_user(shipment.company, RoleCode.DISPATCHER, shipment.branch)
    start = threading.Barrier(2)
    outcomes: list[str] = []

    def cancel() -> None:
        try:
            start.wait()
            move(actor, Shipment.objects.get(pk=shipment.pk), S.CANCELLED, reason="race")
            outcomes.append("ok")
        except DomainError as exc:
            outcomes.append(exc.error_code)
        finally:
            connection.close()

    threads = [threading.Thread(target=cancel) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == sorted(["ok", ErrorCode.SHIPMENT_STATUS_CONFLICT])
    assert TrackingEvent.objects.filter(shipment=shipment).count() == 1


# --- Admin correction --------------------------------------------------------------------


def test_correction_appends_and_audits_without_rewriting_history(
    owner: User, branch: Branch
) -> None:
    shipment = ShipmentFactory.create(branch=branch)
    for target in (S.ASSIGNED, S.OUT_FOR_DELIVERY, S.DELIVERED):
        shipment = move(owner, shipment, target)
    history = list(
        TrackingEvent.objects.filter(shipment=shipment).values_list("pk", "status_after")
    )

    response = client_for(owner).post(
        correction_url(shipment.public_id),
        {"to": "delivery_failed", "expected_status": "delivered", "reason": "POD was wrong"},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert response.json()["status"] == "delivery_failed"
    events = TrackingEvent.objects.filter(shipment=shipment).order_by("pk")
    assert list(events.values_list("pk", "status_after"))[:3] == history
    correction = events.last()
    assert correction is not None
    assert (correction.type, correction.public, correction.reason) == (
        TrackingEventType.CORRECTION,
        False,
        "POD was wrong",
    )
    entry = AuditLog.objects.get(action=AuditAction.SHIPMENT_STATUS_CORRECTED)
    assert (entry.before, entry.after) == ({"status": "delivered"}, {"status": "delivery_failed"})


def test_correction_needs_permission_and_reason(
    client: APIClient, owner: User, branch: Branch
) -> None:
    shipment = ShipmentFactory.create(branch=branch, status=S.DELIVERED)
    body = {"to": "delivery_failed", "expected_status": "delivered"}

    denied = client.post(correction_url(shipment.public_id), {**body, "reason": "x"}, format="json")
    no_reason = client_for(owner).post(correction_url(shipment.public_id), body, format="json")

    assert error(denied) == (403, "permission.denied")  # dispatcher
    assert error(no_reason) == (400, "validation.invalid")
    assert not TrackingEvent.objects.exists()


@pytest.mark.parametrize(
    ("origin", "target", "expected"),
    [
        (S.DELIVERED, S.DELIVERED, (409, "shipment.invalid_transition")),
        (S.DRAFT, S.CREATED, (409, "shipment.invalid_transition")),
        (S.CREATED, S.DRAFT, (400, "validation.invalid")),
        (S.CREATED, S.DELIVERED, (200, None)),
    ],
)
def test_correction_targets(
    owner: User, branch: Branch, origin: S, target: S, expected: tuple[int, str | None]
) -> None:
    shipment = ShipmentFactory.create(branch=branch, status=origin)

    response = client_for(owner).post(
        correction_url(shipment.public_id),
        {"to": target, "expected_status": origin, "reason": "x"},
        format="json",
    )

    code = response.json()["code"] if response.status_code != 200 else None
    assert (response.status_code, code) == expected


def test_service_rejects_correction_without_permission(client: APIClient, dispatcher: User) -> None:
    shipment = ShipmentFactory.create(branch=dispatcher.branches.get(), status=S.DELIVERED)

    with pytest.raises(DomainError) as exc:
        services.correct_status(
            actor=dispatcher,
            shipment=shipment,
            to=S.DELIVERY_FAILED,
            expected_status=S.DELIVERED,
            reason="x",
            request=RequestFactory().post("/"),
        )

    assert exc.value.error_code == ErrorCode.PERMISSION_DENIED


# --- Timeline ----------------------------------------------------------------------------


def test_timeline_is_chronological_and_includes_internal_events(
    owner: User, branch: Branch
) -> None:
    shipment = ShipmentFactory.create(branch=branch)
    shipment = move(owner, shipment, S.CANCELLED, reason="oops")
    services.correct_status(
        actor=owner,
        shipment=shipment,
        to=S.CREATED,
        expected_status=S.CANCELLED,
        reason="fix",
        request=RequestFactory().post("/"),
    )

    body = client_for(owner).get(events_url(shipment.public_id)).json()

    assert [(e["type"], e["status_after"], e["public"]) for e in body["results"]] == [
        ("status_change", "cancelled", True),
        ("correction", "created", False),
    ]
    assert body["results"][0]["actor"] == {
        "public_id": str(owner.public_id),
        "full_name": owner.full_name,
    }


def test_timeline_query_count_is_constant(owner: User, branch: Branch) -> None:
    shipment = ShipmentFactory.create(branch=branch)
    client = client_for(owner)

    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            assert client.get(events_url(shipment.public_id)).status_code == 200
        return len(queries)

    shipment = move(owner, shipment, S.CANCELLED, reason="x")
    one = count()
    for target in (S.CREATED, S.CANCELLED, S.CREATED, S.CANCELLED):
        shipment = move(owner, shipment, target, reason="x")

    assert count() == one


def test_timeline_scope(client: APIClient, other_branch: Branch) -> None:
    theirs = ShipmentFactory.create(branch=other_branch)

    assert error(client.get(events_url(theirs.public_id))) == (404, "not_found")
