"""Driver and vehicle documents over HTTP: types and rules, attaching uploads, number
visibility, list filters, archive and the download URL. Uploads go to the real object
store, as in the uploads tests."""

import uuid
from datetime import date, timedelta
from typing import Any
from unittest import mock

import pytest
import urllib3
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.accounts.tests.conftest import client_for
from apps.audit.models import AuditAction, AuditLog
from apps.core import storage
from apps.core.errors import DomainError
from apps.fleet import selectors, services
from apps.fleet.factories import (
    DriverDocumentFactory,
    DriverFactory,
    VehicleDocumentFactory,
    VehicleFactory,
)
from apps.fleet.models import DriverDocument, VehicleDocument, VehicleDocumentType
from apps.fleet.tests.conftest import (
    confirmed_upload,
    driver_url,
    make_driver,
    make_user,
    new_key,
    post,
    vehicle_url,
)
from apps.tenancy.factories import BranchFactory, CompanyFactory
from apps.tenancy.models import Branch, Company
from apps.uploads.factories import UploadFactory
from apps.uploads.models import Upload
from apps.uploads.tests.conftest import make_jpeg

pytestmark = pytest.mark.django_db

SECRET_NUMBER = "DL-SECRET-4711"


def licence_body(upload_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "type": "license",
        "number": SECRET_NUMBER,
        "categories": ["C", "B"],
        "issued_on": "2020-01-01",
        "expires_on": "2099-01-01",
        "upload_id": upload_id,
    } | overrides


def registration_body(upload_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "type": "registration",
        "number": "REG-1",
        "expires_on": "2099-01-01",
        "upload_id": upload_id,
    } | overrides


def docs_url(owner: Any, suffix: str = "", *, vehicle: bool = False) -> str:
    base = vehicle_url if vehicle else driver_url
    return base(owner.public_id, f"documents/{suffix}")


def add_licence(client: APIClient, driver: Any, **overrides: Any) -> Any:
    body = licence_body(confirmed_upload(client), **overrides)
    return post(client, docs_url(driver), body, **{"Idempotency-Key": new_key()})


def add_registration(client: APIClient, vehicle: Any, **overrides: Any) -> Any:
    body = registration_body(confirmed_upload(client), **overrides)
    return post(client, docs_url(vehicle, vehicle=True), body, **{"Idempotency-Key": new_key()})


def code(response: Any) -> str:
    return str(response.json()["code"])


# --- add -----------------------------------------------------------------------------------


def test_add_a_licence_makes_the_driver_available(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)
    upload_id = confirmed_upload(client)

    response = post(
        client,
        docs_url(driver),
        licence_body(upload_id),
        **{"Idempotency-Key": new_key()},
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["type"] == "license"
    assert body["number"] == SECRET_NUMBER  # the manager sees it
    assert body["categories"] == ["B", "C"]  # de-duplicated, in enum order
    assert (body["issued_on"], body["expires_on"]) == ("2020-01-01", "2099-01-01")
    assert body["is_expired"] is False
    assert body["archived_at"] is None
    assert "upload" not in body  # no storage details leave the API
    document = DriverDocument.objects.get(public_id=body["public_id"])
    assert document.upload.public_id == uuid.UUID(upload_id)
    assert document.upload.attached_at is not None
    driver_body = client.get(driver_url(driver.public_id)).json()
    assert driver_body["availability"]["available"] is True


def test_adding_is_audited_without_the_number(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)

    response = add_licence(client, driver)

    entry = AuditLog.objects.get(action=AuditAction.FLEET_DOCUMENT_ADDED)
    assert entry.object_id == response.json()["public_id"]
    assert entry.after == {"type": "license"}
    assert SECRET_NUMBER not in str(entry.__dict__)


def test_add_a_vehicle_document(client: APIClient, branch: Branch) -> None:
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)

    response = add_registration(client, vehicle)

    assert response.status_code == 201, response.content
    assert response.json()["type"] == "registration"
    assert "categories" not in response.json()
    assert VehicleDocument.objects.get().vehicle == vehicle


def test_replaying_an_add_creates_one_document(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)
    body = licence_body(confirmed_upload(client))
    key = {"Idempotency-Key": new_key()}

    first = post(client, docs_url(driver), body, **key)
    second = post(client, docs_url(driver), body, **key)

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json() == second.json()
    assert DriverDocument.objects.count() == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"expires_on": None},  # a licence must expire
        {"expires_on": "2019-12-31"},  # before it was issued
        {"categories": []},  # a licence lists its categories
        {"categories": ["Z"]},  # not a Vienna category
        {"categories": "C"},
        {"type": "medical", "categories": [], "expires_on": None},
        {"type": "id_card", "categories": ["C"]},  # only a licence has categories
        {"type": "passport"},
        {"expires_on": "31-12-2099"},
        {"number": "x" * 51},
    ],
)
def test_invalid_driver_documents_are_400(
    client: APIClient, branch: Branch, overrides: dict[str, Any]
) -> None:
    driver = make_driver(branch, licensed=False)

    response = post(client, docs_url(driver), licence_body(confirmed_upload(client), **overrides))

    assert (response.status_code, code(response)) == (400, "validation.invalid")
    assert not DriverDocument.objects.exists()
    assert Upload.objects.get().attached_at is None  # the upload stays free to use


@pytest.mark.parametrize(
    "overrides",
    [
        {"expires_on": None},
        {"type": "insurance", "expires_on": None},
        {"type": "pink_slip"},
        {"issued_on": "2100-01-01"},
    ],
)
def test_invalid_vehicle_documents_are_400(
    client: APIClient, branch: Branch, overrides: dict[str, Any]
) -> None:
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)

    response = post(
        client,
        docs_url(vehicle, vehicle=True),
        registration_body(confirmed_upload(client), **({"issued_on": "2020-01-01"} | overrides)),
    )

    assert (response.status_code, code(response)) == (400, "validation.invalid")


def test_documents_without_an_expiry_are_allowed_where_expiry_is_optional(
    client: APIClient, branch: Branch
) -> None:
    driver = make_driver(branch)
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)

    other = post(
        client,
        docs_url(driver),
        {"type": "other", "upload_id": confirmed_upload(client)},
    )
    inspection = post(
        client,
        docs_url(vehicle, vehicle=True),
        {"type": "inspection", "upload_id": confirmed_upload(client)},
    )

    assert (other.status_code, inspection.status_code) == (201, 201)
    assert other.json()["expires_on"] is None
    assert other.json()["is_expired"] is False  # no expiry, never expired


def test_the_database_enforces_the_type_rules(branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)
    bad: list[tuple[Any, dict[str, Any]]] = [
        (DriverDocumentFactory, {"driver": driver, "expires_on": None}),
        (DriverDocumentFactory, {"driver": driver, "categories": []}),
        (DriverDocumentFactory, {"driver": driver, "categories": ["Z"]}),
        (DriverDocumentFactory, {"driver": driver, "type": "id_card"}),  # categories on non-licence
        (DriverDocumentFactory, {"driver": driver, "type": "passport"}),
        (
            DriverDocumentFactory,
            {"driver": driver, "issued_on": date(2100, 1, 1), "expires_on": date(2099, 1, 1)},
        ),
        (VehicleDocumentFactory, {"vehicle": vehicle, "expires_on": None}),
        (
            VehicleDocumentFactory,
            {"vehicle": vehicle, "type": VehicleDocumentType.INSURANCE, "expires_on": None},
        ),
        (VehicleDocumentFactory, {"vehicle": vehicle, "type": "passport"}),
        (
            VehicleDocumentFactory,
            {"vehicle": vehicle, "issued_on": date(2100, 1, 1), "expires_on": date(2099, 1, 1)},
        ),
    ]
    for factory_class, fields in bad:
        with pytest.raises(IntegrityError), transaction.atomic():
            factory_class.create(**fields)


def test_one_upload_backs_one_document_at_the_database_level(branch: Branch) -> None:
    first = DriverDocumentFactory.create(driver=make_driver(branch, licensed=False))

    with pytest.raises(IntegrityError), transaction.atomic():
        DriverDocumentFactory.create(driver=first.driver, upload=first.upload)


# --- attaching the upload ------------------------------------------------------------------


def test_another_users_upload_is_a_missing_reference(
    client: APIClient, company: Company, branch: Branch
) -> None:
    driver = make_driver(branch, licensed=False)
    colleague = client_for(make_user(company, RoleCode.FLEET_MANAGER, branch))
    upload_id = confirmed_upload(colleague)

    response = post(client, docs_url(driver), licence_body(upload_id))

    assert (response.status_code, code(response)) == (400, "validation.invalid_reference")
    assert "upload_id" in response.json()["errors"]
    assert not DriverDocument.objects.exists()
    assert Upload.objects.get().attached_at is None


def test_another_companys_upload_is_a_missing_reference(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)
    foreign = UploadFactory.create()

    response = post(client, docs_url(driver), licence_body(str(foreign.public_id)))

    assert (response.status_code, code(response)) == (400, "validation.invalid_reference")
    assert str(foreign.public_id) not in response.content.decode()


def test_an_unknown_upload_is_a_missing_reference(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)

    response = post(client, docs_url(driver), licence_body(str(uuid.uuid4())))

    assert (response.status_code, code(response)) == (400, "validation.invalid_reference")


def test_an_unconfirmed_upload_is_409(
    client: APIClient, fleet_manager: User, branch: Branch
) -> None:
    driver = make_driver(branch, licensed=False)
    pending = UploadFactory.create(company=fleet_manager.company, uploaded_by=fleet_manager)

    response = post(client, docs_url(driver), licence_body(str(pending.public_id)))

    assert (response.status_code, code(response)) == (409, "upload.not_confirmed")
    assert not DriverDocument.objects.exists()


def test_an_upload_can_back_only_one_document(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)
    upload_id = confirmed_upload(client)
    first = post(client, docs_url(driver), licence_body(upload_id))

    again = post(client, docs_url(driver), licence_body(upload_id))

    assert first.status_code == 201
    assert (again.status_code, code(again)) == (409, "upload.in_use")
    assert DriverDocument.objects.count() == 1


def test_a_file_swapped_after_confirmation_is_refused(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)
    upload_id = confirmed_upload(client)
    upload = Upload.objects.get(public_id=upload_id)
    storage.put(upload.object_key, make_jpeg(extra_bytes=50), content_type="image/jpeg")

    response = post(client, docs_url(driver), licence_body(upload_id))

    assert (response.status_code, code(response)) == (409, "upload.not_confirmed")


def test_a_failed_save_leaves_the_upload_unattached(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)
    upload_id = confirmed_upload(client)

    with mock.patch.object(DriverDocument, "save", side_effect=RuntimeError("disk")):
        response = post(client, docs_url(driver), licence_body(upload_id))

    assert response.status_code == 500
    assert Upload.objects.get(public_id=upload_id).attached_at is None
    assert not DriverDocument.objects.exists()


def test_an_archived_owner_takes_no_documents(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch)
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)
    post(client, driver_url(driver.public_id, "archive/"))
    post(client, vehicle_url(vehicle.public_id, "archive/"))
    attached_before = Upload.objects.filter(attached_at__isnull=False).count()

    on_driver = add_licence(client, driver)
    on_vehicle = add_registration(client, vehicle)

    assert (on_driver.status_code, code(on_driver)) == (409, "driver.archived")
    assert (on_vehicle.status_code, code(on_vehicle)) == (409, "vehicle.archived")
    assert Upload.objects.filter(attached_at__isnull=False).count() == attached_before


# --- list and detail -----------------------------------------------------------------------


def test_list_filters(client: APIClient, company: Company, branch: Branch) -> None:
    today = company.local_today()
    driver = make_driver(branch, licensed=False)
    lic = DriverDocumentFactory.create(driver=driver, expires_on=today + timedelta(days=30))
    medical = DriverDocumentFactory.create(
        driver=driver,
        type="medical",
        categories=[],
        expires_on=today - timedelta(days=1),
    )
    old = DriverDocumentFactory.create(driver=driver, archived_at=timezone.now())
    DriverDocumentFactory.create()  # another company's driver

    def ids(query: str = "") -> set[str]:
        response = client.get(docs_url(driver) + query)
        assert response.status_code == 200, response.content
        return {d["public_id"] for d in response.json()["results"]}

    assert ids() == {str(lic.public_id), str(medical.public_id)}  # archived hidden
    assert ids("?include_archived=true") == {str(d.public_id) for d in (lic, medical, old)}
    assert ids("?type=medical") == {str(medical.public_id)}
    assert ids(f"?expires_before={today}") == {str(medical.public_id)}
    assert ids(f"?expires_before={today - timedelta(days=1)}") == set()  # strict
    assert ids(f"?expires_before={today + timedelta(days=31)}") == {
        str(lic.public_id),
        str(medical.public_id),
    }
    assert client.get(docs_url(driver) + "?type=passport").status_code == 400
    assert client.get(docs_url(driver) + "?expires_before=soon").status_code == 400


def test_every_document_carries_is_expired(
    client: APIClient, company: Company, branch: Branch
) -> None:
    today = company.local_today()
    driver = make_driver(branch, licensed=False)
    for offset in (-1, 0, 1):
        DriverDocumentFactory.create(driver=driver, expires_on=today + timedelta(days=offset))

    rows = client.get(docs_url(driver)).json()["results"]

    assert {r["expires_on"]: r["is_expired"] for r in rows} == {
        str(today - timedelta(days=1)): True,
        str(today): False,  # valid through its last day
        str(today + timedelta(days=1)): False,
    }


def test_vehicle_list_and_detail(client: APIClient, company: Company, branch: Branch) -> None:
    vehicle = VehicleFactory.create(company=company, branch=branch)
    registration = VehicleDocumentFactory.create(vehicle=vehicle)
    VehicleDocumentFactory.create(
        vehicle=vehicle, type=VehicleDocumentType.INSURANCE, expires_on=date(2020, 1, 1)
    )

    listed = client.get(docs_url(vehicle, vehicle=True) + "?type=registration").json()["results"]
    detail = client.get(docs_url(vehicle, f"{registration.public_id}/", vehicle=True))

    assert [r["public_id"] for r in listed] == [str(registration.public_id)]
    assert detail.status_code == 200
    assert detail.json()["number"] == "REG-0001"
    expired = client.get(docs_url(vehicle, vehicle=True) + "?type=insurance").json()["results"]
    assert expired[0]["is_expired"] is True


@pytest.mark.parametrize(
    ("role", "sees_number"),
    [
        (RoleCode.OWNER, True),
        (RoleCode.ADMIN, True),
        (RoleCode.OPS_MANAGER, True),
        (RoleCode.FLEET_MANAGER, True),
        (RoleCode.DISPATCHER, False),
        (RoleCode.FINANCE, False),
        (RoleCode.VIEWER, False),
    ],
)
def test_the_number_is_shown_only_to_fleet_managers(
    role: RoleCode, sees_number: bool, company: Company, branch: Branch
) -> None:
    client = client_for(make_user(company, role, branch))
    driver = make_driver(branch, licensed=False)
    document = DriverDocumentFactory.create(driver=driver, number=SECRET_NUMBER)

    listed = client.get(docs_url(driver)).json()["results"][0]
    detail = client.get(docs_url(driver, f"{document.public_id}/")).json()

    for row in (listed, detail):
        assert row["number"] == (SECRET_NUMBER if sees_number else None)
        assert row["type"] == "license"  # the rest stays visible to dispatchers
        assert row["categories"] == ["B", "C"]
        assert SECRET_NUMBER in str(row) or not sees_number


def test_a_vehicle_document_number_is_hidden_from_dispatchers(
    company: Company, branch: Branch
) -> None:
    client = client_for(make_user(company, RoleCode.DISPATCHER, branch))
    vehicle = VehicleFactory.create(company=company, branch=branch)
    VehicleDocumentFactory.create(vehicle=vehicle, number="PLATE-SECRET")

    response = client.get(docs_url(vehicle, vehicle=True))

    assert response.json()["results"][0]["number"] is None
    assert "PLATE-SECRET" not in response.content.decode()


# --- archive -------------------------------------------------------------------------------


def test_archive_ends_the_documents_validity_and_is_idempotent(
    client: APIClient, branch: Branch
) -> None:
    driver = make_driver(branch, licensed=False)
    document = DriverDocumentFactory.create(driver=driver)
    url = docs_url(driver, f"{document.public_id}/archive/")
    assert client.get(driver_url(driver.public_id)).json()["availability"]["available"] is True

    first, second = post(client, url), post(client, url)

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json()["archived_at"] == second.json()["archived_at"] is not None
    availability = client.get(driver_url(driver.public_id)).json()["availability"]
    assert availability["reasons"] == ["license_missing"]
    assert AuditLog.objects.filter(action=AuditAction.FLEET_DOCUMENT_ARCHIVED).count() == 1
    assert client.get(docs_url(driver)).json()["results"] == []  # hidden by default


def test_archive_a_vehicle_document(client: APIClient, branch: Branch) -> None:
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)
    document = VehicleDocumentFactory.create(vehicle=vehicle)

    response = post(client, docs_url(vehicle, f"{document.public_id}/archive/", vehicle=True))

    assert response.status_code == 200
    document.refresh_from_db()
    assert document.archived_at is not None


# --- download ------------------------------------------------------------------------------


def test_download_returns_a_short_lived_url_to_the_file(
    client: APIClient, branch: Branch, settings: Any
) -> None:
    driver = make_driver(branch, licensed=False)
    scan = make_jpeg(extra_bytes=20)
    created = post(
        client,
        docs_url(driver),
        licence_body(confirmed_upload(client, scan)),
    )
    document_url = docs_url(driver, f"{created.json()['public_id']}/download/")

    response = post(client, document_url)

    assert response.status_code == 200, response.content
    body = response.json()
    assert set(body) == {"url", "expires_at"}
    fetched = urllib3.PoolManager().request("GET", body["url"])
    assert fetched.status == 200
    assert fetched.data == scan
    assert "attachment" in fetched.headers["Content-Disposition"]
    assert f"X-Amz-Expires={settings.UPLOAD_URL_TTL_SECONDS}" in body["url"]
    entry = AuditLog.objects.get(action=AuditAction.FLEET_DOCUMENT_DOWNLOADED)
    assert entry.object_id == created.json()["public_id"]
    assert SECRET_NUMBER not in str(entry.__dict__)


def test_download_a_vehicle_document(client: APIClient, branch: Branch) -> None:
    vehicle = VehicleFactory.create(company=branch.company, branch=branch)
    created = add_registration(client, vehicle)

    response = post(
        client, docs_url(vehicle, f"{created.json()['public_id']}/download/", vehicle=True)
    )

    assert response.status_code == 200
    assert urllib3.PoolManager().request("GET", response.json()["url"]).status == 200


@pytest.mark.parametrize("role", [RoleCode.DISPATCHER, RoleCode.FINANCE, RoleCode.VIEWER])
def test_fleet_view_alone_cannot_download(role: RoleCode, company: Company, branch: Branch) -> None:
    client = client_for(make_user(company, role, branch))
    document = DriverDocumentFactory.create(driver=make_driver(branch, licensed=False))

    response = post(client, docs_url(document.driver, f"{document.public_id}/download/"))

    assert response.status_code == 403
    assert not AuditLog.objects.filter(action=AuditAction.FLEET_DOCUMENT_DOWNLOADED).exists()


def test_download_after_the_file_was_swapped_is_409(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)
    created = add_licence(client, driver)
    upload = DriverDocument.objects.get().upload
    storage.put(upload.object_key, make_jpeg(extra_bytes=99), content_type="image/jpeg")

    response = post(client, docs_url(driver, f"{created.json()['public_id']}/download/"))

    assert (response.status_code, code(response)) == (409, "upload.not_confirmed")
    assert not AuditLog.objects.filter(action=AuditAction.FLEET_DOCUMENT_DOWNLOADED).exists()


def test_archived_documents_can_still_be_downloaded(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)
    created = add_licence(client, driver)
    path = f"{created.json()['public_id']}/"
    post(client, docs_url(driver, path + "archive/"))

    assert post(client, docs_url(driver, path + "download/")).status_code == 200


# --- access --------------------------------------------------------------------------------

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
def test_role_matrix(role: RoleCode, company: Company, branch: Branch) -> None:
    client = client_for(make_user(company, role, branch))
    driver = make_driver(branch, licensed=False)
    # A document whose file really is in the bucket, so a permitted download succeeds.
    writer = client_for(make_user(company, RoleCode.FLEET_MANAGER, branch))
    one = f"{add_licence(writer, driver).json()['public_id']}/"
    upload_id = confirmed_upload(client) if role in WRITE_ROLES else str(uuid.uuid4())

    listed = client.get(docs_url(driver))
    detail = client.get(docs_url(driver, one))
    added = post(client, docs_url(driver), licence_body(upload_id))
    archived = post(client, docs_url(driver, one + "archive/"))
    downloaded = post(client, docs_url(driver, one + "download/"))

    assert listed.status_code == (200 if role in READ_ROLES else 403)
    assert detail.status_code == (200 if role in READ_ROLES else 403)
    assert added.status_code == (201 if role in WRITE_ROLES else 403)
    assert archived.status_code == (200 if role in WRITE_ROLES else 403)
    assert downloaded.status_code == (200 if role in WRITE_ROLES else 403)


def test_unauthenticated_is_401(branch: Branch) -> None:
    document = DriverDocumentFactory.create(driver=make_driver(branch, licensed=False))
    anonymous = APIClient()
    base = docs_url(document.driver)

    assert anonymous.get(base).status_code == 401
    assert anonymous.post(base, {}, format="json").status_code == 401
    assert anonymous.get(base + f"{document.public_id}/").status_code == 401
    assert anonymous.post(base + f"{document.public_id}/download/").status_code == 401


def test_unknown_ids_are_404(client: APIClient, branch: Branch) -> None:
    driver = make_driver(branch, licensed=False)
    document = DriverDocumentFactory.create(driver=driver)
    missing = uuid.uuid4()

    assert client.get(driver_url(missing, "documents/")).status_code == 404
    assert post(client, driver_url(missing, "documents/"), {}).status_code == 404
    assert client.get(docs_url(driver, f"{missing}/")).status_code == 404
    assert post(client, docs_url(driver, f"{missing}/archive/")).status_code == 404
    assert post(client, docs_url(driver, f"{missing}/download/")).status_code == 404
    other_driver = make_driver(branch, licensed=False)  # the document of a different driver
    assert client.get(docs_url(other_driver, f"{document.public_id}/")).status_code == 404


def test_the_owner_is_found_before_the_body_is_read(client: APIClient) -> None:
    foreign = DriverFactory.create()

    response = post(client, docs_url(foreign), {"nonsense": True})

    assert response.status_code == 404


def test_other_branches_documents_are_404(
    client: APIClient, company: Company, other_branch: Branch
) -> None:
    far_driver = make_driver(other_branch, licensed=False)
    far_vehicle = VehicleFactory.create(company=company, branch=other_branch)
    driver_doc = DriverDocumentFactory.create(driver=far_driver)
    vehicle_doc = VehicleDocumentFactory.create(vehicle=far_vehicle)

    assert client.get(docs_url(far_driver)).status_code == 404
    assert client.get(docs_url(far_vehicle, vehicle=True)).status_code == 404
    assert (
        post(client, docs_url(far_driver, f"{driver_doc.public_id}/download/")).status_code == 404
    )
    assert (
        post(
            client, docs_url(far_vehicle, f"{vehicle_doc.public_id}/archive/", vehicle=True)
        ).status_code
        == 404
    )
    admin = client_for(make_user(company, RoleCode.ADMIN))  # all branches
    assert admin.get(docs_url(far_driver)).status_code == 200


def test_services_re_check_scope_even_when_the_view_did_not(
    company: Company, branch: Branch, other_branch: Branch
) -> None:
    local = make_user(company, RoleCode.FLEET_MANAGER, branch)
    foreign_manager = UserFactory.create(
        company=CompanyFactory.create(), roles=[RoleCode.FLEET_MANAGER]
    )
    far_driver = make_driver(other_branch, licensed=False)
    document = DriverDocumentFactory.create(driver=far_driver)
    request = mock.Mock()

    for actor in (local, foreign_manager):
        with pytest.raises(DomainError) as error:
            services.archive_document(actor=actor, document=document, request=request)
        assert error.value.status_code == 404
        with pytest.raises(DomainError) as error:
            services.download_document(actor=actor, document=document, request=request)
        assert error.value.status_code == 404
        with pytest.raises(DomainError) as error:
            services.add_driver_document(
                actor=actor,
                driver=far_driver,
                fields=services.DocumentFields(type="other", upload_id=uuid.uuid4()),
                request=request,
            )
        assert error.value.status_code == 404
    document.refresh_from_db()
    assert document.archived_at is None


def test_another_branches_vehicle_is_404_for_services_too(
    company: Company, branch: Branch, other_branch: Branch
) -> None:
    local = make_user(company, RoleCode.FLEET_MANAGER, branch)
    far_vehicle = VehicleFactory.create(company=company, branch=other_branch)
    document = VehicleDocumentFactory.create(vehicle=far_vehicle)

    with pytest.raises(DomainError) as error:
        services.archive_document(actor=local, document=document, request=mock.Mock())

    assert error.value.status_code == 404
    with pytest.raises(DomainError) as error:
        services.add_vehicle_document(
            actor=local,
            vehicle=far_vehicle,
            fields=services.DocumentFields(type="other", upload_id=uuid.uuid4()),
            request=mock.Mock(),
        )
    assert error.value.status_code == 404


def test_expiring_documents_ignore_other_companies(branch: Branch) -> None:
    manager = make_user(branch.company, RoleCode.FLEET_MANAGER, branch)
    DriverDocumentFactory.create(driver=make_driver(BranchFactory.create(), licensed=False))

    assert list(selectors.expiring_documents(manager, date(2100, 1, 1))[0]) == []
