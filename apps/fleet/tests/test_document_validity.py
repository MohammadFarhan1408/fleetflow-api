"""When a document counts (memory D-API-27): valid through `expires_on`, inclusive, in the
company timezone; archived documents never count. These tests use the selectors and the
availability they feed, which is what Phase 4 assignment will call."""

import itertools
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import RoleCode, User
from apps.fleet import selectors
from apps.fleet.factories import (
    DriverDocumentFactory,
    VehicleDocumentFactory,
    VehicleFactory,
)
from apps.fleet.models import (
    Driver,
    DriverDocumentType,
    DriverStatus,
    LicenseCategory,
    Vehicle,
    VehicleDocumentType,
    VehicleStatus,
)
from apps.fleet.tests.conftest import (
    DRIVERS_URL,
    VEHICLES_URL,
    document_vehicle,
    driver_url,
    make_driver,
    make_user,
    vehicle_url,
)
from apps.tenancy.factories import BranchFactory, CompanyFactory
from apps.tenancy.models import Branch, Company

pytestmark = pytest.mark.django_db

TODAY = date(2030, 6, 15)
DAY = timedelta(days=1)


# --- the validity rule ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expires_on", "valid"),
    [
        (TODAY - DAY, False),  # yesterday: expired
        (TODAY, True),  # today: valid through its last day
        (TODAY + DAY, True),
    ],
)
def test_a_document_is_valid_through_its_expiry_date(
    branch: Branch, expires_on: date, valid: bool
) -> None:
    driver = make_driver(branch, licensed=False)
    DriverDocumentFactory.create(driver=driver, expires_on=expires_on)

    assert (selectors.valid_license(driver, TODAY) is not None) is valid


@pytest.mark.parametrize(
    ("document_type", "selector_type"),
    [
        (VehicleDocumentType.REGISTRATION, VehicleDocumentType.REGISTRATION),
        (VehicleDocumentType.INSURANCE, VehicleDocumentType.INSURANCE),
    ],
)
@pytest.mark.parametrize(("offset", "valid"), [(-1, False), (0, True), (1, True)])
def test_vehicle_documents_use_the_same_boundary(
    branch: Branch,
    document_type: VehicleDocumentType,
    selector_type: VehicleDocumentType,
    offset: int,
    valid: bool,
) -> None:
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)
    VehicleDocumentFactory.create(
        vehicle=vehicle, type=document_type, expires_on=TODAY + offset * DAY
    )

    found = selectors.valid_vehicle_document(vehicle, selector_type, TODAY)

    assert (found is not None) is valid


def test_only_the_asked_type_counts(branch: Branch) -> None:
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)
    VehicleDocumentFactory.create(vehicle=vehicle, type=VehicleDocumentType.INSURANCE)

    assert (
        selectors.valid_vehicle_document(vehicle, VehicleDocumentType.REGISTRATION, TODAY) is None
    )
    assert selectors.valid_vehicle_document(vehicle, VehicleDocumentType.INSURANCE, TODAY)


def test_an_archived_document_never_counts(branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)
    DriverDocumentFactory.create(driver=driver, archived_at=timezone.now())

    assert selectors.valid_license(driver, TODAY) is None


def test_the_newest_valid_licence_wins(branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)
    old = DriverDocumentFactory.create(
        driver=driver, issued_on=date(2020, 1, 1), categories=[LicenseCategory.B]
    )
    new = DriverDocumentFactory.create(
        driver=driver, issued_on=date(2024, 1, 1), categories=[LicenseCategory.C]
    )
    DriverDocumentFactory.create(  # newest, but expired: ignored
        driver=driver,
        issued_on=date(2029, 1, 1),
        expires_on=TODAY - DAY,
        categories=[LicenseCategory.D],
    )

    assert selectors.valid_license(driver, TODAY) == new
    new.archived_at = timezone.now()
    new.save()
    assert selectors.valid_license(driver, TODAY) == old


@pytest.mark.parametrize(
    ("held", "required", "covers"),
    [
        ([LicenseCategory.C], LicenseCategory.C, True),
        ([LicenseCategory.B, LicenseCategory.C], LicenseCategory.C, True),
        ([LicenseCategory.C], LicenseCategory.C1, False),  # exact match: C does not give C1
        ([LicenseCategory.C], LicenseCategory.CE, False),
        ([LicenseCategory.CE], LicenseCategory.C, False),
        ([LicenseCategory.B], LicenseCategory.BE, False),
        ([LicenseCategory.D1], LicenseCategory.D, False),
    ],
)
def test_licence_categories_match_exactly(
    branch: Branch, held: list[str], required: LicenseCategory, covers: bool
) -> None:
    driver = make_driver(branch, licensed=False)
    DriverDocumentFactory.create(driver=driver, categories=held)
    vehicle = VehicleFactory.create(
        company=branch.company, branch=branch, required_license_category=required
    )

    assert selectors.license_covers(driver, vehicle, TODAY) is covers


def test_licence_covers_needs_a_valid_licence(branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)
    assert selectors.license_covers(driver, vehicle, TODAY) is False  # none on file

    DriverDocumentFactory.create(driver=driver, expires_on=TODAY - DAY, categories=["C"])
    assert selectors.license_covers(driver, vehicle, TODAY) is False  # expired

    DriverDocumentFactory.create(driver=driver, expires_on=TODAY, categories=["C"])
    assert selectors.license_covers(driver, vehicle, TODAY) is True  # expires today


def test_a_non_licence_document_never_covers(branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)
    DriverDocumentFactory.create(
        driver=driver, type=DriverDocumentType.MEDICAL, categories=[], number="M"
    )
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)

    assert selectors.valid_license(driver, TODAY) is None
    assert selectors.license_covers(driver, vehicle, TODAY) is False


# --- the company's business day ------------------------------------------------------------


@pytest.mark.parametrize(
    ("zone", "utc_now", "local_day"),
    [
        # Auckland is UTC+13 in January: 23:30 UTC on the 1st is already the 2nd there.
        ("Pacific/Auckland", datetime(2030, 1, 1, 23, 30, tzinfo=UTC), date(2030, 1, 2)),
        # Los Angeles is UTC-8: 03:00 UTC on the 2nd is still the 1st there.
        ("America/Los_Angeles", datetime(2030, 1, 2, 3, 0, tzinfo=UTC), date(2030, 1, 1)),
        ("UTC", datetime(2030, 1, 1, 23, 30, tzinfo=UTC), date(2030, 1, 1)),
    ],
)
def test_validity_follows_the_company_day_across_utc_midnight(
    zone: str, utc_now: datetime, local_day: date
) -> None:
    company = CompanyFactory.create(timezone=zone)
    driver = make_driver(BranchFactory.create(company=company), licensed=False)
    # Mocked without HTTP: patching `now` would also break JWT expiry checks.
    with mock.patch("django.utils.timezone.now", return_value=utc_now):
        today = company.local_today()
    DriverDocumentFactory.create(driver=driver, expires_on=local_day - DAY)
    assert today == local_day
    assert selectors.valid_license(driver, today) is None  # expired the company's yesterday

    DriverDocumentFactory.create(driver=driver, expires_on=local_day)
    assert selectors.valid_license(driver, today) is not None  # valid through its last day


# --- availability reasons ------------------------------------------------------------------


def _availability(client: APIClient, url: str, on_date: date = TODAY) -> dict[str, object]:
    response = client.get(f"{url}?availability_date={on_date}")
    assert response.status_code == 200, response.content
    return dict(response.json()["availability"])


def test_driver_licence_reasons(client: APIClient, branch: Branch) -> None:
    missing = make_driver(branch, licensed=False)
    expired = make_driver(branch, licensed=False)
    DriverDocumentFactory.create(driver=expired, expires_on=TODAY - DAY)
    archived_only = make_driver(branch, licensed=False)
    DriverDocumentFactory.create(driver=archived_only, archived_at=timezone.now())
    ok = make_driver(branch, licensed=False)
    DriverDocumentFactory.create(driver=ok, expires_on=TODAY)

    assert _availability(client, driver_url(missing.public_id))["reasons"] == ["license_missing"]
    assert _availability(client, driver_url(expired.public_id))["reasons"] == ["license_expired"]
    assert _availability(client, driver_url(archived_only.public_id))["reasons"] == [
        "license_missing"
    ]
    assert _availability(client, driver_url(ok.public_id)) == {
        "date": str(TODAY),
        "available": True,
        "reasons": [],
    }
    # The same licence on the next day has expired.
    assert _availability(client, driver_url(ok.public_id), TODAY + DAY)["reasons"] == [
        "license_expired"
    ]


@pytest.mark.parametrize(
    "document_type",
    [DriverDocumentType.ID_CARD, DriverDocumentType.MEDICAL, DriverDocumentType.OTHER],
)
def test_other_driver_documents_do_not_affect_availability(
    client: APIClient, branch: Branch, document_type: DriverDocumentType
) -> None:
    driver = make_driver(branch)
    DriverDocumentFactory.create(
        driver=driver, type=document_type, categories=[], expires_on=TODAY - DAY
    )

    assert _availability(client, driver_url(driver.public_id))["available"] is True


def test_vehicle_document_reasons_and_date(client: APIClient, branch: Branch) -> None:
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)
    url = vehicle_url(vehicle.public_id)
    assert _availability(client, url)["reasons"] == ["registration_missing", "insurance_missing"]

    VehicleDocumentFactory.create(vehicle=vehicle, expires_on=TODAY - DAY)
    VehicleDocumentFactory.create(
        vehicle=vehicle, type=VehicleDocumentType.INSURANCE, expires_on=TODAY
    )
    assert _availability(client, url)["reasons"] == ["registration_expired"]

    VehicleDocumentFactory.create(vehicle=vehicle, expires_on=TODAY + DAY)
    assert _availability(client, url) == {"date": str(TODAY), "available": True, "reasons": []}
    # Insurance runs out after today, registration a day later.
    assert _availability(client, url, TODAY + DAY)["reasons"] == ["insurance_expired"]
    assert _availability(client, url, TODAY + 2 * DAY)["reasons"] == [
        "registration_expired",
        "insurance_expired",
    ]


def test_vehicle_availability_date_must_be_a_date(client: APIClient, branch: Branch) -> None:
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)

    response = client.get(f"{vehicle_url(vehicle.public_id)}?availability_date=15-06-2030")

    assert response.status_code == 400


def test_vehicle_default_date_is_the_companys_today(
    client: APIClient, company: Company, branch: Branch
) -> None:
    vehicle = document_vehicle(VehicleFactory.create(company=company, branch=branch))

    availability = client.get(vehicle_url(vehicle.public_id)).json()["availability"]

    assert availability["date"] == str(company.local_today())
    assert availability["available"] is True


# --- one rule for the filter and the reasons -----------------------------------------------

DOCUMENT_STATES: dict[str, Callable[[], date | None]] = {
    "valid": lambda: TODAY,
    "expired": lambda: TODAY - DAY,
    "missing": lambda: None,
}


def test_the_driver_filter_and_reasons_agree_for_every_state(
    client: APIClient, branch: Branch
) -> None:
    for archived, login_off, suspended, licence in itertools.product(
        [False, True], [False, True], [False, True], DOCUMENT_STATES
    ):
        driver = make_driver(branch, licensed=False)
        expires_on = DOCUMENT_STATES[licence]()
        if expires_on:
            DriverDocumentFactory.create(driver=driver, expires_on=expires_on)
        if archived:
            Driver.objects.filter(pk=driver.pk).update(archived_at=timezone.now())
        if login_off:
            User.objects.filter(pk=driver.user_id).update(is_active=False)
        if suspended:
            Driver.objects.filter(pk=driver.pk).update(
                status=DriverStatus.SUSPENDED, status_reason="x"
            )

    base = f"{DRIVERS_URL}?include_archived=true&availability_date={TODAY}&page_size=100"
    rows = client.get(base).json()["results"]
    available = {r["public_id"] for r in client.get(base + "&available=true").json()["results"]}
    unavailable = {r["public_id"] for r in client.get(base + "&available=false").json()["results"]}

    assert len(rows) == 24
    assert available == {r["public_id"] for r in rows if r["availability"]["available"]}
    assert unavailable == {r["public_id"] for r in rows if not r["availability"]["available"]}
    assert len(available) == 1 and len(unavailable) == 23


def test_the_vehicle_filter_and_reasons_agree_for_every_state(
    client: APIClient, branch: Branch
) -> None:
    for archived, maintenance, registration, insurance in itertools.product(
        [False, True], [False, True], DOCUMENT_STATES, DOCUMENT_STATES
    ):
        vehicle = VehicleFactory.create(company=branch.company, branch=branch)
        for document_type, state in (
            (VehicleDocumentType.REGISTRATION, registration),
            (VehicleDocumentType.INSURANCE, insurance),
        ):
            expires_on = DOCUMENT_STATES[state]()
            if expires_on:
                VehicleDocumentFactory.create(
                    vehicle=vehicle, type=document_type, expires_on=expires_on
                )
        if archived:
            Vehicle.objects.filter(pk=vehicle.pk).update(archived_at=timezone.now())
        if maintenance:
            Vehicle.objects.filter(pk=vehicle.pk).update(
                status=VehicleStatus.IN_MAINTENANCE, status_reason="x"
            )

    base = f"{VEHICLES_URL}?include_archived=true&availability_date={TODAY}&page_size=100"
    rows = client.get(base).json()["results"]
    available = {r["public_id"] for r in client.get(base + "&available=true").json()["results"]}
    unavailable = {r["public_id"] for r in client.get(base + "&available=false").json()["results"]}

    assert len(rows) == 36
    assert available == {r["public_id"] for r in rows if r["availability"]["available"]}
    assert unavailable == {r["public_id"] for r in rows if not r["availability"]["available"]}
    assert len(available) == 1 and len(unavailable) == 35
    assert all(r["availability"]["date"] == str(TODAY) for r in rows)


def test_driver_list_query_count_does_not_grow_with_rows(client: APIClient, branch: Branch) -> None:
    make_driver(branch)
    with CaptureQueriesContext(connection) as one:
        assert client.get(DRIVERS_URL).status_code == 200
    for _ in range(9):
        make_driver(branch)

    with CaptureQueriesContext(connection) as ten:
        response = client.get(DRIVERS_URL)

    assert len(response.json()["results"]) == 10
    assert len(ten) == len(one)


def test_vehicle_list_query_count_does_not_grow_with_rows(
    client: APIClient, branch: Branch
) -> None:
    document_vehicle(VehicleFactory.create(company=branch.company, branch=branch))
    with CaptureQueriesContext(connection) as one:
        assert client.get(VEHICLES_URL).status_code == 200
    for _ in range(9):
        document_vehicle(VehicleFactory.create(company=branch.company, branch=branch))

    with CaptureQueriesContext(connection) as ten:
        response = client.get(VEHICLES_URL)

    assert len(response.json()["results"]) == 10
    assert len(ten) == len(one)


# --- expiring documents --------------------------------------------------------------------


def test_expiring_documents_cover_live_owners_in_scope(
    company: Company, branch: Branch, other_branch: Branch
) -> None:
    manager = make_user(company, RoleCode.FLEET_MANAGER, branch)
    driver = make_driver(branch, licensed=False)
    soon = DriverDocumentFactory.create(driver=driver, expires_on=TODAY)
    DriverDocumentFactory.create(driver=driver, expires_on=TODAY + 10 * DAY)  # later
    DriverDocumentFactory.create(  # archived document
        driver=driver, expires_on=TODAY - DAY, archived_at=timezone.now()
    )
    retired = make_driver(branch, licensed=False)
    DriverDocumentFactory.create(driver=retired, expires_on=TODAY)
    Driver.objects.filter(pk=retired.pk).update(archived_at=timezone.now())  # archived owner
    DriverDocumentFactory.create(driver=make_driver(other_branch, licensed=False), expires_on=TODAY)
    DriverDocumentFactory.create(  # another company
        driver=make_driver(BranchFactory.create(), licensed=False), expires_on=TODAY
    )
    vehicle = VehicleFactory.create(company=company, branch=branch)
    registration = VehicleDocumentFactory.create(vehicle=vehicle, expires_on=TODAY)

    drivers, vehicles = selectors.expiring_documents(manager, TODAY + DAY)

    assert list(drivers) == [soon]  # the boundary: expires today, asked before tomorrow
    assert list(vehicles) == [registration]
    assert list(selectors.expiring_documents(manager, TODAY)[0]) == []  # strict "before"
