import itertools
import threading
from datetime import UTC, date, datetime
from typing import Any
from unittest import mock

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.accounts.tests.conftest import client_for
from apps.audit.models import AuditAction, AuditLog
from apps.core.errors import DomainError
from apps.fleet import selectors, services
from apps.fleet.factories import DriverFactory, DriverLeaveFactory
from apps.fleet.models import Driver, DriverLeave, DriverStatus
from apps.fleet.tests.conftest import (
    DRIVERS_URL,
    driver_url,
    make_driver,
    make_user,
    post,
)
from apps.tenancy.factories import CompanyFactory
from apps.tenancy.models import Branch, Company

pytestmark = pytest.mark.django_db


def leave_url(driver: Driver, leave_id: Any = "") -> str:
    return driver_url(driver.public_id, f"leave/{leave_id}" + ("/" if leave_id else ""))


def add_leave(client: APIClient, driver: Driver, starts: str, ends: str) -> Any:
    return post(
        client, leave_url(driver), {"starts_on": starts, "ends_on": ends, "reason": "Holiday"}
    )


def availability(client: APIClient, driver: Driver, on: str | None = None) -> dict[str, Any]:
    url = driver_url(driver.public_id) + (f"?availability_date={on}" if on else "")
    response = client.get(url)
    assert response.status_code == 200, response.content
    body: dict[str, Any] = response.json()["availability"]
    return body


# --- leave API -----------------------------------------------------------------------------


def test_add_and_list_leave(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)

    created = add_leave(client, driver, "2030-01-10", "2030-01-12")
    listed = client.get(leave_url(driver))

    assert created.status_code == 201, created.content
    body = created.json()
    assert (body["starts_on"], body["ends_on"], body["reason"]) == (
        "2030-01-10",
        "2030-01-12",
        "Holiday",
    )
    assert [row["public_id"] for row in listed.json()["results"]] == [body["public_id"]]
    assert AuditLog.objects.filter(action=AuditAction.DRIVER_LEAVE_ADDED).count() == 1


def test_a_single_day_of_leave_is_allowed(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)

    assert add_leave(client, driver, "2030-01-10", "2030-01-10").status_code == 201


@pytest.mark.parametrize(
    ("starts", "ends"),
    [
        ("2030-01-12", "2030-01-10"),  # ends before it starts
        ("2030-01-01", "2031-01-02"),  # 367 days
        ("not-a-date", "2030-01-10"),
    ],
)
def test_invalid_leave_is_400(client: APIClient, branch: Branch, starts: str, ends: str) -> None:
    driver = make_driver(branch)

    response = add_leave(client, driver, starts, ends)

    assert response.status_code == 400
    assert response.json()["code"] == "validation.invalid"


def test_leave_of_366_days_is_the_longest_allowed(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)

    assert add_leave(client, driver, "2030-01-01", "2030-12-31").status_code == 201  # 365 days
    other = make_driver(branch)
    assert add_leave(client, other, "2032-01-01", "2032-12-31").status_code == 201  # 366 (leap)
    third = make_driver(branch)
    assert add_leave(client, third, "2032-01-01", "2033-01-01").status_code == 400  # 367


def test_leave_requires_a_reason(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)

    for reason in ("", "   "):
        response = post(
            client,
            leave_url(driver),
            {"starts_on": "2030-01-10", "ends_on": "2030-01-12", "reason": reason},
        )
        assert response.status_code == 400


@pytest.mark.parametrize(
    ("starts", "ends", "overlaps"),
    [
        ("2030-01-01", "2030-01-09", False),  # ends the day before
        ("2030-01-01", "2030-01-10", True),  # touches the first day
        ("2030-01-11", "2030-01-11", True),  # inside
        ("2030-01-12", "2030-01-20", True),  # touches the last day
        ("2030-01-13", "2030-01-20", False),  # starts the day after
        ("2029-12-01", "2030-02-01", True),  # surrounds
    ],
)
def test_overlapping_leave_is_409_and_adjacent_leave_is_fine(
    client: APIClient, branch: Branch, starts: str, ends: str, overlaps: bool
) -> None:
    driver = make_driver(branch)
    add_leave(client, driver, "2030-01-10", "2030-01-12")

    response = add_leave(client, driver, starts, ends)

    if overlaps:
        assert response.status_code == 409
        assert response.json()["code"] == "driver.leave_overlap"
    else:
        assert response.status_code == 201


def test_two_drivers_can_have_the_same_leave(client: APIClient, branch: Branch) -> None:
    first, second = make_driver(branch), make_driver(branch)

    assert add_leave(client, first, "2030-01-10", "2030-01-12").status_code == 201
    assert add_leave(client, second, "2030-01-10", "2030-01-12").status_code == 201


def test_the_database_rejects_overlapping_leave_even_without_the_service_check(
    branch: Branch,
) -> None:
    leave = DriverLeaveFactory.create(driver=make_driver(branch))

    with pytest.raises(IntegrityError), transaction.atomic():
        DriverLeaveFactory.create(
            driver=leave.driver, starts_on=date(2030, 1, 12), ends_on=date(2030, 1, 14)
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"starts_on": date(2030, 1, 12), "ends_on": date(2030, 1, 10)},
        {"starts_on": date(2030, 1, 1), "ends_on": date(2031, 1, 2)},
        {"reason": ""},
    ],
)
def test_the_database_rejects_invalid_leave(branch: Branch, fields: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        DriverLeaveFactory.create(driver=make_driver(branch), **fields)


def test_delete_leave_only_before_it_starts(
    client: APIClient, company: Company, branch: Branch
) -> None:
    driver = make_driver(branch)
    today = company.local_today()
    future = DriverLeaveFactory.create(
        driver=driver,
        starts_on=today.replace(year=today.year + 1),
        ends_on=today.replace(year=today.year + 1),
    )
    past_start = DriverLeaveFactory.create(
        driver=driver, starts_on=date(2020, 1, 1), ends_on=date(2020, 1, 2)
    )
    starts_today = DriverLeaveFactory.create(driver=driver, starts_on=today, ends_on=today)

    started = client.delete(leave_url(driver, past_start.public_id))
    today_response = client.delete(leave_url(driver, starts_today.public_id))
    removed = client.delete(leave_url(driver, future.public_id))

    assert started.status_code == 409
    assert started.json()["code"] == "driver.leave_started"
    assert today_response.status_code == 409
    assert removed.status_code == 204
    assert not DriverLeave.objects.filter(pk=future.pk).exists()
    assert DriverLeave.objects.filter(pk=past_start.pk).exists()
    assert AuditLog.objects.filter(action=AuditAction.DRIVER_LEAVE_DELETED).count() == 1
    assert client.delete(leave_url(driver, future.public_id)).status_code == 404


def test_leave_belongs_to_its_driver_in_the_url(client: APIClient, branch: Branch) -> None:
    mine, other = make_driver(branch), make_driver(branch)
    leave = DriverLeaveFactory.create(driver=other)

    assert client.delete(leave_url(mine, leave.public_id)).status_code == 404


# --- availability -------------------------------------------------------------------------


def test_availability_boundaries_of_a_leave(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)
    add_leave(client, driver, "2030-01-10", "2030-01-12")

    by_day = {day: availability(client, driver, f"2030-01-{day}") for day in (9, 10, 11, 12, 13)}

    assert {day: row["available"] for day, row in by_day.items()} == {
        9: True,
        10: False,
        11: False,
        12: False,
        13: True,
    }
    assert by_day[10]["reasons"] == ["on_leave"]
    assert by_day[10]["date"] == "2030-01-10"


def test_availability_combines_every_reason(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)
    add_leave(client, driver, "2030-01-10", "2030-01-12")
    post(client, driver_url(driver.public_id, "status/"), {"status": "suspended", "reason": "x"})
    User.objects.filter(pk=driver.user_id).update(is_active=False)
    post(client, driver_url(driver.public_id, "archive/"))

    row = availability(client, driver, "2030-01-11")

    assert row["available"] is False
    assert set(row["reasons"]) == {"archived", "login_disabled", "suspended", "on_leave"}


def test_a_deactivated_login_makes_the_driver_unavailable(
    client: APIClient, branch: Branch
) -> None:
    driver = make_driver(branch)
    User.objects.filter(pk=driver.user_id).update(is_active=False)

    assert availability(client, driver)["reasons"] == ["login_disabled"]


def test_default_date_is_the_companys_today(
    client: APIClient, company: Company, branch: Branch
) -> None:
    driver = make_driver(branch)
    today = company.local_today()
    DriverLeaveFactory.create(driver=driver, starts_on=today, ends_on=today)

    row = availability(client, driver)

    assert row["date"] == today.isoformat()
    assert row["reasons"] == ["on_leave"]


def test_company_today_follows_the_company_timezone() -> None:
    # 06:00 UTC on 11 Jan is still the evening of 10 Jan in Los Angeles (UTC-8) and already
    # the afternoon of the 11th in Tokyo.
    frozen = datetime(2030, 1, 11, 6, 0, tzinfo=UTC)
    los_angeles = CompanyFactory.build(timezone="America/Los_Angeles")
    tokyo = CompanyFactory.build(timezone="Asia/Tokyo")

    with mock.patch("django.utils.timezone.now", return_value=frozen):
        assert los_angeles.local_today() == date(2030, 1, 10)
        assert tokyo.local_today() == date(2030, 1, 11)


def test_availability_date_overrides_the_default(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)
    DriverLeaveFactory.create(driver=driver, starts_on=date(2030, 6, 1), ends_on=date(2030, 6, 5))

    assert availability(client, driver, "2030-06-03")["available"] is False
    assert availability(client, driver, "2030-06-06")["available"] is True


def test_list_available_filter_honours_availability_date(client: APIClient, branch: Branch) -> None:
    on_leave, free = make_driver(branch), make_driver(branch)
    DriverLeaveFactory.create(driver=on_leave, starts_on=date(2030, 6, 1), ends_on=date(2030, 6, 5))

    def free_ids(day: str) -> set[str]:
        response = client.get(f"{DRIVERS_URL}?available=true&availability_date={day}")
        return {d["public_id"] for d in response.json()["results"]}

    assert free_ids("2030-06-03") == {str(free.public_id)}
    assert free_ids("2030-06-06") == {str(free.public_id), str(on_leave.public_id)}


def test_the_list_filter_and_the_reasons_agree_for_every_state(
    client: APIClient, branch: Branch
) -> None:
    """The `available` filter and `availability.reasons` are two implementations of one
    rule; every combination of the four causes must give the same answer in both."""
    for archived, login_off, suspended, on_leave in itertools.product([False, True], repeat=4):
        driver = make_driver(branch)
        if archived:
            Driver.objects.filter(pk=driver.pk).update(archived_at=timezone.now())
        if login_off:
            User.objects.filter(pk=driver.user_id).update(is_active=False)
        if suspended:
            Driver.objects.filter(pk=driver.pk).update(
                status=DriverStatus.SUSPENDED, status_reason="x"
            )
        if on_leave:
            DriverLeaveFactory.create(
                driver=driver, starts_on=date(2030, 6, 1), ends_on=date(2030, 6, 5)
            )

    base = f"{DRIVERS_URL}?include_archived=true&availability_date=2030-06-03&page_size=100"
    rows = client.get(base).json()["results"]
    available_ids = {r["public_id"] for r in client.get(base + "&available=true").json()["results"]}
    unavailable_ids = {
        r["public_id"] for r in client.get(base + "&available=false").json()["results"]
    }

    assert len(rows) == 16
    assert available_ids == {r["public_id"] for r in rows if r["availability"]["available"]}
    assert unavailable_ids == {r["public_id"] for r in rows if not r["availability"]["available"]}
    assert len(available_ids) == 1 and len(unavailable_ids) == 15


def test_selector_reasons_match_the_row_for_unannotated_inputs(
    company: Company, branch: Branch
) -> None:
    admin = make_user(company, RoleCode.ADMIN)
    driver = make_driver(branch)

    row = selectors.get_driver(admin, driver.public_id, date(2030, 1, 1))

    assert selectors.driver_unavailability(row) == []


# --- concurrency and scope -----------------------------------------------------------------


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_overlapping_leaves_at_once_have_exactly_one_winner() -> None:
    company = CompanyFactory.create()
    branch = Branch.objects.create(company=company, name="Main", code="MAIN")
    actor = UserFactory.create(company=company, roles=[RoleCode.OWNER])
    driver = DriverFactory.create(company=company, branch=branch)
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def add(starts: date, ends: date) -> None:
        barrier.wait()
        try:
            services.add_leave(
                actor=actor,
                driver=driver,
                starts_on=starts,
                ends_on=ends,
                reason="Holiday",
                request=RequestFactory().post("/"),
            )
            outcomes.append("ok")
        except DomainError as exc:
            outcomes.append(str(exc.error_code))
        finally:
            connection.close()

    threads = [
        threading.Thread(target=add, args=(date(2030, 1, 10), date(2030, 1, 12))),
        threading.Thread(target=add, args=(date(2030, 1, 11), date(2030, 1, 13))),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["driver.leave_overlap", "ok"]
    assert DriverLeave.objects.filter(driver=driver).count() == 1


def test_services_refuse_a_driver_outside_the_actors_branches(
    company: Company, branch: Branch, other_branch: Branch
) -> None:
    narrow = make_user(company, RoleCode.FLEET_MANAGER, branch)
    driver = make_driver(other_branch)

    with pytest.raises(DomainError) as error:
        services.set_driver_status(
            actor=narrow,
            driver=driver,
            status=DriverStatus.SUSPENDED,
            reason="x",
            request=RequestFactory().post("/"),
        )

    assert error.value.status_code == 404
    driver.refresh_from_db()
    assert driver.status == DriverStatus.ACTIVE


def test_leave_endpoints_are_scoped_by_the_drivers_branch(
    company: Company, branch: Branch, other_branch: Branch
) -> None:
    narrow = client_for(make_user(company, RoleCode.FLEET_MANAGER, branch))
    driver = make_driver(other_branch)
    leave = DriverLeaveFactory.create(driver=driver)

    assert narrow.get(leave_url(driver)).status_code == 404
    assert add_leave(narrow, driver, "2031-01-10", "2031-01-11").status_code == 404
    assert narrow.delete(leave_url(driver, leave.public_id)).status_code == 404
