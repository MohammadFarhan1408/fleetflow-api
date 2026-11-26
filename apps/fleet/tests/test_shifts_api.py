"""The signed-in driver's own endpoints: profile, shift start and end (memory D-API-28)."""

import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.accounts.tests.conftest import client_for
from apps.core.errors import DomainError
from apps.fleet import selectors, services
from apps.fleet.factories import (
    DriverDocumentFactory,
    DriverFactory,
    DriverLeaveFactory,
    ShiftFactory,
)
from apps.fleet.models import Driver, DriverStatus, Shift
from apps.fleet.tests.conftest import make_driver, make_user, new_key, post
from apps.tenancy.factories import CompanyFactory
from apps.tenancy.models import Branch, Company

pytestmark = pytest.mark.django_db

ME_DRIVER = "/api/v1/me/driver/"
START = "/api/v1/me/shift/start/"
END = "/api/v1/me/shift/end/"
NOW = datetime(2030, 6, 15, 12, 0, tzinfo=UTC)


def keyed(client: APIClient, url: str, body: dict[str, Any] | None = None, key: str = "") -> Any:
    return post(client, url, body, **{"Idempotency-Key": key or new_key()})


def iso(moment: datetime) -> str:
    return moment.isoformat()


@pytest.fixture
def driver(branch: Branch) -> Driver:
    return make_driver(branch)


@pytest.fixture
def driver_client(driver: Driver) -> APIClient:
    return client_for(driver.user)


def code(response: Any) -> str:
    return str(response.json()["code"])


# --- profile -------------------------------------------------------------------------------


def test_my_driver_shows_the_profile_availability_and_open_shift(
    driver_client: APIClient, driver: Driver
) -> None:
    before = driver_client.get(ME_DRIVER)
    started = keyed(driver_client, START)
    during = driver_client.get(ME_DRIVER).json()

    assert before.status_code == 200, before.content
    body = before.json()
    assert body["public_id"] == str(driver.public_id)
    assert body["user"]["email"] == driver.user.email
    assert body["availability"]["available"] is True
    assert body["active_shift"] is None
    assert during["active_shift"] == {
        "public_id": started.json()["public_id"],
        "started_at": started.json()["started_at"],
        "ended_at": None,
    }


def test_my_driver_reports_unavailability_without_blocking(
    driver_client: APIClient, driver: Driver
) -> None:
    Driver.objects.filter(pk=driver.pk).update(status=DriverStatus.SUSPENDED, status_reason="x")

    body = driver_client.get(ME_DRIVER).json()

    assert body["availability"]["reasons"] == ["suspended"]


def test_the_driver_role_gets_shifts_permission_in_me(driver_client: APIClient) -> None:
    permissions = driver_client.get("/api/v1/me/").json()["permissions"]

    assert "shifts.manage_own" in permissions


# --- start and end -------------------------------------------------------------------------


def test_start_and_end_a_shift(driver_client: APIClient, driver: Driver) -> None:
    started = keyed(driver_client, START)
    ended = keyed(driver_client, END)

    assert started.status_code == 201, started.content
    assert started.json()["ended_at"] is None
    assert ended.status_code == 200, ended.content
    assert ended.json()["public_id"] == started.json()["public_id"]
    assert ended.json()["ended_at"] is not None
    shift = Shift.objects.get()
    assert shift.driver == driver
    assert shift.is_open is False
    assert shift.start_recorded_at is not None and shift.end_recorded_at is not None
    assert driver_client.get(ME_DRIVER).json()["active_shift"] is None


def test_a_new_shift_can_start_after_the_last_one_ended(driver_client: APIClient) -> None:
    keyed(driver_client, START)
    keyed(driver_client, END)

    again = keyed(driver_client, START)

    assert again.status_code == 201
    assert Shift.objects.count() == 2
    assert Shift.objects.filter(ended_at=None).count() == 1


def test_the_device_time_is_recorded_and_the_server_time_kept(
    driver_client: APIClient,
) -> None:
    device = timezone.now() - timedelta(hours=3)

    response = keyed(driver_client, START, {"occurred_at": iso(device)})

    assert response.status_code == 201, response.content
    shift = Shift.objects.get()
    assert shift.started_at == device
    assert shift.start_recorded_at > device + timedelta(hours=2)


def test_a_start_without_a_key_is_rejected(driver_client: APIClient) -> None:
    for url in (START, END):
        response = post(driver_client, url)
        assert (response.status_code, code(response)) == (400, "idempotency.key_required")
    assert not Shift.objects.exists()


def test_replaying_a_start_returns_the_first_response(driver_client: APIClient) -> None:
    key = new_key()

    first = keyed(driver_client, START, key=key)
    second = keyed(driver_client, START, key=key)

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json() == second.json()
    assert Shift.objects.count() == 1


def test_replaying_an_end_returns_the_first_response(driver_client: APIClient) -> None:
    keyed(driver_client, START)
    key = new_key()

    first = keyed(driver_client, END, key=key)
    second = keyed(driver_client, END, key=key)

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json()


def test_a_second_start_with_a_new_key_is_409(driver_client: APIClient) -> None:
    keyed(driver_client, START)

    response = keyed(driver_client, START)

    assert (response.status_code, code(response)) == (409, "shift.already_open")
    assert Shift.objects.count() == 1


def test_ending_without_an_open_shift_is_409(driver_client: APIClient) -> None:
    response = keyed(driver_client, END)

    assert (response.status_code, code(response)) == (409, "shift.not_open")


def test_ending_twice_with_new_keys_is_409(driver_client: APIClient) -> None:
    keyed(driver_client, START)
    keyed(driver_client, END)

    response = keyed(driver_client, END)

    assert (response.status_code, code(response)) == (409, "shift.not_open")


@pytest.mark.parametrize(
    "occurred_at",
    [
        lambda: timezone.now() + timedelta(hours=1),  # the future
        lambda: timezone.now() - timedelta(hours=25),  # too old
        lambda: "yesterday",
        lambda: None,
    ],
)
def test_a_bad_device_time_is_400(driver_client: APIClient, occurred_at: Any) -> None:
    value = occurred_at()
    body = {"occurred_at": iso(value) if isinstance(value, datetime) else value}

    response = keyed(driver_client, START, body)

    assert (response.status_code, code(response)) == (400, "validation.invalid")
    assert "occurred_at" in response.json()["errors"]
    assert not Shift.objects.exists()


def test_a_start_before_the_previous_shift_ended_is_400(
    driver_client: APIClient, driver: Driver
) -> None:
    ended = timezone.now() - timedelta(hours=1)
    ShiftFactory.create(
        driver=driver,
        started_at=ended - timedelta(hours=4),
        ended_at=ended,
        end_recorded_at=ended,
    )

    early = keyed(driver_client, START, {"occurred_at": iso(ended - timedelta(minutes=1))})
    at_the_end = keyed(driver_client, START, {"occurred_at": iso(ended)})

    assert (early.status_code, code(early)) == (400, "validation.invalid")
    assert at_the_end.status_code == 201  # starting the moment the last one ended is fine


def test_an_end_that_is_not_after_the_start_is_400(driver_client: APIClient) -> None:
    started = timezone.now() - timedelta(hours=1)
    keyed(driver_client, START, {"occurred_at": iso(started)})

    same = keyed(driver_client, END, {"occurred_at": iso(started)})
    before = keyed(driver_client, END, {"occurred_at": iso(started - timedelta(minutes=5))})
    good = keyed(driver_client, END, {"occurred_at": iso(started + timedelta(minutes=30))})

    assert (same.status_code, before.status_code) == (400, 400)
    assert good.status_code == 200
    assert Shift.objects.get().ended_at == started + timedelta(minutes=30)


# --- the time window (service level: mocking `now` over HTTP would break JWT expiry) -------


@pytest.mark.parametrize(
    ("offset", "accepted"),
    [
        (timedelta(minutes=5), True),  # skew tolerated, edge included
        (timedelta(minutes=5, seconds=1), False),
        (timedelta(0), True),
        (-timedelta(hours=24), True),  # oldest allowed, edge included
        (-timedelta(hours=24, seconds=1), False),
    ],
)
def test_the_device_time_window_boundaries(
    driver: Driver, offset: timedelta, accepted: bool
) -> None:
    with mock.patch("django.utils.timezone.now", return_value=NOW):
        if accepted:
            shift = services.start_shift(actor=driver.user, occurred_at=NOW + offset)
            assert shift.started_at == NOW + offset
        else:
            with pytest.raises(DomainError) as error:
                services.start_shift(actor=driver.user, occurred_at=NOW + offset)
            assert error.value.status_code == 400
            assert not Shift.objects.exists()


def test_an_end_is_bounded_like_a_start(driver: Driver) -> None:
    with mock.patch("django.utils.timezone.now", return_value=NOW):
        services.start_shift(actor=driver.user, occurred_at=NOW - timedelta(hours=2))
        with pytest.raises(DomainError):
            services.end_shift(actor=driver.user, occurred_at=NOW + timedelta(minutes=5, seconds=1))
        ended = services.end_shift(actor=driver.user, occurred_at=NOW + timedelta(minutes=5))

    assert ended.ended_at == NOW + timedelta(minutes=5)


# --- who may start -------------------------------------------------------------------------


def test_a_suspended_driver_cannot_start_but_can_end(
    driver_client: APIClient, driver: Driver
) -> None:
    keyed(driver_client, START)
    Driver.objects.filter(pk=driver.pk).update(status=DriverStatus.SUSPENDED, status_reason="x")

    ended = keyed(driver_client, END)
    start = keyed(driver_client, START)

    assert ended.status_code == 200  # a shift in progress can always be closed
    assert (start.status_code, code(start)) == (409, "driver.suspended")


def test_leave_and_document_gaps_do_not_block_a_shift(branch: Branch) -> None:
    on_leave = make_driver(branch)
    DriverLeaveFactory.create(
        driver=on_leave,
        starts_on=on_leave.company.local_today() - timedelta(days=1),
        ends_on=on_leave.company.local_today() + timedelta(days=1),
    )
    unlicensed = make_driver(branch, licensed=False)
    expired = make_driver(branch, licensed=False)
    DriverDocumentFactory.create(
        driver=expired, expires_on=expired.company.local_today() - timedelta(days=1)
    )

    for driver in (on_leave, unlicensed, expired):
        response = keyed(client_for(driver.user), START)
        assert response.status_code == 201, response.content


@pytest.mark.parametrize("route", [("GET", ME_DRIVER), ("POST", START), ("POST", END)])
def test_an_archived_profile_is_403_on_every_driver_endpoint(
    driver_client: APIClient, driver: Driver, route: tuple[str, str]
) -> None:
    Driver.objects.filter(pk=driver.pk).update(archived_at=timezone.now())
    method, url = route

    response = driver_client.get(url) if method == "GET" else keyed(driver_client, url)

    assert (response.status_code, code(response)) == (403, "driver.profile_inactive")
    assert not Shift.objects.exists()


@pytest.mark.parametrize("route", [("GET", ME_DRIVER), ("POST", START), ("POST", END)])
def test_a_driver_user_without_a_profile_is_403(company: Company, route: tuple[str, str]) -> None:
    client = client_for(UserFactory.create(company=company, roles=[RoleCode.DRIVER]))
    method, url = route

    response = client.get(url) if method == "GET" else keyed(client, url)

    assert (response.status_code, code(response)) == (403, "driver.profile_inactive")


def test_the_profile_check_comes_before_body_validation(company: Company) -> None:
    client = client_for(UserFactory.create(company=company, roles=[RoleCode.DRIVER]))

    response = keyed(client, START, {"occurred_at": "not-a-time"})

    assert (response.status_code, code(response)) == (403, "driver.profile_inactive")


def test_a_driver_needs_no_branch_membership(driver_client: APIClient, driver: Driver) -> None:
    assert not driver.user.branches.exists()

    assert driver_client.get(ME_DRIVER).status_code == 200
    assert keyed(driver_client, START).status_code == 201


# --- access and isolation ------------------------------------------------------------------


@pytest.mark.parametrize("role", list(RoleCode))
def test_only_the_driver_role_has_these_endpoints(
    role: RoleCode, company: Company, branch: Branch
) -> None:
    if role == RoleCode.DRIVER:
        client = client_for(make_driver(branch).user)
    else:
        client = client_for(make_user(company, role, branch))

    results = (client.get(ME_DRIVER), keyed(client, START), keyed(client, END))

    if role == RoleCode.DRIVER:
        assert [r.status_code for r in results] == [200, 201, 200]
    else:
        assert [(r.status_code, code(r)) for r in results] == [(403, "permission.denied")] * 3


def test_unauthenticated_is_401() -> None:
    anonymous = APIClient()

    assert anonymous.get(ME_DRIVER).status_code == 401
    assert anonymous.post(START, {}, format="json").status_code == 401
    assert anonymous.post(END, {}, format="json").status_code == 401


def test_a_driver_only_ever_touches_their_own_shifts(branch: Branch) -> None:
    ann, bob = make_driver(branch), make_driver(branch)
    ann_client, bob_client = client_for(ann.user), client_for(bob.user)
    started = keyed(ann_client, START)

    bob_sees = bob_client.get(ME_DRIVER).json()
    bob_end = keyed(bob_client, END)

    assert bob_sees["public_id"] == str(bob.public_id)
    assert bob_sees["active_shift"] is None
    assert (bob_end.status_code, code(bob_end)) == (409, "shift.not_open")
    assert Shift.objects.get(public_id=started.json()["public_id"]).ended_at is None


def test_another_companys_driver_is_a_separate_world() -> None:
    first = DriverFactory.create()
    second = DriverFactory.create(company=CompanyFactory.create())
    first_client, second_client = client_for(first.user), client_for(second.user)

    keyed(first_client, START)

    assert second_client.get(ME_DRIVER).json()["active_shift"] is None
    assert keyed(second_client, START).status_code == 201
    assert Shift.objects.count() == 2
    assert {s.company_id for s in Shift.objects.all()} == {first.company_id, second.company_id}


# --- database and races --------------------------------------------------------------------


def test_the_database_allows_one_open_shift_per_driver(driver: Driver) -> None:
    ShiftFactory.create(driver=driver)

    with pytest.raises(IntegrityError), transaction.atomic():
        ShiftFactory.create(driver=driver)


def test_the_database_checks_shift_times(driver: Driver) -> None:
    started = timezone.now() - timedelta(hours=3)
    bad = [
        {"ended_at": started, "end_recorded_at": started},  # not after the start
        {"ended_at": started - timedelta(hours=1), "end_recorded_at": started},
        {"ended_at": started + timedelta(hours=1)},  # end without its receipt time
        {"end_recorded_at": started + timedelta(hours=1)},  # receipt time without an end
    ]
    for fields in bad:
        with pytest.raises(IntegrityError), transaction.atomic():
            ShiftFactory.create(driver=driver, started_at=started, **fields)


def test_a_lost_race_is_reported_as_an_open_shift(driver: Driver) -> None:
    with (
        mock.patch.object(Shift, "save", side_effect=IntegrityError),
        mock.patch.object(services, "_open_shift_exists", side_effect=[False, True]),
        pytest.raises(DomainError) as error,
    ):
        services.start_shift(actor=driver.user, occurred_at=None)

    assert error.value.status_code == 409
    assert str(error.value.error_code) == "shift.already_open"


def test_an_unrelated_integrity_error_is_not_swallowed(driver: Driver) -> None:
    with (
        mock.patch.object(Shift, "save", side_effect=IntegrityError),
        mock.patch.object(services, "_open_shift_exists", return_value=False),
        pytest.raises(IntegrityError),
    ):
        services.start_shift(actor=driver.user, occurred_at=None)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_starts_at_once_have_exactly_one_winner() -> None:
    driver = DriverFactory.create()
    user = driver.user
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def start() -> None:
        barrier.wait()
        try:
            services.start_shift(actor=User.objects.get(pk=user.pk), occurred_at=None)
            outcomes.append("ok")
        except DomainError as exc:
            outcomes.append(str(exc.error_code))
        finally:
            connection.close()

    threads = [threading.Thread(target=start) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["ok", "shift.already_open"]
    assert Shift.objects.filter(driver=driver, ended_at=None).count() == 1


def test_selectors_find_the_open_shift(driver: Driver) -> None:
    assert selectors.active_shift(driver) is None
    open_shift = ShiftFactory.create(driver=driver)

    assert selectors.active_shift(driver) == open_shift


def test_services_refuse_a_user_without_a_profile(company: Company) -> None:
    user = UserFactory.create(company=company, roles=[RoleCode.DRIVER])

    for call in (services.start_shift, services.end_shift):
        with pytest.raises(DomainError) as error:
            call(actor=user, occurred_at=None)
        assert str(error.value.error_code) == "driver.profile_inactive"


def test_services_refuse_an_archived_profile_even_if_the_view_did_not(driver: Driver) -> None:
    shift = ShiftFactory.create(driver=driver)
    Driver.objects.filter(pk=driver.pk).update(archived_at=timezone.now())

    for call in (services.start_shift, services.end_shift):
        with pytest.raises(DomainError) as error:
            call(actor=driver.user, occurred_at=None)
        assert str(error.value.error_code) == "driver.profile_inactive"
    shift.refresh_from_db()
    assert shift.ended_at is None
