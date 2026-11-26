"""Routes over HTTP: create, list, read, edit, scope and access."""

from datetime import timedelta
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import RoleCode
from apps.accounts.tests.conftest import client_for
from apps.audit.models import AuditAction, AuditLog
from apps.dispatch.factories import RouteFactory, RouteStopFactory
from apps.dispatch.models import Route, RouteStatus
from apps.dispatch.tests.conftest import (
    ROUTES_URL,
    code,
    make_shipment,
    make_user,
    post,
    route_url,
    version_of,
)
from apps.tenancy.factories import BranchFactory
from apps.tenancy.models import Branch, Company

pytestmark = pytest.mark.django_db


def create_body(branch: Branch, **overrides: Any) -> dict[str, Any]:
    return {"branch_id": str(branch.public_id), "route_date": "2099-06-15"} | overrides


def create(client: APIClient, branch: Branch, **overrides: Any) -> Any:
    return post(client, ROUTES_URL, create_body(branch, **overrides))


# --- create --------------------------------------------------------------------------------


def test_create_a_route(client: APIClient, branch: Branch) -> None:
    response = create(client, branch, sequence_enforced=False)

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["status"] == "planned"
    assert (body["version"], body["stop_count"], body["stops"]) == (1, 0, [])
    assert body["sequence_enforced"] is False
    assert body["branch"] == {
        "public_id": str(branch.public_id),
        "code": "BER1",
        "name": branch.name,
    }
    entry = AuditLog.objects.get(action=AuditAction.ROUTE_CREATED)
    assert entry.object_id == body["public_id"]
    assert entry.after == {"status": "planned"}


def test_sequencing_is_enforced_by_default(client: APIClient, branch: Branch) -> None:
    assert create(client, branch).json()["sequence_enforced"] is True


def test_replaying_a_create_makes_one_route(client: APIClient, branch: Branch) -> None:
    headers = {"Idempotency-Key": "route-create-1"}
    first = post(client, ROUTES_URL, create_body(branch), **headers)
    second = post(client, ROUTES_URL, create_body(branch), **headers)

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json() == second.json()
    assert Route.objects.count() == 1


def test_a_route_can_be_for_today_but_not_for_the_past(
    client: APIClient, company: Company, branch: Branch
) -> None:
    today = company.local_today()

    ok = create(client, branch, route_date=str(today))
    past = create(client, branch, route_date=str(today - timedelta(days=1)))

    assert ok.status_code == 201
    assert (past.status_code, code(past)) == (400, "validation.invalid")
    assert "route_date" in past.json()["errors"]


@pytest.mark.parametrize("missing", ["branch_id", "route_date"])
def test_create_needs_branch_and_date(client: APIClient, branch: Branch, missing: str) -> None:
    body = create_body(branch)
    del body[missing]

    response = post(client, ROUTES_URL, body)

    assert (response.status_code, code(response)) == (400, "validation.invalid")


def test_another_branch_or_company_is_a_missing_reference(
    client: APIClient, other_branch: Branch
) -> None:
    foreign = BranchFactory.create()

    for branch in (other_branch, foreign):  # outside the dispatcher's branches / not theirs
        response = create(client, branch)
        assert (response.status_code, code(response)) == (400, "validation.invalid_reference")
        assert str(branch.public_id) not in response.content.decode()
    assert not Route.objects.exists()


# --- list and read -------------------------------------------------------------------------


def test_list_filters_and_scope(
    client: APIClient, company: Company, branch: Branch, other_branch: Branch
) -> None:
    mine = RouteFactory.create(branch=branch)
    later = RouteFactory.create(branch=branch, route_date="2099-07-01")
    cancelled = RouteFactory.create(
        branch=branch, status=RouteStatus.CANCELLED, cancelled_at=timezone.now()
    )
    far = RouteFactory.create(branch=other_branch)
    RouteFactory.create()  # another company

    def ids(query: str = "", who: APIClient = client) -> set[str]:
        response = who.get(f"{ROUTES_URL}?{query}")
        assert response.status_code == 200, response.content
        return {r["public_id"] for r in response.json()["results"]}

    everything = {str(r.public_id) for r in (mine, later, cancelled)}
    assert ids() == everything  # not the other branch's, not the other company's
    assert ids("route_date=2099-07-01") == {str(later.public_id)}
    assert ids("status=cancelled") == {str(cancelled.public_id)}
    assert ids(f"branch={branch.public_id}") == everything
    admin = client_for(make_user(company, RoleCode.ADMIN))  # all branches
    assert ids("", admin) == everything | {str(far.public_id)}
    for query in ("status=flying", "route_date=15-06-2099", "branch=x"):
        assert client.get(f"{ROUTES_URL}?{query}").status_code == 400


def test_list_shows_stop_counts_in_constant_queries(client: APIClient, branch: Branch) -> None:
    for _ in range(2):
        RouteStopFactory.create(route=RouteFactory.create(branch=branch))
    with CaptureQueriesContext(connection) as small:
        assert client.get(ROUTES_URL).status_code == 200
    for _ in range(8):
        RouteStopFactory.create(route=RouteFactory.create(branch=branch))

    with CaptureQueriesContext(connection) as large:
        response = client.get(ROUTES_URL)

    assert len(response.json()["results"]) == 10
    assert {r["stop_count"] for r in response.json()["results"]} == {1}
    assert len(large) == len(small)


def test_detail_shows_the_stops_in_order_in_constant_queries(
    client: APIClient, route: Route
) -> None:
    RouteStopFactory.create(route=route, sequence=1)
    with CaptureQueriesContext(connection) as small:
        assert client.get(route_url(route)).status_code == 200
    for n in range(2, 10):
        RouteStopFactory.create(route=route, sequence=n)

    with CaptureQueriesContext(connection) as large:
        body = client.get(route_url(route)).json()

    assert [s["sequence"] for s in body["stops"]] == list(range(1, 10))
    assert body["stop_count"] == 9
    assert len(large) == len(small)


def test_unknown_and_other_branch_routes_are_404(
    client: APIClient, other_branch: Branch, route: Route
) -> None:
    far = RouteFactory.create(branch=other_branch)

    assert client.get(route_url(far)).status_code == 404
    assert client.get(route_url("00000000-0000-0000-0000-000000000000")).status_code == 404
    assert client.get(route_url(route)).status_code == 200


# --- edit ----------------------------------------------------------------------------------


def test_edit_the_date_and_flag_bumps_the_version(client: APIClient, route: Route) -> None:
    response = client.patch(
        route_url(route),
        {"version": 1, "route_date": "2099-08-01", "sequence_enforced": False},
        format="json",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert (body["route_date"], body["sequence_enforced"], body["version"]) == (
        "2099-08-01",
        False,
        2,
    )


def test_a_stale_version_is_409(client: APIClient, route: Route) -> None:
    client.patch(route_url(route), {"version": 1, "sequence_enforced": False}, format="json")

    stale = client.patch(route_url(route), {"version": 1, "sequence_enforced": True}, format="json")

    assert (stale.status_code, code(stale)) == (409, "route.version_conflict")
    assert Route.objects.get(pk=route.pk).sequence_enforced is False
    assert version_of(route) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"version": 1},  # nothing to change
        {"sequence_enforced": False},  # no version
        {"version": 0, "sequence_enforced": False},
        {"version": 1, "route_date": "2000-01-01"},  # the past
    ],
)
def test_invalid_edits_are_400(client: APIClient, route: Route, body: dict[str, Any]) -> None:
    response = client.patch(route_url(route), body, format="json")

    assert (response.status_code, code(response)) == (400, "validation.invalid")
    assert version_of(route) == 1


@pytest.mark.parametrize(
    "route_status",
    [RouteStatus.ASSIGNED, RouteStatus.DISPATCHED, RouteStatus.IN_PROGRESS, RouteStatus.COMPLETED],
)
def test_only_a_planned_route_can_be_edited(
    client: APIClient, branch: Branch, route_status: RouteStatus
) -> None:
    route = RouteFactory.create(branch=branch, status=route_status)

    response = client.patch(
        route_url(route), {"version": 1, "sequence_enforced": False}, format="json"
    )

    assert (response.status_code, code(response)) == (409, "route.invalid_status")


# --- access --------------------------------------------------------------------------------

READ_ROLES = {
    RoleCode.OWNER,
    RoleCode.ADMIN,
    RoleCode.OPS_MANAGER,
    RoleCode.DISPATCHER,
    RoleCode.FLEET_MANAGER,
    RoleCode.CUSTOMER_SERVICE,
    RoleCode.VIEWER,
}
WRITE_ROLES = {RoleCode.OWNER, RoleCode.ADMIN, RoleCode.OPS_MANAGER, RoleCode.DISPATCHER}


@pytest.mark.parametrize("role", list(RoleCode))
def test_role_matrix(role: RoleCode, company: Company, branch: Branch) -> None:
    client = client_for(make_user(company, role, branch))
    route = RouteFactory.create(branch=branch)
    shipment = make_shipment(branch)

    read = client.get(ROUTES_URL)
    detail = client.get(route_url(route))
    created = create(client, branch)
    patched = client.patch(
        route_url(route), {"version": 1, "sequence_enforced": False}, format="json"
    )
    stop = post(
        client,
        route_url(route, "stops/"),
        {"version": 2, "shipment_id": str(shipment.public_id), "type": "pickup"},
    )
    reordered = post(client, route_url(route, "reorder/"), {"version": 3, "stop_ids": []})
    cancelled = post(client, route_url(route, "cancel/"), {"version": 99})
    removed = client.delete(
        route_url(route, "stops/00000000-0000-0000-0000-000000000000/?version=3")
    )

    can_read, can_write = role in READ_ROLES, role in WRITE_ROLES
    assert read.status_code == (200 if can_read else 403)
    assert detail.status_code == (200 if can_read else 403)
    assert created.status_code == (201 if can_write else 403)
    assert patched.status_code == (200 if can_write else 403)
    assert stop.status_code == (201 if can_write else 403)
    assert reordered.status_code == (400 if can_write else 403)  # the stop is not listed
    assert cancelled.status_code == (409 if can_write else 403)  # stale version, but allowed
    assert removed.status_code == (404 if can_write else 403)


def test_unauthenticated_is_401(route: Route) -> None:
    anonymous = APIClient()

    assert anonymous.get(ROUTES_URL).status_code == 401
    assert anonymous.get(route_url(route)).status_code == 401
    assert anonymous.post(ROUTES_URL, {}, format="json").status_code == 401
    assert anonymous.delete(route_url(route, f"stops/{route.public_id}/")).status_code == 401


def test_a_date_alone_or_the_flag_alone_can_be_edited(client: APIClient, route: Route) -> None:
    date_only = client.patch(
        route_url(route), {"version": 1, "route_date": "2099-09-01"}, format="json"
    )
    flag_only = client.patch(
        route_url(route), {"version": 2, "sequence_enforced": False}, format="json"
    )

    assert date_only.json()["route_date"] == "2099-09-01"
    assert date_only.json()["sequence_enforced"] is True
    assert flag_only.json()["route_date"] == "2099-09-01"
    assert flag_only.json()["sequence_enforced"] is False
