"""Service-level behaviour the API tests cannot reach: lost races and stale objects."""

import threading
from datetime import date
from decimal import Decimal
from unittest import mock

import pytest
from django.db import IntegrityError, connection
from django.http import HttpRequest
from django.test import RequestFactory

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.core.errors import DomainError, ErrorCode
from apps.fleet import services
from apps.fleet.factories import DriverLeaveFactory, VehicleFactory
from apps.fleet.models import Driver, DriverLeave, LicenseCategory, Vehicle, VehicleStatus
from apps.fleet.tests.conftest import make_driver, make_user
from apps.tenancy.factories import BranchFactory, CompanyFactory
from apps.tenancy.models import Branch, Company

pytestmark = pytest.mark.django_db


def request() -> HttpRequest:
    return RequestFactory().post("/")


def vehicle_fields(branch: Branch, plate: str = "AB 12") -> services.VehicleFields:
    return services.VehicleFields(
        registration_number=plate,
        branch_id=branch.public_id,
        required_license_category=LicenseCategory.C,
        capacity_weight_kg=Decimal(1000),
        capacity_volume_m3=Decimal(10),
    )


def test_create_driver_maps_a_lost_race_to_already_exists(
    company: Company, branch: Branch, driver_user: User
) -> None:
    actor = make_user(company, RoleCode.ADMIN)
    fields = services.DriverFields(branch_id=branch.public_id, phone="+4915112345678")

    with (
        mock.patch.object(services, "_has_profile", side_effect=[False, True]),
        mock.patch.object(Driver, "save", side_effect=IntegrityError),
        pytest.raises(DomainError) as error,
    ):
        services.create_driver(
            actor=actor,
            user_id=driver_user.public_id,
            fields=fields,
            request=request(),
        )

    assert error.value.error_code == ErrorCode.DRIVER_ALREADY_EXISTS


def test_create_driver_does_not_hide_other_integrity_errors(
    company: Company, branch: Branch, driver_user: User
) -> None:
    actor = make_user(company, RoleCode.ADMIN)
    fields = services.DriverFields(branch_id=branch.public_id, phone="+4915112345678")

    with (
        mock.patch.object(Driver, "save", side_effect=IntegrityError),
        pytest.raises(IntegrityError),
    ):
        services.create_driver(
            actor=actor,
            user_id=driver_user.public_id,
            fields=fields,
            request=request(),
        )


def test_add_leave_maps_a_lost_race_to_overlap(company: Company, branch: Branch) -> None:
    actor = make_user(company, RoleCode.ADMIN)
    driver = make_driver(branch)

    with (
        mock.patch.object(services, "_overlapping_leave", side_effect=[False, True]),
        mock.patch.object(DriverLeave, "save", side_effect=IntegrityError),
        pytest.raises(DomainError) as error,
    ):
        services.add_leave(
            actor=actor,
            driver=driver,
            starts_on=date(2030, 1, 1),
            ends_on=date(2030, 1, 2),
            reason="Holiday",
            request=request(),
        )

    assert error.value.error_code == ErrorCode.DRIVER_LEAVE_OVERLAP


def test_add_leave_does_not_hide_other_integrity_errors(company: Company, branch: Branch) -> None:
    actor = make_user(company, RoleCode.ADMIN)

    with (
        mock.patch.object(DriverLeave, "save", side_effect=IntegrityError),
        pytest.raises(IntegrityError),
    ):
        services.add_leave(
            actor=actor,
            driver=make_driver(branch),
            starts_on=date(2030, 1, 1),
            ends_on=date(2030, 1, 2),
            reason="Holiday",
            request=request(),
        )


def test_delete_leave_of_a_leave_that_is_already_gone_is_404(
    company: Company, branch: Branch
) -> None:
    actor = make_user(company, RoleCode.ADMIN)
    leave = DriverLeaveFactory.create(driver=make_driver(branch))
    stale = DriverLeave.objects.get(pk=leave.pk)
    leave.delete()

    with pytest.raises(DomainError) as error:
        services.delete_leave(actor=actor, driver=stale.driver, leave=stale, request=request())

    assert error.value.status_code == 404


def test_vehicle_duplicate_registration_race_maps_to_409(company: Company, branch: Branch) -> None:
    actor = make_user(company, RoleCode.ADMIN)
    duplicate = DomainError(ErrorCode.VEHICLE_DUPLICATE_REGISTRATION)

    with (
        mock.patch.object(services, "_check_registration_free", side_effect=[None, duplicate]),
        mock.patch.object(Vehicle, "save", side_effect=IntegrityError),
        pytest.raises(DomainError) as error,
    ):
        services.create_vehicle(actor=actor, fields=vehicle_fields(branch), request=request())

    assert error.value.error_code == ErrorCode.VEHICLE_DUPLICATE_REGISTRATION


def test_vehicle_other_integrity_errors_are_not_hidden(company: Company, branch: Branch) -> None:
    actor = make_user(company, RoleCode.ADMIN)

    with (
        mock.patch.object(Vehicle, "save", side_effect=IntegrityError),
        pytest.raises(IntegrityError),
    ):
        services.create_vehicle(actor=actor, fields=vehicle_fields(branch), request=request())


def test_vehicle_services_refuse_a_vehicle_outside_the_actors_branches(
    company: Company, branch: Branch, other_branch: Branch
) -> None:
    narrow = make_user(company, RoleCode.FLEET_MANAGER, branch)
    vehicle = VehicleFactory.create(company=company, branch=other_branch)

    with pytest.raises(DomainError) as archive_error:
        services.archive_vehicle(actor=narrow, vehicle=vehicle, request=request())
    with pytest.raises(DomainError) as update_error:
        services.update_vehicle(
            actor=narrow, vehicle=vehicle, fields=services.VehicleFields.of(vehicle)
        )
    assert archive_error.value.status_code == update_error.value.status_code == 404
    vehicle.refresh_from_db()
    assert vehicle.archived_at is None


def test_vehicle_status_reason_can_change_without_a_status_change(
    company: Company, branch: Branch
) -> None:
    actor = make_user(company, RoleCode.ADMIN)
    vehicle = VehicleFactory.create(
        company=company, branch=branch, status=VehicleStatus.IN_MAINTENANCE, status_reason="old"
    )

    updated = services.set_vehicle_status(
        actor=actor,
        vehicle=vehicle,
        status=VehicleStatus.IN_MAINTENANCE,
        reason="new",
        request=request(),
    )

    assert updated.status_reason == "new"


def test_services_cannot_be_used_across_companies(branch: Branch) -> None:
    outsider = UserFactory.create(roles=[RoleCode.OWNER])
    driver = make_driver(branch)

    with pytest.raises(DomainError) as error:
        services.archive_driver(actor=outsider, driver=driver, request=request())

    assert error.value.status_code == 404
    driver.refresh_from_db()
    assert driver.archived_at is None


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_profiles_for_one_user_at_once_have_exactly_one_winner() -> None:
    company = CompanyFactory.create()
    branch = BranchFactory.create(company=company)
    actor = UserFactory.create(company=company, roles=[RoleCode.OWNER])
    user = UserFactory.create(company=company, roles=[RoleCode.DRIVER])
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def create() -> None:
        barrier.wait()
        try:
            services.create_driver(
                actor=actor,
                user_id=user.public_id,
                fields=services.DriverFields(branch_id=branch.public_id, phone="+4915112345678"),
                request=RequestFactory().post("/"),
            )
            outcomes.append("ok")
        except DomainError as exc:
            outcomes.append(str(exc.error_code))
        finally:
            connection.close()

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["driver.already_exists", "ok"]
    assert Driver.objects.filter(user=user).count() == 1


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_vehicles_with_one_plate_at_once_have_exactly_one_winner() -> None:
    company = CompanyFactory.create()
    branch = BranchFactory.create(company=company)
    actor = UserFactory.create(company=company, roles=[RoleCode.OWNER])
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def create() -> None:
        barrier.wait()
        try:
            services.create_vehicle(
                actor=actor,
                fields=vehicle_fields(branch, "RACE 1"),
                request=RequestFactory().post("/"),
            )
            outcomes.append("ok")
        except DomainError as exc:
            outcomes.append(str(exc.error_code))
        finally:
            connection.close()

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["ok", "vehicle.duplicate_registration"]
    assert Vehicle.objects.filter(company=company).count() == 1
