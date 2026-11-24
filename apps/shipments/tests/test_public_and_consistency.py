"""Public tracking (architecture §7, §26.4 "Tracking & GPS" token rows), the nightly
consistency check (§12) and a property test of the status/event invariant."""

import logging
from datetime import date, timedelta
from typing import Any

import pytest
from django.db import connection, transaction
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rest_framework.test import APIClient

from apps.accounts.models import RoleCode
from apps.audit.models import AuditAction, AuditLog
from apps.core.errors import DomainError
from apps.shipments import services
from apps.shipments.factories import ShipmentFactory
from apps.shipments.models import (
    STATUS_EVENT_TYPES,
    EventSource,
    Shipment,
    ShipmentStatus,
    TrackingEvent,
    TrackingEventType,
)
from apps.shipments.tasks import check_status_consistency
from apps.shipments.tests.conftest import make_user
from apps.tenancy.models import CompanyStatus

pytestmark = pytest.mark.django_db

S = ShipmentStatus


def track_url(token: str) -> str:
    return f"/api/v1/track/{token}/"


def add_event(shipment: Shipment, status: S, *, public: bool = True, **extra: Any) -> None:
    TrackingEvent.objects.create(
        company=shipment.company,
        shipment=shipment,
        type=extra.pop("type", TrackingEventType.STATUS_CHANGE),
        status_after=status,
        occurred_at=extra.pop("occurred_at", timezone.now()),
        source=EventSource.ADMIN,
        public=public,
        **extra,
    )


# --- Public tracking --------------------------------------------------------------------


def test_public_tracking_shows_only_approved_fields() -> None:
    shipment = ShipmentFactory.create(scheduled_pickup_date=date(2030, 1, 2))
    add_event(shipment, S.CREATED)
    add_event(shipment, S.CANCELLED, public=False, type=TrackingEventType.CORRECTION)
    delivery = shipment.addresses.get(role="delivery")

    response = APIClient().get(track_url(shipment.tracking_token))

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "tracking_number": shipment.tracking_number,
        "status": "created",
        "carrier": shipment.company.name,
        "service": shipment.service_type.name,
        "delivery": {"city": delivery.city, "country": "US"},
        "scheduled_pickup_date": "2030-01-02",
        "events": [{"status": "created", "occurred_at": body["events"][0]["occurred_at"]}],
    }
    text = response.content.decode()
    for private in (delivery.line1, delivery.contact_name, shipment.customer.name):
        assert private not in text


@pytest.mark.parametrize("case", ["unknown", "rotated", "draft", "suspended"])
def test_public_tracking_404s(case: str) -> None:
    shipment = ShipmentFactory.create(status=S.DRAFT if case == "draft" else S.CREATED)
    token = shipment.tracking_token
    if case == "unknown":
        token = "x" * 22
    elif case == "rotated":
        services.rotate_tracking_token(
            actor=make_user(shipment.company, RoleCode.OWNER),
            shipment=shipment,
            request=RequestFactory().post("/"),
        )
    elif case == "suspended":
        shipment.company.status = CompanyStatus.SUSPENDED
        shipment.company.save()

    response = APIClient().get(track_url(token))

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_one_token_opens_exactly_one_shipment() -> None:
    mine, other = ShipmentFactory.create(), ShipmentFactory.create()

    body = APIClient().get(track_url(mine.tracking_token)).json()

    assert body["tracking_number"] == mine.tracking_number != other.tracking_number


def test_public_tracking_ignores_credentials() -> None:
    shipment = ShipmentFactory.create()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")

    assert client.get(track_url(shipment.tracking_token)).status_code == 200


def test_public_tracking_is_throttled_per_ip() -> None:
    shipment = ShipmentFactory.create()
    client = APIClient()

    statuses = [client.get(track_url(shipment.tracking_token)).status_code for _ in range(61)]

    assert statuses[:60] == [200] * 60
    assert statuses[60] == 429


def test_public_tracking_query_count_is_constant() -> None:
    shipment = ShipmentFactory.create()
    add_event(shipment, S.CREATED)

    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            assert APIClient().get(track_url(shipment.tracking_token)).status_code == 200
        return len(queries)

    one = count()
    for _ in range(5):
        add_event(shipment, S.CREATED)

    assert count() == one == 3  # shipment with company and service type, delivery, events


# --- Nightly consistency check ----------------------------------------------------------


def test_consistency_check_reports_mismatches_without_fixing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    consistent = ShipmentFactory.create()
    add_event(consistent, S.CREATED)
    drifted = ShipmentFactory.create(status=S.ASSIGNED)
    add_event(drifted, S.CREATED)
    no_events = ShipmentFactory.create()
    ShipmentFactory.create(status=S.DRAFT)  # drafts have no events by design
    corrected = ShipmentFactory.create(status=S.DELIVERY_FAILED)
    add_event(corrected, S.DELIVERED)
    add_event(corrected, S.DELIVERY_FAILED, type=TrackingEventType.CORRECTION, public=False)

    with caplog.at_level(logging.WARNING, logger="apps.shipments.tasks"):
        found = check_status_consistency()

    assert found == 2
    entries = {
        entry.object_id: entry.after
        for entry in AuditLog.objects.filter(action=AuditAction.SHIPMENT_STATUS_MISMATCH)
    }
    assert entries == {
        str(drifted.public_id): {"status": "assigned", "last_event_status": "created"},
        str(no_events.public_id): {"status": "created", "last_event_status": None},
    }
    assert len([r for r in caplog.records if r.message == "Shipment status mismatch"]) == 2
    drifted.refresh_from_db()
    assert drifted.status == S.ASSIGNED  # reported, never auto-fixed


def test_consistency_uses_the_latest_recorded_event_not_the_latest_occurred() -> None:
    # A late offline event can carry an earlier device time; the status follows the
    # order in which events were applied.
    shipment = ShipmentFactory.create(status=S.CANCELLED)
    add_event(shipment, S.CREATED)
    add_event(shipment, S.CANCELLED, occurred_at=timezone.now() - timedelta(hours=1))

    assert check_status_consistency() == 0


# --- Property: status always equals the last status event --------------------------------


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(steps=st.lists(st.sampled_from(list(S)), min_size=1, max_size=12))
def test_status_always_matches_last_status_event(steps: list[S]) -> None:
    with transaction.atomic():
        shipment = ShipmentFactory.create(status=S.DRAFT)
        actor = make_user(shipment.company, RoleCode.OWNER)
        for target in steps:
            events_before = TrackingEvent.objects.filter(shipment=shipment).count()
            try:
                shipment = services.transition(
                    actor=actor,
                    shipment=shipment,
                    to=target,
                    expected_status=S(shipment.status),
                    reason="property",
                    request=RequestFactory().post("/"),
                    authorized_by_caller=True,
                )
            except DomainError:
                # A rejected step leaves no trace.
                assert TrackingEvent.objects.filter(shipment=shipment).count() == events_before
            shipment.refresh_from_db()
            last = (
                TrackingEvent.objects.filter(shipment=shipment, type__in=STATUS_EVENT_TYPES)
                .order_by("-recorded_at", "-pk")
                .first()
            )
            if last is None:
                assert shipment.status == S.DRAFT
            else:
                assert shipment.status == last.status_after
        transaction.set_rollback(True)
