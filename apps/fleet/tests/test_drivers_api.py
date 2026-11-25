from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.accounts.tests.conftest import client_for
from apps.audit.models import AuditAction, AuditLog
from apps.fleet.models import Driver, DriverStatus
from apps.fleet.tests.conftest import (
    DRIVERS_URL,
    driver_url,
    make_driver,
    make_user,
    new_key,
    post,
)
from apps.tenancy.factories import BranchFactory, CompanyFactory
from apps.tenancy.models import Branch, Company

pytestmark = pytest.mark.django_db


def create_body(user: User, branch: Branch, **overrides: Any) -> dict[str, Any]:
    return {
        "user_id": str(user.public_id),
        "branch_id": str(branch.public_id),
        "phone": "+4915112345678",
    } | overrides


def code(response: Any) -> str:
    return str(response.json()["code"])


# --- create ---------------------------------------------------------------------------------


def test_create_driver_for_a_driver_user(
    client: APIClient, driver_user: User, branch: Branch
) -> None:
    response = post(
        client, DRIVERS_URL, create_body(driver_user, branch), **{"Idempotency-Key": new_key()}
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["user"] == {
        "public_id": str(driver_user.public_id),
        "full_name": driver_user.full_name,
        "email": driver_user.email,
    }
    assert body["branch"] == {
        "public_id": str(branch.public_id),
        "code": "BER1",
        "name": branch.name,
    }
    assert (body["phone"], body["status"], body["status_reason"]) == (
        "+4915112345678",
        "active",
        "",
    )
    assert body["availability"]["available"] is True
    assert body["availability"]["reasons"] == []
    driver = Driver.objects.get(public_id=body["public_id"])
    assert driver.company == driver_user.company
    entry = AuditLog.objects.get(action=AuditAction.DRIVER_CREATED)
    assert entry.object_id == body["public_id"]
    assert driver_user.email not in str(entry.after)


def test_create_is_idempotent_with_the_same_key(
    client: APIClient, driver_user: User, branch: Branch
) -> None:
    key = new_key()
    body = create_body(driver_user, branch)

    first = post(client, DRIVERS_URL, body, **{"Idempotency-Key": key})
    second = post(client, DRIVERS_URL, body, **{"Idempotency-Key": key})

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.headers["Idempotent-Replayed"] == "true"
    assert Driver.objects.count() == 1


@pytest.mark.parametrize("field", ["user_id", "branch_id", "phone"])
def test_create_requires_its_fields(
    client: APIClient, driver_user: User, branch: Branch, field: str
) -> None:
    body = create_body(driver_user, branch)
    del body[field]

    response = post(client, DRIVERS_URL, body)

    assert response.status_code == 400
    assert code(response) == "validation.invalid"
    assert field in response.json()["errors"]


@pytest.mark.parametrize("phone", ["0151 1234567", "+0123456", "+49 151 123", "abc", ""])
def test_create_rejects_a_phone_that_is_not_e164(
    client: APIClient, driver_user: User, branch: Branch, phone: str
) -> None:
    response = post(client, DRIVERS_URL, create_body(driver_user, branch, phone=phone))

    assert response.status_code == 400
    assert "phone" in response.json()["errors"]


def test_create_rejects_a_user_without_the_driver_role(
    client: APIClient, company: Company, branch: Branch
) -> None:
    dispatcher = make_user(company, RoleCode.DISPATCHER)

    response = post(client, DRIVERS_URL, create_body(dispatcher, branch))

    assert response.status_code == 400
    assert code(response) == "validation.invalid_reference"
    assert Driver.objects.count() == 0


def test_create_rejects_an_inactive_user(
    client: APIClient, driver_user: User, branch: Branch
) -> None:
    User.objects.filter(pk=driver_user.pk).update(is_active=False)

    response = post(client, DRIVERS_URL, create_body(driver_user, branch))

    assert code(response) == "validation.invalid_reference"


def test_create_rejects_another_companys_user_and_branch_without_echoing_them(
    client: APIClient, driver_user: User, branch: Branch
) -> None:
    foreign_user = UserFactory.create(roles=[RoleCode.DRIVER])
    foreign_branch = BranchFactory.create(company=foreign_user.company)

    user_response = post(client, DRIVERS_URL, create_body(foreign_user, branch))
    branch_response = post(client, DRIVERS_URL, create_body(driver_user, foreign_branch))

    for response, field in ((user_response, "user_id"), (branch_response, "branch_id")):
        assert response.status_code == 400
        assert code(response) == "validation.invalid_reference"
        assert field in response.json()["errors"]
        assert str(foreign_user.public_id) not in response.content.decode()
        assert str(foreign_branch.public_id) not in response.content.decode()
    assert Driver.objects.count() == 0


def test_create_rejects_a_second_profile_for_the_same_user(
    client: APIClient, driver_user: User, branch: Branch
) -> None:
    make_driver(branch, user=driver_user)

    response = post(client, DRIVERS_URL, create_body(driver_user, branch))

    assert response.status_code == 409
    assert code(response) == "driver.already_exists"


def test_create_rejects_a_branch_outside_the_actors_scope(
    client: APIClient, driver_user: User, other_branch: Branch
) -> None:
    # The fleet manager belongs to `branch` only.
    response = post(client, DRIVERS_URL, create_body(driver_user, other_branch))

    assert response.status_code == 400
    assert code(response) == "validation.invalid_reference"


def test_all_branches_permission_allows_any_branch_of_the_company(
    company: Company, driver_user: User, other_branch: Branch
) -> None:
    admin = client_for(make_user(company, RoleCode.ADMIN))

    response = post(admin, DRIVERS_URL, create_body(driver_user, other_branch))

    assert response.status_code == 201


# --- read and update -----------------------------------------------------------------------


def test_detail_and_unknown_id(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)

    ok = client.get(driver_url(driver.public_id))
    missing = client.get(driver_url("00000000-0000-7000-8000-000000000000"))

    assert ok.status_code == 200
    assert ok.json()["public_id"] == str(driver.public_id)
    assert missing.status_code == 404


def test_patch_changes_phone_and_branch(
    client: APIClient, company: Company, branch: Branch
) -> None:
    second = BranchFactory.create(company=company, code="HAM1")
    fleet = make_user(company, RoleCode.FLEET_MANAGER, branch, second)
    driver = make_driver(branch)

    response = client_for(fleet).patch(
        driver_url(driver.public_id),
        {"phone": "+4930123456", "branch_id": str(second.public_id)},
        format="json",
    )

    assert response.status_code == 200
    driver.refresh_from_db()
    assert (driver.phone, driver.branch) == ("+4930123456", second)


def test_patch_with_only_the_phone_keeps_the_branch(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)

    response = client.patch(driver_url(driver.public_id), {"phone": "+4930123456"}, format="json")

    assert response.status_code == 200
    driver.refresh_from_db()
    assert driver.branch == branch


def test_patch_cannot_move_a_driver_to_a_branch_outside_scope(
    client: APIClient, branch: Branch, other_branch: Branch
) -> None:
    driver = make_driver(branch)

    response = client.patch(
        driver_url(driver.public_id), {"branch_id": str(other_branch.public_id)}, format="json"
    )

    assert response.status_code == 400
    assert code(response) == "validation.invalid_reference"


def test_patch_of_an_archived_driver_is_409(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)
    post(client, driver_url(driver.public_id, "archive/"))

    response = client.patch(driver_url(driver.public_id), {"phone": "+4930123456"}, format="json")

    assert response.status_code == 409
    assert code(response) == "driver.archived"


def test_the_user_is_not_changeable_through_patch(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)
    other = UserFactory.create(company=branch.company, roles=[RoleCode.DRIVER])

    client.patch(driver_url(driver.public_id), {"user_id": str(other.public_id)}, format="json")

    driver.refresh_from_db()
    assert driver.user != other


# --- status and archive --------------------------------------------------------------------


def test_suspend_and_reinstate(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)
    url = driver_url(driver.public_id, "status/")

    suspended = post(client, url, {"status": "suspended", "reason": "Licence under review"})
    reinstated = post(client, url, {"status": "active"})

    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"
    assert suspended.json()["status_reason"] == "Licence under review"
    assert suspended.json()["availability"]["reasons"] == ["suspended"]
    assert reinstated.json()["status"] == "active"
    assert reinstated.json()["status_reason"] == ""
    assert reinstated.json()["availability"]["available"] is True


def test_suspending_requires_a_reason(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)

    for body in ({"status": "suspended"}, {"status": "suspended", "reason": "   "}):
        response = post(client, driver_url(driver.public_id, "status/"), body)
        assert response.status_code == 400
        assert "reason" in response.json()["errors"]
    driver.refresh_from_db()
    assert driver.status == DriverStatus.ACTIVE


def test_invalid_status_is_400(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)

    response = post(client, driver_url(driver.public_id, "status/"), {"status": "retired"})

    assert response.status_code == 400


def test_repeating_a_status_change_is_a_no_op_and_the_audit_log_holds_no_reason(
    client: APIClient, branch: Branch
) -> None:
    driver = make_driver(branch)
    url = driver_url(driver.public_id, "status/")
    body = {"status": "suspended", "reason": "Secret HR matter"}

    post(client, url, body)
    post(client, url, body)

    entries = AuditLog.objects.filter(action=AuditAction.DRIVER_STATUS_CHANGED)
    assert entries.count() == 1
    assert entries.get().after == {"status": "suspended"}
    assert "Secret HR matter" not in str(list(AuditLog.objects.values_list("after", flat=True)))


def test_changing_only_the_reason_updates_it_without_a_new_audit_entry(
    client: APIClient, branch: Branch
) -> None:
    driver = make_driver(branch)
    url = driver_url(driver.public_id, "status/")
    post(client, url, {"status": "suspended", "reason": "first"})

    response = post(client, url, {"status": "suspended", "reason": "second"})

    assert response.json()["status_reason"] == "second"
    assert AuditLog.objects.filter(action=AuditAction.DRIVER_STATUS_CHANGED).count() == 1


def test_archive_is_idempotent_and_audited_once(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)
    url = driver_url(driver.public_id, "archive/")

    first = post(client, url)
    second = post(client, url)

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json()["archived_at"] == second.json()["archived_at"]
    assert first.json()["availability"]["reasons"] == ["archived"]
    assert AuditLog.objects.filter(action=AuditAction.DRIVER_ARCHIVED).count() == 1


def test_an_archived_driver_cannot_change_status_or_get_leave(
    client: APIClient, branch: Branch
) -> None:
    driver = make_driver(branch)
    post(client, driver_url(driver.public_id, "archive/"))

    status_response = post(client, driver_url(driver.public_id, "status/"), {"status": "active"})
    leave_response = post(
        client,
        driver_url(driver.public_id, "leave/"),
        {"starts_on": "2030-01-10", "ends_on": "2030-01-12", "reason": "Holiday"},
    )

    assert (status_response.status_code, code(status_response)) == (409, "driver.archived")
    assert (leave_response.status_code, code(leave_response)) == (409, "driver.archived")


# --- list ------------------------------------------------------------------------------------


def test_list_is_scoped_to_the_users_branches(
    company: Company, branch: Branch, other_branch: Branch
) -> None:
    mine = make_driver(branch)
    theirs = make_driver(other_branch)
    make_driver(BranchFactory.create(company=CompanyFactory.create()))
    fleet = client_for(make_user(company, RoleCode.FLEET_MANAGER, branch))
    admin = client_for(make_user(company, RoleCode.ADMIN))

    narrow = {d["public_id"] for d in fleet.get(DRIVERS_URL).json()["results"]}
    wide = {d["public_id"] for d in admin.get(DRIVERS_URL).json()["results"]}

    assert narrow == {str(mine.public_id)}
    assert wide == {str(mine.public_id), str(theirs.public_id)}
    assert fleet.get(driver_url(theirs.public_id)).status_code == 404


def test_list_filters(client: APIClient, company: Company, branch: Branch) -> None:
    ann = make_driver(branch, user__full_name="Ann Active", user__email="ann@example.com")
    sue = make_driver(branch, user__full_name="Sue Suspended", user__email="sue@example.com")
    old = make_driver(branch, user__full_name="Old Archived", user__email="old@example.com")
    post(client, driver_url(sue.public_id, "status/"), {"status": "suspended", "reason": "x"})
    post(client, driver_url(old.public_id, "archive/"))

    def ids(query: str) -> set[str]:
        response = client.get(f"{DRIVERS_URL}?{query}")
        assert response.status_code == 200, response.content
        return {d["public_id"] for d in response.json()["results"]}

    assert ids("") == {str(ann.public_id), str(sue.public_id)}  # archived hidden
    assert ids("include_archived=true") == {str(d.public_id) for d in (ann, sue, old)}
    assert ids("status=suspended") == {str(sue.public_id)}
    assert ids("search=SUE") == {str(sue.public_id)}
    assert ids("search=ann@example") == {str(ann.public_id)}
    assert ids(f"branch={branch.public_id}") == {str(ann.public_id), str(sue.public_id)}
    assert ids("available=true") == {str(ann.public_id)}
    assert ids("available=false") == {str(sue.public_id)}


@pytest.mark.parametrize(
    "query",
    ["status=retired", "branch=not-a-uuid", "availability_date=31-01-2030", "available=maybe"],
)
def test_list_rejects_invalid_filters(client: APIClient, query: str) -> None:
    response = client.get(f"{DRIVERS_URL}?{query}")

    assert response.status_code == 400
    assert code(response) == "validation.invalid"


def test_list_query_count_does_not_grow_with_rows(
    client: APIClient, branch: Branch, django_assert_max_num_queries: Any
) -> None:
    make_driver(branch)
    with django_assert_max_num_queries(8):
        assert client.get(DRIVERS_URL).status_code == 200
    for _ in range(15):
        make_driver(branch)

    with django_assert_max_num_queries(8):  # the same ceiling for 16 rows
        response = client.get(DRIVERS_URL)

    assert len(response.json()["results"]) == 16


# --- access ----------------------------------------------------------------------------------

READ_ROLES = {
    RoleCode.OWNER,
    RoleCode.ADMIN,
    RoleCode.OPS_MANAGER,
    RoleCode.DISPATCHER,
    RoleCode.FLEET_MANAGER,
    RoleCode.FINANCE,
    RoleCode.VIEWER,
}
WRITE_ROLES = {RoleCode.OWNER, RoleCode.ADMIN, RoleCode.OPS_MANAGER, RoleCode.FLEET_MANAGER}


@pytest.mark.parametrize("role", list(RoleCode))
def test_role_matrix(role: RoleCode, company: Company, branch: Branch, driver_user: User) -> None:
    user = make_user(company, role, branch)
    driver = make_driver(branch)
    client = client_for(user)

    read = client.get(DRIVERS_URL)
    write = post(client, DRIVERS_URL, create_body(driver_user, branch))
    patch = client.patch(driver_url(driver.public_id), {"phone": "+4930123456"}, format="json")

    assert read.status_code == (200 if role in READ_ROLES else 403)
    assert write.status_code == (201 if role in WRITE_ROLES else 403)
    assert patch.status_code == (200 if role in WRITE_ROLES else 403)


def test_unauthenticated_is_401(branch: Branch) -> None:
    driver = make_driver(branch)
    anonymous = APIClient()

    assert anonymous.get(DRIVERS_URL).status_code == 401
    assert anonymous.get(driver_url(driver.public_id)).status_code == 401
    assert anonymous.post(driver_url(driver.public_id, "status/")).status_code == 401
